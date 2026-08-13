"""Nuwa — the advisor production layer.

Distillation is an expensive one-time operation that turns a subject ("Benjamin Graham") into a
reusable AdvisorManifest. It runs on the *requesting user's* Anthropic key, never the project
owner's, and it is never re-run when an existing advisor joins a committee.

    subject + focus + depth
        -> research plan          (1 LLM call)
        -> research passes        (N LLM calls, concurrent)
        -> synthesis              (1 LLM call, structured output)
        -> deterministic validation
        -> registry write

Everything after synthesis is deterministic: schema validation, expertise-vector normalization,
and a hard requirement that the persona declares honest boundaries.
"""

from __future__ import annotations

import asyncio
import re
from datetime import UTC, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.advisors.registry import AdvisorRegistry
from app.core.run_context import RunContext
from app.domain.advisor import AdvisorManifest, AdvisorOrigin, EvidenceRef
from app.domain.needs import NEED_DIMENSIONS, ExpertiseVector
from app.domain.question import QuestionTopic
from app.llm.provider import LLMProvider, Message
from app.nuwa.schemas import PLAN_SCHEMA, RESEARCH_SCHEMA, SYNTHESIS_SCHEMA


class DistillationDepth(str, Enum):
    quick = "quick"
    standard = "standard"
    deep = "deep"

    @property
    def research_passes(self) -> int:
        return {
            DistillationDepth.quick: 2,
            DistillationDepth.standard: 4,
            DistillationDepth.deep: 7,
        }[self]

    def expected_calls(self) -> int:
        """Upper bound: plan + research passes + synthesis.

        A run can make fewer calls if the planner returns fewer questions than the budget
        allows, or if a research pass fails. It never makes more.
        """
        return 1 + self.research_passes + 1


class DistillationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subject: str = Field(min_length=2, max_length=200)
    focus_areas: list[str] = Field(default_factory=list, max_length=8)
    depth: DistillationDepth = DistillationDepth.standard
    advisor_id: str | None = Field(default=None, pattern=r"^[a-z0-9_]+$")


class DistillationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    manifest: AdvisorManifest
    research_pass_count: int
    warnings: list[str] = Field(default_factory=list)
    runtime_profile_tokens: int


class DistillationError(RuntimeError):
    pass


NUWA_CHARTER = """\
You are Nuwa, a distillation system that turns a well-documented investor or school of thought
into a reusable reasoning profile.

You are producing a *persona for analysis*, not an impersonation and not a claim to speak for a
real person. Follow these rules:

- Base everything on what the subject has actually written, said, or is reliably documented to
  believe. Do not invent positions, quotes, or biographical facts.
- Capture how the subject *reasons*, not just what they concluded. Mental models and decision
  rules transfer to new situations; specific past picks do not.
- Be honest about limits. Every profile must state what the subject is bad at, wrong about, or
  unwilling to opine on. A persona with no blind spots is a failed distillation.
- Never produce a profile that would give personalized investment advice with false authority.
"""


class NuwaDistiller:
    def __init__(self, provider: LLMProvider, registry: AdvisorRegistry) -> None:
        self._provider = provider
        self._registry = registry

    async def distill(
        self, request: DistillationRequest, context: RunContext, *, register: bool = True
    ) -> DistillationResult:
        plan = await self._plan(request, context)
        findings = await self._research(request, plan, context)
        manifest, warnings = await self._synthesize(request, plan, findings, context)

        if register:
            self._registry.register(manifest, persist=True)

        return DistillationResult(
            manifest=manifest,
            research_pass_count=len(findings),
            warnings=warnings,
            runtime_profile_tokens=manifest.to_runtime_profile().approx_tokens(),
        )

    # --- stages -----------------------------------------------------------------------

    async def _plan(self, request: DistillationRequest, context: RunContext) -> list[str]:
        focus = ", ".join(request.focus_areas) if request.focus_areas else "their overall approach"
        user = (
            f"Subject: {request.subject}\n"
            f"Focus areas: {focus}\n"
            f"Number of research questions to produce: {request.depth.research_passes}\n\n"
            "Produce the research questions that would most efficiently reveal how this subject "
            "reasons. Prefer questions about decision rules and failure modes over questions "
            "about biography or performance history."
        )
        resp = await self._provider.generate(
            [Message(role="user", content=user)],
            context,
            stable_system=NUWA_CHARTER,
            role="nuwa_plan",
            schema=PLAN_SCHEMA,
        )
        if not resp.ok or not resp.parsed:
            raise DistillationError(
                f"Could not plan the distillation: {resp.usage.error or 'no usable output'}"
            )
        questions = [str(q) for q in resp.parsed.get("questions", []) if str(q).strip()]
        if not questions:
            raise DistillationError("The research plan came back empty.")
        return questions[: request.depth.research_passes]

    async def _research(
        self, request: DistillationRequest, questions: list[str], context: RunContext
    ) -> list[dict[str, Any]]:
        async def one(question: str) -> dict[str, Any] | None:
            user = (
                f"Subject: {request.subject}\n\n"
                f"Research question: {question}\n\n"
                "Answer from documented material. Where you are uncertain whether the subject "
                "actually held a view, say so rather than asserting it."
            )
            resp = await self._provider.generate(
                [Message(role="user", content=user)],
                context,
                stable_system=NUWA_CHARTER,
                role="nuwa_research",
                schema=RESEARCH_SCHEMA,
            )
            if not resp.ok or not resp.parsed:
                return None
            return {"question": question, **resp.parsed}

        results = await asyncio.gather(*(one(q) for q in questions))
        findings = [r for r in results if r is not None]
        if not findings:
            raise DistillationError("Every research pass failed; nothing to synthesize.")
        return findings

    async def _synthesize(
        self,
        request: DistillationRequest,
        questions: list[str],
        findings: list[dict[str, Any]],
        context: RunContext,
    ) -> tuple[AdvisorManifest, list[str]]:
        research_block = "\n\n".join(
            f"### {f['question']}\n"
            f"Findings: {'; '.join(str(x) for x in f.get('findings', []))}\n"
            f"Decision rules: {'; '.join(str(x) for x in f.get('decision_rules', []))}\n"
            f"Limitations: {'; '.join(str(x) for x in f.get('limitations', []))}\n"
            f"Sources: {'; '.join(str(x) for x in f.get('sources', []))}"
            for f in findings
        )
        dimensions = ", ".join(d.value for d in NEED_DIMENSIONS)
        topics = ", ".join(t.value for t in QuestionTopic)
        user = (
            f"Subject: {request.subject}\n\n"
            f"## Research\n\n{research_block}\n\n"
            "Synthesize this into a single reusable advisor profile.\n\n"
            f"`expertise` must score the subject from 0 to 1 on each of: {dimensions}. Be "
            "discriminating — a profile that scores highly on everything is useless for routing, "
            "because it never differentiates from any other advisor.\n"
            f"`topic_affinity` must be drawn only from: {topics}.\n"
            "`blind_spots` and `honest_boundaries` must both be non-empty."
        )
        resp = await self._provider.generate(
            [Message(role="user", content=user)],
            context,
            stable_system=NUWA_CHARTER,
            role="nuwa_synthesis",
            schema=SYNTHESIS_SCHEMA,
            max_tokens=max(context.model_config.max_tokens, 4096),
        )
        if not resp.ok or not resp.parsed:
            raise DistillationError(f"Synthesis failed: {resp.usage.error or 'no usable output'}")
        return _build_manifest(request, resp.parsed)


