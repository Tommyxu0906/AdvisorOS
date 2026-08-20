"""Run one consultation turn: fan out to the lenses, constrain, then synthesize.

Concurrent on purpose. The lenses are meant to reach their own view before hearing this turn's
answers from the others — sequential calls would let the second read the first, and a committee
whose members echo each other in order is not producing independent perspectives. They *do* see
every previous turn, which is what makes it a conversation rather than a series of unrelated
one-shots.

The synthesis at the end is deterministic. It counts votes over feasible candidates and applies
a tie-break; no model chooses the outcome. That is the whole architecture in one function — the
lenses have a real say in what gets picked, and arithmetic decides what they are allowed to pick
between.
"""

from __future__ import annotations

import asyncio
import json
import logging

from app.consult.candidates import ACT_ID, HOLD_ID, build_candidates
from app.consult.constraints import apply_constraints, failed_response
from app.consult.models import (
    AdvisorConsultResponse,
    ChatMessage,
    ConfidenceSignal,
    ConsultSynthesis,
    DecisionCandidate,
    Stance,
)
from app.consult.prompts import consult_prompt
from app.consult.schemas import CONSULT_SCHEMA
from app.core.run_context import RunContext
from app.domain.advisor import AdvisorRuntimeProfile
from app.domain.report import Guardrail
from app.llm.provider import LLMProvider, Message
from app.policy.engine import PortfolioScenario

logger = logging.getLogger(__name__)

CONSULT_ROLE = "advisor_consult"
MAX_TOKENS = 1200


class ConsultResult:
    """One turn's output. A plain object — it is assembled, not validated from the wire."""

    def __init__(
        self,
        responses: list[AdvisorConsultResponse],
        candidates: list[DecisionCandidate],
        synthesis: ConsultSynthesis,
    ) -> None:
        self.responses = responses
        self.candidates = candidates
        self.synthesis = synthesis


async def consult(
    *,
    provider: LLMProvider,
    context: RunContext,
    advisors: list[AdvisorRuntimeProfile],
    profile,  # FinancialProfile
    analytics,  # ProfileAnalytics
    portfolio_analytics,  # PortfolioAnalytics | None
    scenario: PortfolioScenario | None,
    guardrails: list[Guardrail],
    history: list[ChatMessage],
    question: str,
) -> ConsultResult:
    candidates = build_candidates(scenario, guardrails)

    responses = await asyncio.gather(
        *(
            _ask_one(
                provider=provider,
                context=context,
                advisor=advisor,
                profile=profile,
                analytics=analytics,
                portfolio_analytics=portfolio_analytics,
                scenario=scenario,
                guardrails=guardrails,
                candidates=candidates,
                history=history,
                question=question,
            )
            for advisor in advisors
        )
    )

    constrained = [apply_constraints(r, candidates) for r in responses]
    return ConsultResult(constrained, candidates, synthesize(constrained, candidates))


async def _ask_one(
    *,
    provider: LLMProvider,
    context: RunContext,
    advisor: AdvisorRuntimeProfile,
    profile,
    analytics,
    portfolio_analytics,
    scenario,
    guardrails,
    candidates,
    history,
    question,
) -> AdvisorConsultResponse:
    stable, user = consult_prompt(
        advisor=advisor,
        profile=profile,
        analytics=analytics,
        portfolio_analytics=portfolio_analytics,
        scenario=scenario,
        guardrails=guardrails,
        candidates=candidates,
        history=history,
        question=question,
    )

    try:
        result = await provider.generate(
            [Message(role="user", content=user)],
            context,
            stable_system=stable,
            role=CONSULT_ROLE,
            advisor_id=advisor.advisor_id,
            schema=CONSULT_SCHEMA,
            max_tokens=MAX_TOKENS,
        )
    except Exception as exc:  # noqa: BLE001 - one lens failing must not end the consultation
        logger.warning("consult call failed for %s: %s", advisor.advisor_id, type(exc).__name__)
        return failed_response(advisor.advisor_id, advisor.display_name, type(exc).__name__)

    if result.refused:
        return AdvisorConsultResponse(
            advisor_id=advisor.advisor_id,
            display_name=advisor.display_name,
            stance=Stance.abstain,
            declined=True,
            declined_reason="This framework declined to answer on this question.",
            confidence_signal=ConfidenceSignal.low,
        )

    return parse_response(result.text, advisor)


