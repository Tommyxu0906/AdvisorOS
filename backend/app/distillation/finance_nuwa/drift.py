"""What an investor actually did, separated from what the market did to them.

A position going from 10% of a portfolio to 13% looks like conviction and is very often nothing
of the kind — the stock went up and the manager never touched it. Treating that as a purchase
would teach a persona a decision its subject never made, and the error is systematic rather than
random: it manufactures buying in exactly the names that rose and selling in the names that
fell, which is momentum-chasing behaviour attributed to someone who may have been doing the
opposite. Any distillation built on raw weight changes learns that artifact.

So the classification here always answers a counterfactual: **what would the weights have been
if they had done nothing?** The gap between that and the observed weights is the part that
required a decision.

Two ways to answer it, and the difference between them matters enough to be a field on the
result rather than an implementation detail:

**From share counts — exact.** If the position went from 1.2M shares to 900k shares, they sold.
No inference, no market assumption, nothing to get wrong. Institutional holdings disclosures
report share counts, so this is the usual case for a manager with public filings, and it is
strictly better than anything derived from weights.

**From values, drift-adjusted — inferred.** When only market values are known, each position is
grown by its own return over the period, the no-trade portfolio is rebuilt from those, and the
observed weight is compared against that. This is a real inference with real error: it inherits
whatever error is in the return series, and it cannot see a purchase exactly offset by a price
fall. It is reported as inferred so that downstream scoring can weight it accordingly.

The one trap worth naming: **splits**. Shares doubling overnight is not a purchase, and a
disclosure that is not split-adjusted will read as one. `split_factor` exists so a caller can
say so; `detect_suspected_split` exists because callers forget.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ObservedAction(str, Enum):
    """What the investor did to one position over one period."""

    enter = "enter"
    """Opened a position that did not exist before."""

    increase = "increase"
    """Added beyond what price movement alone would explain."""

    hold = "hold"
    """Left it alone. The weight may still have moved a great deal."""

    reduce = "reduce"
    """Sold part of it."""

    exit = "exit"
    """Sold all of it."""

    @property
    def is_active(self) -> bool:
        """Whether a decision was taken. `hold` is a decision too, but not a transaction."""
        return self is not ObservedAction.hold

    @property
    def direction(self) -> int:
        """+1 buying, -1 selling, 0 neither. Used by ranking metrics."""
        if self in (ObservedAction.enter, ObservedAction.increase):
            return 1
        if self in (ObservedAction.reduce, ObservedAction.exit):
            return -1
        return 0


class ActionBasis(str, Enum):
    """How confidently the action is known. Not every observation is equal evidence."""

    share_count = "share_count"
    """Derived from share counts. A fact, not a reading."""

    drift_adjusted_value = "drift_adjusted_value"
    """Inferred by comparing observed weights against a no-trade counterfactual."""

    raw_value = "raw_value"
    """Values with no return data to adjust by. Weak — price movement is indistinguishable
    from trading, so only large moves are classified and everything else is `hold`."""

    stated = "stated"
    """The subject said what they did. No position data behind it."""

    @property
    def is_exact(self) -> bool:
        return self is ActionBasis.share_count


# A share count that moved by less than this is treated as unchanged. Institutional disclosures
# report exact shares, so in principle any change is real — but rounding, restatements, and
# partial-period reporting all produce sub-percent wobble that is not a decision. An AdvisorOS
# convention, not a finding.
SHARE_TOLERANCE = 0.005

# Active weight delta, in weight units, below which a move is treated as drift rather than
# decision. Wider than the share tolerance because the drift estimate carries the error of the
# return series it was built from.
WEIGHT_TOLERANCE = 0.005

# Without returns, only moves too large for plausible price action are called decisions. A
# quarter in which a position's weight moves by half its own size is not usually a price move.
RAW_VALUE_TOLERANCE = 0.50


class ActionClassification(BaseModel):
    """One position, one period, and the arithmetic behind the verdict.

    Carries its own reasoning because these become training signal: a scorer that cannot tell an
    exact share-count observation from a weak value-only inference will weight a guess as
    heavily as a fact.
    """

    model_config = ConfigDict(extra="forbid")

    symbol: str = Field(min_length=1)
    action: ObservedAction
    basis: ActionBasis

    start_weight: float | None = None
    end_weight: float | None = None
    drift_weight: float | None = Field(
        default=None, description="Weight it would have had with no trading at all"
    )
    active_delta: float | None = Field(
        default=None, description="end_weight - drift_weight: the part that took a decision"
    )
    share_change_pct: float | None = None

    suspected_split: bool = Field(
        default=False,
        description="Share count moved by a near-integer ratio with little value change",
    )
    explanation: str = ""

    @property
    def is_trustworthy(self) -> bool:
        """Whether this is worth training on without a human looking at it first."""
        return self.basis.is_exact and not self.suspected_split


class PositionSnapshot(BaseModel):
    """One position as disclosed at one date."""

    model_config = ConfigDict(extra="forbid")

    symbol: str = Field(min_length=1)
    market_value: float = Field(ge=0)
    shares: float | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _consistent(self) -> PositionSnapshot:
        if self.shares == 0 and self.market_value > 0:
            raise ValueError(f"{self.symbol}: zero shares cannot hold a positive market value")
        return self


def classify_position(
    symbol: str,
    *,
    start: PositionSnapshot | None,
    end: PositionSnapshot | None,
    period_return: float | None = None,
    start_total: float | None = None,
    end_total: float | None = None,
    drift_total: float | None = None,
    split_factor: float = 1.0,
) -> ActionClassification:
    """Classify one position's change, preferring share counts over any value-based inference.

    `drift_total` is the no-trade total for the whole portfolio and must be computed across every
    position — a single position's drift weight depends on what the *rest* of the portfolio did,
    so it cannot be derived from this position alone. `classify_portfolio` handles that; this
    function is exposed mainly so the arithmetic can be tested one position at a time.
    """
    held_before = start is not None and start.market_value > 0
    held_after = end is not None and end.market_value > 0

    if not held_before and not held_after:
        return ActionClassification(
            symbol=symbol,
            action=ObservedAction.hold,
            basis=ActionBasis.raw_value,
            explanation="Not held at either date.",
        )

    start_weight = _weight(start, start_total)
    end_weight = _weight(end, end_total)

    if not held_before:
        return ActionClassification(
            symbol=symbol,
            action=ObservedAction.enter,
            basis=ActionBasis.share_count
            if end and end.shares is not None
            else ActionBasis.raw_value,
            start_weight=0.0,
            end_weight=end_weight,
            explanation="Position did not exist at the start of the period.",
        )
    if not held_after:
        return ActionClassification(
            symbol=symbol,
            action=ObservedAction.exit,
            basis=ActionBasis.share_count
            if start and start.shares is not None
            else ActionBasis.raw_value,
            start_weight=start_weight,
            end_weight=0.0,
            explanation="Position was fully disposed of during the period.",
        )

    assert start is not None and end is not None  # both held; narrows the type

    # --- exact, when the filing reports share counts -------------------------------------
    if start.shares is not None and end.shares is not None and start.shares > 0:
        adjusted_start = start.shares * split_factor
        change = (end.shares - adjusted_start) / adjusted_start
        split = detect_suspected_split(start, end, split_factor=split_factor)

        if abs(change) <= SHARE_TOLERANCE:
            action, reason = ObservedAction.hold, "Share count unchanged"
        elif change > 0:
            action, reason = ObservedAction.increase, f"Share count up {change:.1%}"
        else:
            action, reason = ObservedAction.reduce, f"Share count down {abs(change):.1%}"

        note = (
            " — but the ratio looks like an unadjusted split, so this may be no decision at all"
            if split
            else ""
        )
        return ActionClassification(
            symbol=symbol,
            action=action,
            basis=ActionBasis.share_count,
            start_weight=start_weight,
            end_weight=end_weight,
            share_change_pct=change,
            suspected_split=split,
            explanation=f"{reason}{note}.",
        )

    # --- inferred, by removing the drift -------------------------------------------------
    if period_return is not None and drift_total and drift_total > 0:
        drifted_value = start.market_value * (1.0 + period_return)
        drift_weight = drifted_value / drift_total
        active_delta = (end_weight or 0.0) - drift_weight

        if abs(active_delta) <= WEIGHT_TOLERANCE:
            action = ObservedAction.hold
            reason = (
                f"Weight moved {(start_weight or 0):.1%} to {(end_weight or 0):.1%}, but a "
                f"no-trade portfolio would have landed at {drift_weight:.1%} — price movement, "
                "not a decision"
            )
        elif active_delta > 0:
            action = ObservedAction.increase
            reason = (
                f"Weight is {active_delta:+.1%} above the {drift_weight:.1%} that doing nothing "
                "would have produced"
            )
        else:
            action = ObservedAction.reduce
            reason = (
                f"Weight is {active_delta:+.1%} below the {drift_weight:.1%} that doing nothing "
                "would have produced"
            )

        return ActionClassification(
            symbol=symbol,
            action=action,
            basis=ActionBasis.drift_adjusted_value,
            start_weight=start_weight,
            end_weight=end_weight,
            drift_weight=drift_weight,
            active_delta=active_delta,
            explanation=reason + ".",
        )

    # --- weak, when nothing separates price from trading ---------------------------------
    change = (end.market_value - start.market_value) / start.market_value
    if abs(change) <= RAW_VALUE_TOLERANCE:
        action = ObservedAction.hold
        reason = (
            f"Value moved {change:+.0%} with no return data to attribute it to. Too small to "
            "distinguish trading from price movement, so it is recorded as no decision"
        )
    else:
        action = ObservedAction.increase if change > 0 else ObservedAction.reduce
        reason = (
            f"Value moved {change:+.0%}, beyond what price movement alone plausibly explains — "
            "but with no return data this remains an inference"
        )
    return ActionClassification(
        symbol=symbol,
        action=action,
        basis=ActionBasis.raw_value,
        start_weight=start_weight,
        end_weight=end_weight,
        explanation=reason + ".",
    )


def classify_portfolio(
    start: list[PositionSnapshot],
    end: list[PositionSnapshot],
    *,
    returns: dict[str, float] | None = None,
    split_factors: dict[str, float] | None = None,
) -> list[ActionClassification]:
    """Classify every position across two disclosures.

    The no-trade total is computed once across all positions before anything is classified,
    because a single name's drift weight depends on how the *rest* of the portfolio moved. Doing
    this per-position — the obvious shortcut — silently assumes every other holding was flat, and
    in a quarter when the market moved, that error lands on every name at once.
    """
    returns = returns or {}
    split_factors = split_factors or {}

    by_symbol_start = {p.symbol: p for p in start}
    by_symbol_end = {p.symbol: p for p in end}

    start_total = sum(p.market_value for p in start)
    end_total = sum(p.market_value for p in end)
    drift_total = sum(p.market_value * (1.0 + returns.get(p.symbol, 0.0)) for p in start)

    return [
        classify_position(
            symbol,
            start=by_symbol_start.get(symbol),
            end=by_symbol_end.get(symbol),
            period_return=returns.get(symbol),
            start_total=start_total or None,
            end_total=end_total or None,
            drift_total=drift_total or None,
            split_factor=split_factors.get(symbol, 1.0),
        )
        for symbol in sorted(set(by_symbol_start) | set(by_symbol_end))
    ]


def detect_suspected_split(
    start: PositionSnapshot, end: PositionSnapshot, *, split_factor: float = 1.0
) -> bool:
    """Flag a share-count change that looks like a corporate action rather than a trade.

    The signature is a near-integer share ratio with the position's value roughly intact: 2x the
    shares at half the price is the same money and no decision. Only a heuristic — a manager who
    genuinely doubles a position is indistinguishable from a 2:1 split on share count alone,
    which is why the result is a flag for review rather than a reclassification.
    """
    if split_factor != 1.0:
        return False  # the caller has already told us about it
    if start.shares is None or end.shares is None or start.shares <= 0:
        return False
    if start.market_value <= 0 or end.market_value <= 0:
        return False

    share_ratio = end.shares / start.shares
    value_ratio = end.market_value / start.market_value
    if share_ratio < 1.5 and share_ratio > 0.51:
        return False

    nearest = round(share_ratio) if share_ratio >= 1 else 1 / round(1 / share_ratio)
    near_integer = nearest > 0 and abs(share_ratio - nearest) / nearest < 0.02
    value_roughly_intact = 0.8 <= value_ratio <= 1.25
    return near_integer and value_roughly_intact


def _weight(snapshot: PositionSnapshot | None, total: float | None) -> float | None:
    if snapshot is None or not total:
        return None
    return snapshot.market_value / total
