"""Turning two canonical quarters into decisions, on the right side of the information boundary.

There is exactly one way in: two `CanonicalQuarter` objects. No overload takes a raw
`HoldingsSnapshot`, and that absence is the point. Five quarters in the Berkshire range hold
positions that only an amendment reveals, and a builder that accepts raw snapshots would let a
future caller — quite reasonably, reading `snapshots/` off disk — silently reintroduce the
fabricated-purchase bug six months after everyone forgot about it. The safe path is not
documented, it is the only one that exists.

The subtler rule is the one this module is really built around. An amendment corrects what was
*true* without changing what was *knowable*, and a decision episode needs both, kept apart:

    ground truth        what the investor actually did, using every position we now know about,
                        including the ones disclosed years later

    replay inputs       what an observer could have seen when the decision was made, which
                        excludes anything still under confidential treatment at the time

Both come from the same `CanonicalQuarter`. Ground truth reads `positions`; inputs read
`positions_knowable_on(cutoff)`. Getting this backwards in either direction is a real failure:
use only public data for the label and purchases move to the wrong quarter; use the full
canonical set for the inputs and the model is handed holdings the market did not know about.

**Which book was public is not the book that just ended.** This is the part that surprises. A Q4
decision window opens on 1 October, but the Q3 holdings are not filed until mid-November — so an
outside observer standing on 1 October has the *Q2* filing, forty-five days after a quarter that
ended in June. The most recent quarter is never the most recent *knowable* quarter, and taking
`history[-1]` as the input book quietly grants six weeks to three months of hindsight on every
single episode. `_last_public_book` walks back until it finds one that had actually been filed.

That conservatism has a real cost and it is worth naming: the investor obviously knew their own
holdings on 1 October, so a strict public-observer replay asks the model to predict a decision
with less information than the decider had. `ReplayView` makes the choice explicit rather than
implicit, and defaults to the public view — a persona deployed later will only ever see public
data, so training it on private holdings would build in a train-serve skew on top of the
attribution problem.
"""

from __future__ import annotations

from datetime import date
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

from app.distillation.finance_nuwa.dataset import MagnitudeBucket, bucket_magnitude
from app.distillation.finance_nuwa.disclosure import DisclosureScope
from app.distillation.finance_nuwa.drift import (
    ActionBasis,
    ActionClassification,
    ObservedAction,
    PositionSnapshot,
    classify_portfolio,
)
from app.distillation.finance_nuwa.episode import (
    AttributionBasis,
    DecisionEpisode,
    EpisodeInputs,
    Observation,
)
from app.distillation.finance_nuwa.lineage import CanonicalPosition, CanonicalQuarter


class ReplayView(str, Enum):
    """Whose knowledge the replay inputs represent."""

    public_observer = "public_observer"
    """Only filings actually public at the cutoff. The default, and the stricter option."""

    investor_own_book = "investor_own_book"
    """The decider's own holdings as at the last quarter end, including confidential positions.
    Closer to what the investor knew, further from what a deployed persona will ever see."""


class BuiltAction(BaseModel):
    """One classified decision, before it becomes an episode."""

    model_config = ConfigDict(extra="forbid")

    classification: ActionClassification
    magnitude: MagnitudeBucket
    # True when the label depends on a position the public could not see at decision time. The
    # episode is still valid — the investor knew their own book — but it is worth counting.
    label_depends_on_late_disclosure: bool = False


def classify_quarter_pair(
    previous: CanonicalQuarter,
    current: CanonicalQuarter,
    *,
    returns: dict[str, float] | None = None,
    split_factors: dict[str, float] | None = None,
) -> list[BuiltAction]:
    """Classify every position change between two canonical quarters.

    Takes `CanonicalQuarter` and nothing else. A raw-snapshot overload would be convenient and
    would quietly resurrect the five quarters whose amendments matter.
    """
    late_keys = {p.identity.cusip for q in (previous, current) for p in q.late_disclosed}

    classifications = classify_portfolio(
        [_to_snapshot(p) for p in previous.positions],
        [_to_snapshot(p) for p in current.positions],
        returns=returns,
        split_factors=split_factors,
    )

    built: list[BuiltAction] = []
    for classification in classifications:
        fraction = classification.share_change_pct
        if fraction is None and classification.active_delta is not None:
            start = classification.start_weight or 0.0
            fraction = classification.active_delta / start if start > 0 else None
        built.append(
            BuiltAction(
                classification=classification,
                magnitude=bucket_magnitude(classification.action, fraction),
                label_depends_on_late_disclosure=classification.symbol in late_keys,
            )
        )
    return built