# --- deterministic validation ----------------------------------------------------------


def slugify(subject: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", subject.lower()).strip("_")
    slug = re.sub(r"_+", "_", slug)
    return slug or "custom_advisor"


def _build_manifest(
    request: DistillationRequest, parsed: dict[str, Any]
) -> tuple[AdvisorManifest, list[str]]:
    """Validate and normalize LLM output into a manifest. Pure code — no model involved."""
    warnings: list[str] = []

    advisor_id = request.advisor_id or slugify(request.subject)

    raw_expertise = parsed.get("expertise") or {}
    if not isinstance(raw_expertise, dict):
        raw_expertise = {}
    scores: dict[str, float] = {}
    for dim in NEED_DIMENSIONS:
        try:
            scores[dim.value] = max(0.0, min(1.0, float(raw_expertise.get(dim.value, 0.0))))
        except (TypeError, ValueError):
            scores[dim.value] = 0.0
            warnings.append(f"Expertise score for {dim.value} was unusable; defaulted to 0.")

    if all(v == 0.0 for v in scores.values()):
        raise DistillationError(
            "The distilled profile scored zero on every need dimension, so it could never be "
            "selected for a committee. Treat this run as failed."
        )
    if sum(1 for v in scores.values() if v >= 0.8) >= 6:
        warnings.append(
            "This profile scores highly on nearly every dimension, which weakens advisor "
            "routing. Consider re-running the distillation with narrower focus areas."
        )

    topics: list[QuestionTopic] = []
    for t in parsed.get("topic_affinity") or []:
        try:
            topics.append(QuestionTopic(str(t)))
        except ValueError:
            warnings.append(f"Dropped unrecognized topic affinity '{t}'.")

    blind_spots = _strs(parsed.get("blind_spots"))
    boundaries = _strs(parsed.get("honest_boundaries"))
    if not blind_spots:
        raise DistillationError(
            "The distilled profile declares no blind spots. A persona with no acknowledged limits "
            "is not safe to put in front of a user's finances."
        )
    if not boundaries:
        raise DistillationError(
            "The distilled profile declares no honest boundaries — no questions it will decline. "
            "This is a required field."
        )

    evidence = []
    for e in parsed.get("evidence") or []:
        if isinstance(e, dict):
            evidence.append(
                EvidenceRef(
                    label=str(e.get("label", "")).strip() or "unlabelled source",
                    source=str(e.get("source", "")),
                    note=str(e.get("note", "")),
                )
            )
        elif str(e).strip():
            evidence.append(EvidenceRef(label=str(e)))
    if not evidence:
        warnings.append("No evidence references were produced; the profile is unsourced.")

    manifest = AdvisorManifest(
        advisor_id=advisor_id,
        display_name=str(parsed.get("display_name") or request.subject),
        subject=request.subject,
        origin=AdvisorOrigin.custom,
        one_line=str(parsed.get("one_line") or f"Reasoning in the style of {request.subject}."),
        expertise=ExpertiseVector(**scores),
        topic_affinity=topics,
        mental_models=_strs(parsed.get("mental_models"))[:12],
        heuristics=_strs(parsed.get("heuristics"))[:12],
        reasoning_rules=_strs(parsed.get("reasoning_rules"))[:10],
        blind_spots=blind_spots[:8],
        honest_boundaries=boundaries[:8],
        evidence=evidence[:8],
        provenance=(
            f"Distilled by Nuwa at depth '{request.depth.value}' from subject "
            f"'{request.subject}'. Focus areas: "
            f"{', '.join(request.focus_areas) if request.focus_areas else 'general'}."
        ),
        distilled_at=datetime.now(UTC),
    )

    if not manifest.mental_models and not manifest.heuristics:
        raise DistillationError(
            "The distilled profile contains neither mental models nor heuristics; there is "
            "nothing for the advisor to reason with."
        )

    return manifest, warnings


def _strs(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(v).strip() for v in value if str(v).strip()]
