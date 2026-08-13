"""Committee orchestration.

Runs the depth-appropriate pipeline over the user's own Anthropic credentials, aggregates token
usage, and re-checks the synthesized report against the deterministic guardrails.

Failure policy: a single advisor call failing degrades the committee rather than killing the run.
A failed synthesis is fatal, because there is no report without it.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

from app.advisors.registry import AdvisorRegistry
from app.advisors.selection import CommitteeSelection
from app.analytics.guardrails import verify_report_against_guardrails
from app.analytics.portfolio_analytics import PortfolioAnalytics
from app.analytics.profile_analytics import ProfileAnalytics
from app.committee import prompts
from app.committee.schemas import (
    ANALYSIS_SCHEMA,
    CRITIQUE_SCHEMA,
    RISK_CHALLENGE_SCHEMA,
    SYNTHESIS_SCHEMA,
)
from app.core.run_context import RunContext
from app.domain.advisor import AdvisorRuntimeProfile
from app.domain.profile import FinancialProfile
from app.domain.report import (
    AdvisorAnalysis,
    CommitteeReport,
    Critique,
    Guardrail,
    RiskChallenge,
)
from app.llm.provider import LLMProvider, Message


class CommitteeError(RuntimeError):
    """Raised when the run cannot produce a report at all."""


class CommitteeOrchestrator:
    def __init__(self, provider: LLMProvider, registry: AdvisorRegistry) -> None:
        self._provider = provider
        self._registry = registry

    async def run(
        self,
        *,
        profile: FinancialProfile,
        analytics: ProfileAnalytics,
        portfolio_analytics: PortfolioAnalytics | None,
        guardrails: list[Guardrail],
        selection: CommitteeSelection,
        question: str,
        context: RunContext,
    ) -> CommitteeReport:
        if not selection.selected:
            raise CommitteeError("No advisors were selected; nothing to run.")

        depth = context.depth
        advisors = [self._registry.runtime_profile(s.advisor_id) for s in selection.selected]
        facts = prompts.render_profile_facts(profile, analytics, portfolio_analytics, guardrails)

        analyses = await self._independent_round(advisors, facts, question, context)
        usable = [a for a in analyses if a.thesis]
        if not usable:
            raise CommitteeError(
                "Every advisor call failed. No analysis was produced; you have not been charged "
                "for a usable report."
            )

        critiques: list[Critique] = []
        revised: list[AdvisorAnalysis] = []
        risk: RiskChallenge | None = None

        if "cross_examination" in depth.stages:
            critiques = await self._cross_examination_round(
                advisors, usable, facts, question, context
            )

        if "revised_memo" in depth.stages:
            revised = await self._revised_round(
                advisors, usable, critiques, facts, question, context
            )

        if "risk_challenge" in depth.stages:
            risk = await self._risk_challenge(usable, facts, question, context)

        final_positions = revised or usable
        report = await self._synthesize(
            final_positions, critiques, risk, facts, question, guardrails, context
        )

        report.analyses = analyses
        report.revised_analyses = revised
        report.critiques = critiques
        report.risk_challenge = risk
        report.guardrails = guardrails

        # Post-hoc check: did the synthesized text sail past a blocking guardrail?
        report_text = "\n".join([report.summary, *report.consensus, *report.recommended_actions])
        report.guardrail_violations = verify_report_against_guardrails(report_text, guardrails)
        return report

    # --- stages -----------------------------------------------------------------------

    async def _independent_round(
        self,
        advisors: list[AdvisorRuntimeProfile],
        facts: str,
        question: str,
        context: RunContext,
    ) -> list[AdvisorAnalysis]:
        async def one(advisor: AdvisorRuntimeProfile) -> AdvisorAnalysis:
            stable, user = prompts.independent_prompt(advisor, question)
            resp = await self._provider.generate(
                [Message(role="user", content=user)],
                context,
                system=facts,
                stable_system=stable,
                role="independent",
                advisor_id=advisor.advisor_id,
                schema=ANALYSIS_SCHEMA,
            )
            return _analysis_from(
                resp.parsed, advisor, resp_failed=not resp.ok, refused=resp.refused
            )

        return list(await asyncio.gather(*(one(a) for a in advisors)))

    async def _cross_examination_round(
        self,
        advisors: list[AdvisorRuntimeProfile],
        analyses: list[AdvisorAnalysis],
        facts: str,
        question: str,
        context: RunContext,
    ) -> list[Critique]:
        by_id = {a.advisor_id: a for a in analyses}

        async def one(advisor: AdvisorRuntimeProfile) -> list[Critique]:
            own = by_id.get(advisor.advisor_id)
            if own is None:
                return []
            others = [a for a in analyses if a.advisor_id != advisor.advisor_id]
            if not others:
                return []
            stable, user = prompts.cross_examination_prompt(advisor, own, others, question)
            resp = await self._provider.generate(
                [Message(role="user", content=user)],
                context,
                system=facts,
                stable_system=stable,
                role="cross_examination",
                advisor_id=advisor.advisor_id,
                schema=CRITIQUE_SCHEMA,
            )
            if not resp.ok or not resp.parsed:
                return []
            p = resp.parsed
            # One critique record per peer; the model critiques the round collectively.
            return [
                Critique(
                    from_advisor_id=advisor.advisor_id,
                    target_advisor_id=o.advisor_id,
                    agreement=str(p.get("agreement", "")),
                    disagreement=str(p.get("disagreement", "")),
                    strength=_clamp01(p.get("strength", 0.5)),
                )
                for o in others
            ]

        nested = await asyncio.gather(*(one(a) for a in advisors))
        return [c for group in nested for c in group]

    async def _revised_round(
        self,
        advisors: list[AdvisorRuntimeProfile],
        analyses: list[AdvisorAnalysis],
        critiques: list[Critique],
        facts: str,
        question: str,
        context: RunContext,
    ) -> list[AdvisorAnalysis]:
        by_id = {a.advisor_id: a for a in analyses}

        async def one(advisor: AdvisorRuntimeProfile) -> AdvisorAnalysis | None:
            own = by_id.get(advisor.advisor_id)
            if own is None:
                return None
            against_me = [
                c.disagreement
                for c in critiques
                if c.target_advisor_id == advisor.advisor_id and c.disagreement
            ]
            stable, user = prompts.revised_memo_prompt(advisor, own, against_me, question)
            resp = await self._provider.generate(
                [Message(role="user", content=user)],
                context,
                system=facts,
                stable_system=stable,
                role="revised_memo",
                advisor_id=advisor.advisor_id,
                schema=ANALYSIS_SCHEMA,
            )
            if not resp.ok or not resp.parsed:
                return own  # Keep the original position rather than losing the advisor.
            return _analysis_from(resp.parsed, advisor)

        results = await asyncio.gather(*(one(a) for a in advisors))
        return [r for r in results if r is not None]

    async def _risk_challenge(
        self,
        analyses: list[AdvisorAnalysis],
        facts: str,
        question: str,
        context: RunContext,
    ) -> RiskChallenge | None:
        stable, user = prompts.risk_challenge_prompt(analyses, question)
        resp = await self._provider.generate(
            [Message(role="user", content=user)],
            context,
            system=facts,
            stable_system=stable,
            role="risk_challenge",
            schema=RISK_CHALLENGE_SCHEMA,
        )
        if not resp.ok or not resp.parsed:
            return None
        p = resp.parsed
        return RiskChallenge(
            scenarios=_str_list(p.get("scenarios")),
            unaddressed_risks=_str_list(p.get("unaddressed_risks")),
            worst_case=str(p.get("worst_case", "")),
        )

    async def _synthesize(
        self,
        analyses: list[AdvisorAnalysis],
        critiques: list[Critique],
        risk: RiskChallenge | None,
        facts: str,
        question: str,
        guardrails: list[Guardrail],
        context: RunContext,
    ) -> CommitteeReport:
        critiques_text = "\n".join(
            f"- {c.from_advisor_id} on {c.target_advisor_id}: {c.disagreement}"
            for c in critiques
            if c.disagreement
        )
        risk_text = ""
        if risk:
            risk_text = (
                "Scenarios: " + "; ".join(risk.scenarios) + "\n"
                "Unaddressed: " + "; ".join(risk.unaddressed_risks) + "\n"
                f"Worst case: {risk.worst_case}"
            )

        stable, user = prompts.synthesis_prompt(
            analyses, critiques_text, risk_text, question, guardrails
        )
        resp = await self._provider.generate(
            [Message(role="user", content=user)],
            context,
            system=facts,
            stable_system=stable,
            role="synthesis",
            schema=SYNTHESIS_SCHEMA,
            max_tokens=max(context.model_config.max_tokens, 3072),
        )
        if not resp.ok or not resp.parsed:
            reason = resp.usage.error or (
                "the model declined the request" if resp.refused else "no output"
            )
            raise CommitteeError(f"Committee synthesis failed: {reason}")

        p = resp.parsed
        return CommitteeReport(
            run_id=context.run_id,
            created_at=datetime.now(UTC),
            depth=context.depth,
            question=question,
            summary=str(p.get("summary", "")),
            consensus=_str_list(p.get("consensus")),
            disagreements=_str_list(p.get("disagreements")),
            recommended_actions=_str_list(p.get("recommended_actions")),
            open_questions=_str_list(p.get("open_questions")),
        )


# --- parsing helpers ------------------------------------------------------------------


def _clamp01(value: Any) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return 0.5


def _str_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(v) for v in value if str(v).strip()]


def _analysis_from(
    parsed: dict[str, Any] | None,
    advisor: AdvisorRuntimeProfile,
    *,
    resp_failed: bool = False,
    refused: bool = False,
) -> AdvisorAnalysis:
    if parsed is None:
        note = (
            "The model declined this request."
            if refused
            else "This advisor's call did not return a usable analysis."
        )
        return AdvisorAnalysis(
            advisor_id=advisor.advisor_id,
            display_name=advisor.display_name,
            thesis="",
            reasoning="",
            confidence=0.0,
            declined=True,
            declined_reason=note,
        )
    return AdvisorAnalysis(
        advisor_id=advisor.advisor_id,
        display_name=advisor.display_name,
        thesis=str(parsed.get("thesis", "")),
        reasoning=str(parsed.get("reasoning", "")),
        recommendations=_str_list(parsed.get("recommendations")),
        risks_flagged=_str_list(parsed.get("risks_flagged")),
        confidence=_clamp01(parsed.get("confidence", 0.5)),
        declined=bool(parsed.get("declined", False)),
        declined_reason=str(parsed.get("declined_reason", "")),
    )