def build_episode(
    history: list[CanonicalQuarter],
    current: CanonicalQuarter,
    action: BuiltAction,
    *,
    advisor_id: str,
    entity: str,
    filed_at: date,
    view: ReplayView = ReplayView.public_observer,
    market_context: list[Observation] | None = None,
) -> DecisionEpisode:
    """One episode, with inputs cut off at the moment the decision window opened.

    `history` is every prior canonical quarter, oldest first — not just the previous one. Under
    the public view the input book is the most recent filing that was actually public at the
    cutoff, which for a Q4 decision is usually Q2 rather than Q3.

    The cutoff is the *start* of the quarter, not its end: a quarterly filing places a trade
    somewhere in three months, and if it happened on the first day then anything later is news
    the investor did not have. That is deliberately the conservative end.
    """
    window_start = _quarter_start(current.period_end)
    symbol = action.classification.symbol

    visible, source_quarter = _visible_book(history, window_start, view)
    visible_total = sum(p.market_value for p in visible)
    holding = next((p for p in visible if p.identity.cusip == symbol), None)

    portfolio_context = [
        Observation(
            label="disclosed portfolio positions",
            value=len(visible),
            observed_at=window_start,
            source=", ".join(source_quarter.contributing_accessions) if source_quarter else "",
            source_kind="filing",
        )
    ]
    if holding is not None and visible_total > 0:
        portfolio_context.append(
            Observation(
                label="position weight in the last disclosed book",
                value=round(holding.market_value / visible_total, 6),
                observed_at=window_start,
                source_kind="filing",
            )
        )

    inputs = EpisodeInputs(
        as_of=window_start,
        symbol=symbol,
        starting_weight=(
            round(holding.market_value / visible_total, 6)
            if holding is not None and visible_total > 0
            else None
        ),
        starting_value=holding.market_value if holding is not None else None,
        portfolio_context=portfolio_context,
        market_context=list(market_context or []),
    )

    return DecisionEpisode(
        advisor_id=advisor_id,
        episode_id=f"{advisor_id}-{symbol.lower()}-{current.period_end.isoformat()}",
        inputs=inputs,
        observed_action=action.classification.action,
        action_basis=action.classification.basis,
        magnitude_low=None,
        magnitude_high=None,
        decision_window_start=window_start,
        decision_window_end=current.period_end,
        disclosure=DisclosureScope.institutional(
            entity,
            period_start=window_start,
            period_end=current.period_end,
            filed_at=filed_at,
        ),
        # Entity-level, always. `otherManager` records shared discretion rather than who chose,
        # and position size is not evidence either — inventing an attribution rule and then
        # training on its output would make the whole evaluation circular.
        attribution=AttributionBasis.entity_filing,
        attribution_confidence=0.5,
    )


def _visible_book(
    history: list[CanonicalQuarter], cutoff: date, view: ReplayView
) -> tuple[list[CanonicalPosition], CanonicalQuarter | None]:
    """The holdings a replay may see, and which quarter they came from.

    Under the public view this walks back through history until it finds a quarter whose filing
    had actually landed by the cutoff. Taking the latest quarter instead would hand the model
    six weeks to three months of hindsight on every episode, which is the largest single leak
    available in this dataset and the easiest one to miss.
    """
    if not history:
        return [], None

    if view is ReplayView.investor_own_book:
        latest = history[-1]
        return list(latest.positions), latest

    for quarter in reversed(history):
        visible = quarter.positions_knowable_on(cutoff)
        if visible:
            return visible, quarter
    return [], None


def _to_snapshot(canonical) -> PositionSnapshot:
    """CUSIP is the symbol here: it is the identifier the filings actually key on."""
    return PositionSnapshot(
        symbol=canonical.identity.cusip,
        market_value=canonical.market_value,
        shares=canonical.shares or None,
    )


def _quarter_start(period_end: date) -> date:
    return date(period_end.year, ((period_end.month - 1) // 3) * 3 + 1, 1)


# Kept for the audit: how many labels the amendment resolution actually changed.
class LabelDelta(BaseModel):
    """What composing amendments did to the ground truth, measured rather than asserted."""

    model_config = ConfigDict(extra="forbid")

    period_end: date
    naive_counts: dict[str, int] = Field(default_factory=dict)
    canonical_counts: dict[str, int] = Field(default_factory=dict)
    fabricated_enters_removed: int = 0

    @property
    def changed(self) -> bool:
        return self.naive_counts != self.canonical_counts


def measure_label_change(
    previous_original_only: CanonicalQuarter,
    previous_canonical: CanonicalQuarter,
    current: CanonicalQuarter,
    *,
    returns: dict[str, float] | None = None,
) -> LabelDelta:
    """Compare labels built with and without the amendments, for one quarter transition.

    Data cleaning that changes no labels is housekeeping. This measures whether it changed the
    behavioural ground truth, which is a claim worth being able to substantiate.
    """
    naive = classify_quarter_pair(previous_original_only, current, returns=returns)
    canonical = classify_quarter_pair(previous_canonical, current, returns=returns)

    def counts(actions: list[BuiltAction]) -> dict[str, int]:
        out: dict[str, int] = {}
        for a in actions:
            out[a.classification.action.value] = out.get(a.classification.action.value, 0) + 1
        return out

    naive_counts, canonical_counts = counts(naive), counts(canonical)
    return LabelDelta(
        period_end=current.period_end,
        naive_counts=naive_counts,
        canonical_counts=canonical_counts,
        fabricated_enters_removed=max(
            0,
            naive_counts.get(ObservedAction.enter.value, 0)
            - canonical_counts.get(ObservedAction.enter.value, 0),
        ),
    )


__all__ = [
    "ActionBasis",
    "ReplayView",
    "BuiltAction",
    "LabelDelta",
    "build_episode",
    "classify_quarter_pair",
    "measure_label_change",
]
