"""Deterministic advisor selection and committee optimization.

No LLM. Given a need vector, a question intent, and the triggered guardrails, pick the smallest
team that covers the profile's dominant needs, and explain why each member was chosen.

Cost discipline is a first-class goal here: the optimizer stops as soon as coverage is adequate
rather than filling every available seat.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from app.domain.advisor import AdvisorManifest
from app.domain.needs import NEED_DIMENSIONS, NeedVector, cosine, dot
from app.domain.question import QuestionIntent
from app.domain.report import AnalysisDepth, Guardrail, GuardrailSeverity

# A need dimension counts as "covered" once some selected advisor scores at least this well on it.
COVERAGE_THRESHOLD = 0.6
# Need dimensions below this are not considered pressing enough to require coverage.
NEED_SALIENCE_THRESHOLD = 0.4
# How hard to penalize an advisor that overlaps an already-selected one.
DIVERSITY_PENALTY = 0.55
# Bonus applied when an advisor's topic affinity matches the question.
TOPIC_AFFINITY_BONUS = 0.35
# Bonus per guardrail-implied dimension the advisor covers well.
GUARDRAIL_BONUS = 0.5

# Which need dimension each guardrail code makes mandatory to cover.
GUARDRAIL_DIMENSION: dict[str, str] = {
    "HORIZON_RISK_MISMATCH": "horizon_pressure",
    "POSITION_CONCENTRATION": "concentration_risk",
    "THIN_BOOK": "concentration_risk",
    "NO_DEPLOYABLE_CASH": "liquidity_risk",
}


class AdvisorScore(BaseModel):
    model_config = ConfigDict(extra="forbid")

    advisor_id: str
    display_name: str
    base_score: float
    topic_bonus: float
    guardrail_bonus: float
    total_score: float
    strongest_dimensions: list[str] = Field(default_factory=list)


class SelectedAdvisor(BaseModel):
    model_config = ConfigDict(extra="forbid")

    advisor_id: str
    display_name: str
    score: float
    rationale: str
    covers: list[str] = Field(default_factory=list)


class CommitteeSelection(BaseModel):
    """The full, explainable output of selection. Shown to the user before any token is spent."""

    model_config = ConfigDict(extra="forbid")

    depth: AnalysisDepth
    selected: list[SelectedAdvisor] = Field(default_factory=list)
    considered: list[AdvisorScore] = Field(default_factory=list)
    required_dimensions: list[str] = Field(default_factory=list)
    covered_dimensions: list[str] = Field(default_factory=list)
    uncovered_dimensions: list[str] = Field(default_factory=list)
    mandatory_dimensions: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)

    @property
    def advisor_ids(self) -> list[str]:
        return [s.advisor_id for s in self.selected]


def score_advisors(
    manifests: list[AdvisorManifest],
    need: NeedVector,
    intent: QuestionIntent,
    guardrails: list[Guardrail],
) -> list[AdvisorScore]:
    """Score every advisor independently. Deterministic and order-stable."""
    mandatory = _mandatory_dimensions(guardrails)
    scores: list[AdvisorScore] = []

    for m in manifests:
        base = dot(need, m.expertise)
        topic_bonus = (
            TOPIC_AFFINITY_BONUS if any(t in m.topic_affinity for t in intent.topics) else 0.0
        )
        guard_bonus = GUARDRAIL_BONUS * sum(
            1 for d in mandatory if m.expertise.as_dict()[d] >= COVERAGE_THRESHOLD
        )
        expertise = m.expertise.as_dict()
        strongest = [k for k, v in sorted(expertise.items(), key=lambda kv: -kv[1])[:3] if v >= 0.6]
        scores.append(
            AdvisorScore(
                advisor_id=m.advisor_id,
                display_name=m.display_name,
                base_score=base,
                topic_bonus=topic_bonus,
                guardrail_bonus=guard_bonus,
                total_score=base + topic_bonus + guard_bonus,
                strongest_dimensions=strongest,
            )
        )

    scores.sort(key=lambda s: (-s.total_score, s.advisor_id))
    return scores


def select_committee(
    manifests: list[AdvisorManifest],
    need: NeedVector,
    intent: QuestionIntent,
    guardrails: list[Guardrail],
    depth: AnalysisDepth = AnalysisDepth.balanced,
) -> CommitteeSelection:
    """Greedy marginal-coverage selection with a diversity penalty and an early stop.

    The early stop is the cost control: if three advisors already cover every salient need
    dimension, a fourth seat is left empty even when the depth mode allows it.
    """
    max_advisors = depth.advisor_count
    min_advisors = min(depth.min_advisor_count, len(manifests))
    by_id = {m.advisor_id: m for m in manifests}
    scores = score_advisors(manifests, need, intent, guardrails)
    score_by_id = {s.advisor_id: s for s in scores}

    need_dict = need.as_dict()
    mandatory = _mandatory_dimensions(guardrails)
    required = sorted(
        {d.value for d in NEED_DIMENSIONS if need_dict[d.value] >= NEED_SALIENCE_THRESHOLD}
        | set(mandatory)
    )

    selected: list[SelectedAdvisor] = []
    covered: set[str] = set()
    notes: list[str] = []

    while len(selected) < max_advisors:
        remaining = [m for m in manifests if m.advisor_id not in {s.advisor_id for s in selected}]
        if not remaining:
            break

        best: tuple[float, AdvisorManifest, list[str]] | None = None
        for m in remaining:
            expertise = m.expertise.as_dict()
            newly = [d for d in required if d not in covered and expertise[d] >= COVERAGE_THRESHOLD]
            marginal = sum(need_dict[d] * expertise[d] for d in newly)
            overlap = max(
                (cosine(m.expertise, by_id[s.advisor_id].expertise) for s in selected),
                default=0.0,
            )
            value = (
                score_by_id[m.advisor_id].total_score * 0.5
                + marginal * 1.5
                - overlap * DIVERSITY_PENALTY
            )
            if (
                best is None
                or value > best[0]
                or (value == best[0] and m.advisor_id < best[1].advisor_id)
            ):
                best = (value, m, newly)

        assert best is not None
        _, chosen, newly = best

        # Early stop: past the minimum viable committee, everything salient is already covered,
        # and this pick would add cost without adding coverage.
        if len(selected) >= min_advisors and not newly and covered >= set(required):
            notes.append(
                f"Stopped at {len(selected)} advisors — all {len(required)} salient need "
                f"dimensions were already covered. The remaining seat would add cost without coverage."
            )
            break

        covered |= set(newly)
        selected.append(
            SelectedAdvisor(
                advisor_id=chosen.advisor_id,
                display_name=chosen.display_name,
                score=score_by_id[chosen.advisor_id].total_score,
                covers=newly,
                rationale=_rationale(chosen, newly, need_dict, intent, mandatory),
            )
        )

    uncovered = sorted(set(required) - covered)
    if uncovered:
        notes.append(
            "No available advisor covers "
            + ", ".join(uncovered)
            + " above the coverage threshold. Treat conclusions on those dimensions as weakly supported."
        )

    return CommitteeSelection(
        depth=depth,
        selected=selected,
        considered=scores,
        required_dimensions=required,
        covered_dimensions=sorted(covered),
        uncovered_dimensions=uncovered,
        mandatory_dimensions=sorted(mandatory),
        notes=notes,
    )


def _mandatory_dimensions(guardrails: list[Guardrail]) -> set[str]:
    """Dimensions that a blocking or cautionary guardrail forces the committee to cover."""
    return {
        GUARDRAIL_DIMENSION[g.code]
        for g in guardrails
        if g.code in GUARDRAIL_DIMENSION
        and g.severity in (GuardrailSeverity.blocking, GuardrailSeverity.caution)
    }


def _rationale(
    manifest: AdvisorManifest,
    newly_covered: list[str],
    need_dict: dict[str, float],
    intent: QuestionIntent,
    mandatory: set[str],
) -> str:
    parts: list[str] = []
    if newly_covered:
        ranked = sorted(newly_covered, key=lambda d: -need_dict[d])
        readable = ", ".join(d.replace("_", " ") for d in ranked)
        parts.append(f"Covers {readable}")
        forced = [d for d in ranked if d in mandatory]
        if forced:
            parts.append(
                "which a triggered guardrail requires ("
                + ", ".join(d.replace("_", " ") for d in forced)
                + ")"
            )
    on_topic = any(t in manifest.topic_affinity for t in intent.topics)
    if not parts:
        # Selected for perspective rather than marginal coverage.
        strengths = [k for k, v in manifest.expertise.as_dict().items() if v >= 0.7]
        lead = "Adds a distinct perspective"
        if strengths:
            lead += " (" + ", ".join(s.replace("_", " ") for s in sorted(strengths)[:3]) + ")"
        parts.append(lead)
    if on_topic:
        parts.append(f"works directly on the question's topic ({intent.primary_topic.value})")
    return "; ".join(parts) + "."