def parse_response(text: str, advisor: AdvisorRuntimeProfile) -> AdvisorConsultResponse:
    """Read one lens's JSON. A malformed answer fails to an explicit parse failure.

    Never to an endorsement and never to a silent abstention: a turn where two lenses returned
    unreadable output and the engine proceeded unopposed must not look like a turn where two
    lenses agreed.
    """
    try:
        payload = json.loads(text)
    except (json.JSONDecodeError, TypeError) as exc:
        return failed_response(advisor.advisor_id, advisor.display_name, f"invalid JSON ({exc})")

    if not isinstance(payload, dict):
        return failed_response(
            advisor.advisor_id, advisor.display_name, "payload was not an object"
        )

    try:
        stance = Stance(str(payload.get("stance", "")).strip().lower())
    except ValueError:
        return failed_response(
            advisor.advisor_id, advisor.display_name, f"unknown stance {payload.get('stance')!r}"
        )

    try:
        confidence = ConfidenceSignal(str(payload.get("confidence_signal", "low")).strip().lower())
    except ValueError:
        # A band we do not recognize degrades to `low` rather than failing the whole response:
        # the stance is the load-bearing field, and low is the honest default for "unstated".
        confidence = ConfidenceSignal.low

    preferred = payload.get("preferred_candidate_id")
    if isinstance(preferred, str) and not preferred.strip():
        preferred = None

    return AdvisorConsultResponse(
        advisor_id=advisor.advisor_id,
        display_name=advisor.display_name,
        stance=stance,
        # Abstention carries no preference; the model validator would reject the pair.
        preferred_candidate_id=None if stance is Stance.abstain else preferred,
        supported_action_ids=_strings(payload.get("supported_action_ids")),
        opposed_action_ids=_strings(payload.get("opposed_action_ids")),
        rationale=str(payload.get("rationale", "")).strip(),
        risks_or_missing_information=_strings(payload.get("risks_or_missing_information")),
        confidence_signal=confidence,
        declined=bool(payload.get("declined", False)),
        declined_reason=str(payload.get("declined_reason", "")).strip(),
    )


def _strings(value) -> list[str]:  # noqa: ANN001
    if not isinstance(value, list):
        return []
    return [str(v).strip() for v in value if str(v).strip()]


def synthesize(
    responses: list[AdvisorConsultResponse],
    candidates: list[DecisionCandidate],
) -> ConsultSynthesis:
    """Pick a feasible candidate and say who wanted what. Deterministic — no model runs here.

    Votes are counted only over feasible candidates, because a vote for something arithmetic
    forbids is not a vote the outcome can honour. It is still reported: `overrides` carries every
    case where a lens wanted something the constraints removed.
    """
    feasible = [c for c in candidates if c.feasible]
    fallback = feasible[0] if feasible else candidates[0]

    tally: dict[str, int] = {c.candidate_id: 0 for c in feasible}
    endorsing: list[str] = []
    opposing: list[str] = []
    abstaining: list[str] = []
    overrides: list[str] = []

    for response in responses:
        if response.parse_failed:
            # Not an abstention — a lens that could not be read contributed nothing at all.
            continue
        if response.stance is Stance.abstain:
            abstaining.append(response.display_name)
            continue
        if response.preferred_candidate_id in tally:
            tally[response.preferred_candidate_id] += 1
        if response.stance in (Stance.endorse, Stance.mixed):
            endorsing.append(response.display_name)
        if response.stance is Stance.oppose:
            opposing.append(response.display_name)

        overrides.extend(
            f"{response.display_name}: {correction}" for correction in response.corrections
        )

    top = max(tally, key=lambda cid: tally[cid]) if tally and max(tally.values()) > 0 else None
    selected = next((c for c in feasible if c.candidate_id == top), fallback)

    # Genuine disagreement: at least one lens each way, and no majority behind the winner.
    votes = tally.get(selected.candidate_id, 0)
    unresolved = bool(endorsing) and bool(opposing) and votes <= len(responses) / 2

    return ConsultSynthesis(
        selected_candidate_id=selected.candidate_id,
        selected_label=selected.label,
        headline=_headline(selected, endorsing, opposing, abstaining, unresolved),
        endorsing=endorsing,
        opposing=opposing,
        abstaining=abstaining,
        overrides=overrides,
        unresolved_disagreement=unresolved,
    )


def _headline(
    selected: DecisionCandidate,
    endorsing: list[str],
    opposing: list[str],
    abstaining: list[str],
    unresolved: bool,
) -> str:
    if not endorsing and not opposing:
        if abstaining:
            return (
                f"No framework offered a view. {selected.label} stands because it is what the "
                "computation supports, not because anyone argued for it."
            )
        return f"{selected.label} stands on the computation alone."

    if unresolved:
        return (
            f"The frameworks disagree and nothing here resolves it. {selected.label} is what the "
            "constraints permit, and the disagreement is worth reading before acting on it."
        )

    if selected.candidate_id == HOLD_ID:
        return "Nothing computed requires action, and the frameworks did not press for any."

    if selected.candidate_id == ACT_ID and not opposing:
        return f"{selected.label}, with no framework opposing it."

    return f"{selected.label} — supported by {', '.join(endorsing) or 'the computation'}."
