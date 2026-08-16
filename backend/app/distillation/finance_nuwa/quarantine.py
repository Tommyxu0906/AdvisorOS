"""Excluding the observations we cannot honestly interpret, and nothing more.

Five transitions in the Berkshire range are Liberty Media recapitalisations into tracking
stocks: one security becomes two, and `SecurityLineage` models a single successor. Three options
were available and two of them are worse than they look.

Implementing one-to-many lineage now means exchange ratios, basis allocation, and split-off
versus spin-off semantics — and with no authoritative corporate-actions feed behind it, the
result would be precise-looking numbers resting on guesses. That is the failure this project
keeps refusing.

Dropping the six Liberty CUSIPs from the whole history is the other tempting shortcut, and it
throws away eleven years of genuine behaviour to avoid a handful of ambiguous quarters. A
position that was recapitalised in 2016 Q2 has a perfectly interpretable identity in 2019, and
its decisions then are real evidence.

So the exclusion is scoped to the *transition*, not the security. Only labels whose meaning
depends on the unresolved event are removed; other securities in the same quarter are untouched,
and the same security becomes usable again as soon as its identity is stable.

The distinction the audit must keep is that **a quarantined episode is not a resolved one**. It
is explicitly withheld, counted, and reported. The gate asks whether any unresolved blocking
action still reaches the modelling data — not whether any exists.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

from app.distillation.finance_nuwa.corporate_actions import ActionCandidate, CorporateActionKind


class ExclusionStatus(str, Enum):
    unresolved_one_to_many = "unresolved_one_to_many"
    """A security became several. Representable only by a lineage graph we do not have."""

    unresolved_unknown = "unresolved_unknown"
    """Something happened that the detector could not classify at all."""

    @property
    def is_unresolved(self) -> bool:
        return True


class ExclusionScope(str, Enum):
    labels_only = "labels_only"
    """Remove the affected securities' labels for this transition. Everything else stands."""

    whole_quarter = "whole_quarter"
    """The quarter itself cannot be trusted. Reserved for corruption, not ambiguity."""


class EpisodeExclusion(BaseModel):
    """One transition, the securities it touches, and why they cannot be interpreted."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    transition_period_end: date
    cusips: tuple[str, ...]
    detected_kind: CorporateActionKind

    reason: str
    evidence: str
    status: ExclusionStatus = ExclusionStatus.unresolved_one_to_many
    scope: ExclusionScope = ExclusionScope.labels_only
    dataset_version: str = ""

    def covers(self, *, period_end: date, cusip: str) -> bool:
        """Whether this exclusion applies to one security at one transition.

        Both conditions, never one. Matching on the security alone would remove it from every
        quarter in the dataset; matching on the quarter alone would remove positions that had
        nothing to do with the event.
        """
        if period_end != self.transition_period_end:
            return False
        if self.scope is ExclusionScope.whole_quarter:
            return True
        return cusip in self.cusips


class ExclusionRegistry(BaseModel):
    """Every scoped exclusion, with the version it belongs to."""

    model_config = ConfigDict(extra="forbid")

    exclusions: list[EpisodeExclusion] = Field(default_factory=list)
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    def excluding(self, *, period_end: date, cusip: str) -> EpisodeExclusion | None:
        for exclusion in self.exclusions:
            if exclusion.covers(period_end=period_end, cusip=cusip):
                return exclusion
        return None

    def is_excluded(self, *, period_end: date, cusip: str) -> bool:
        return self.excluding(period_end=period_end, cusip=cusip) is not None

    @property
    def transitions(self) -> int:
        return len({e.transition_period_end for e in self.exclusions})

    @property
    def securities(self) -> int:
        return len({c for e in self.exclusions for c in e.cusips})


def quarantine_unresolved(
    candidates: list[ActionCandidate], *, dataset_version: str
) -> ExclusionRegistry:
    """Build a registry from the blocking candidates that no curated entry resolved.

    Derived from the unresolved list rather than hand-listed, so a newly detected ambiguity is
    quarantined automatically instead of silently entering the training data while someone gets
    round to writing it down.
    """
    grouped: dict[tuple[date, CorporateActionKind], set[str]] = {}
    reasons: dict[tuple[date, CorporateActionKind], str] = {}

    for candidate in candidates:
        key = (candidate.period_end, candidate.suspected)
        touched = grouped.setdefault(key, set())
        touched.add(candidate.from_cusip)
        if candidate.to_cusip:
            touched.add(candidate.to_cusip)
        reasons.setdefault(key, candidate.reason)

    exclusions = []
    for (period_end, kind), cusips in sorted(grouped.items(), key=lambda kv: kv[0][0]):
        # One source security appearing with several successors in one transition is the
        # signature of a recapitalisation into multiple securities.
        sources = [c.from_cusip for c in candidates if c.period_end == period_end]
        one_to_many = len(sources) != len(set(sources)) or len(cusips) > 2

        exclusions.append(
            EpisodeExclusion(
                transition_period_end=period_end,
                cusips=tuple(sorted(cusips)),
                detected_kind=kind,
                reason=(
                    "one security maps to several successors, which a single-successor lineage "
                    "model cannot express without dropping a leg or double-counting"
                    if one_to_many
                    else "detected as blocking and not resolved by any curated lineage entry"
                ),
                evidence=reasons[(period_end, kind)],
                status=(
                    ExclusionStatus.unresolved_one_to_many
                    if one_to_many
                    else ExclusionStatus.unresolved_unknown
                ),
                scope=ExclusionScope.labels_only,
                dataset_version=dataset_version,
            )
        )
    return ExclusionRegistry(exclusions=exclusions)
