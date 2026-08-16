"""What was knowable about a position when the decision window opened.

Every feature here comes out of the filings themselves, and that is a deliberate choice rather
than a limitation. Each disclosed position carries both a market value and a share count, so
`value / shares` is an implied price — which means a per-security price series, a portfolio
weight series, and a concentration series all fall out of data already on disk. No ticker
mapping, no price vendor, and no third source whose as-of semantics have to be re-verified.

That matters more than the convenience. Every external series introduces a new way to leak the
future: a vendor's "close on 2016-03-31" is knowable on 2016-03-31, but a restated, adjusted, or
back-filled series is not, and the difference is invisible once it is in a dataframe. Features
derived from filings inherit the filing's own disclosure date, which the pipeline already tracks
and already tests.

The cost is honest and worth stating: quarterly resolution. There is no intra-quarter drawdown
here, and a position that fell 40% in April and recovered by June looks untouched. For learning
*decision* behaviour that is largely acceptable, because the decision itself is only resolvable
to a quarter anyway — but it does mean this cannot see the moment someone panicked.

Fundamentals are deliberately absent. Earnings are knowable on the filing date rather than the
period end, and getting that wrong is a subtler leak than anything 13F offers; they come later,
with their own publication-date handling, or not at all.
"""

from __future__ import annotations

import math
from datetime import date

from pydantic import BaseModel, ConfigDict, Field

from app.distillation.finance_nuwa.identity import SecurityKey
from app.distillation.finance_nuwa.lineage import PublicQuarterView

# Bumped when a feature is added, removed, or redefined. Two datasets with different values
# here are not comparable, however similar their columns look.
FEATURE_SCHEMA_VERSION = "pit-features-v1"


class PositionFeatures(BaseModel):
    """One position, as it appeared in the last book the observer could actually see.

    Every field is `| None` because a position new to the book has no history, and a fabricated
    zero would read as "flat" — which is a claim, and a wrong one.
    """

    model_config = ConfigDict(extra="forbid")

    security: SecurityKey
    as_of: date = Field(description="Cutoff these were computed at; nothing later contributed")
    source_period_end: date = Field(description="Period end of the book they came from")

    # --- position context
    weight: float | None = None
    rank: int | None = Field(default=None, description="1 is the largest holding")
    quarters_held: int | None = None

    # --- price context, from implied prices in the filings
    trailing_return_1q: float | None = None
    trailing_return_4q: float | None = None
    drawdown_from_peak: float | None = Field(
        default=None, description="Implied price against its highest disclosed level, <= 0"
    )
    relative_return_4q: float | None = Field(
        default=None, description="Position return minus the book's return over the same span"
    )

    # --- portfolio context
    portfolio_positions: int | None = None
    top5_concentration: float | None = None
    hhi: float | None = None

    def as_dict(self) -> dict[str, float | int | None]:
        """Flat view for a model. Deliberately excludes the identifiers and dates."""
        return {
            "weight": self.weight,
            "rank": self.rank,
            "quarters_held": self.quarters_held,
            "trailing_return_1q": self.trailing_return_1q,
            "trailing_return_4q": self.trailing_return_4q,
            "drawdown_from_peak": self.drawdown_from_peak,
            "relative_return_4q": self.relative_return_4q,
            "portfolio_positions": self.portfolio_positions,
            "top5_concentration": self.top5_concentration,
            "hhi": self.hhi,
        }


def implied_price(quarter: PublicQuarterView, security: SecurityKey) -> float | None:
    """Value over shares, which is the price the filing implies for that security."""
    for position in quarter.positions:
        if position.identity.key == security and position.shares > 0:
            return position.market_value / position.shares
    return None


def build_features(
    visible_history: list[PublicQuarterView],
    security: SecurityKey,
    *,
    as_of: date,
) -> PositionFeatures:
    """Features from the books an observer could see at `as_of`, newest last.

    Takes `PublicQuarterView` and not `CanonicalQuarter`, so the unfiltered book cannot be
    passed by accident. An earlier version accepted the canonical quarter and trusted the caller
    to have filtered it; the caller filtered *quarters* by filing date but not *positions* by
    disclosure date, and confidential holdings reached weight, rank, HHI and hold matching
    without ever appearing in an episode input — invisible to a lookahead check that only
    inspects episode inputs.
    """
    if not visible_history:
        return PositionFeatures(security=security, as_of=as_of, source_period_end=as_of)

    latest = visible_history[-1]
    ordered = sorted(latest.positions, key=lambda p: p.market_value, reverse=True)
    total = sum(p.market_value for p in ordered)

    held = next((p for p in ordered if p.identity.key == security), None)
    weight = held.market_value / total if held is not None and total > 0 else None
    rank = next(
        (i + 1 for i, p in enumerate(ordered) if p.identity.key == security),
        None,
    )

    prices = [
        price
        for quarter in visible_history
        if (price := implied_price(quarter, security)) is not None
    ]

    return PositionFeatures(
        security=security,
        as_of=as_of,
        source_period_end=latest.period_end,
        weight=weight,
        rank=rank,
        quarters_held=_consecutive_tail(visible_history, security) or None,
        trailing_return_1q=_return_over(prices, 1),
        trailing_return_4q=_return_over(prices, 4),
        drawdown_from_peak=_drawdown(prices),
        relative_return_4q=_relative_return(visible_history, security, 4),
        portfolio_positions=len(ordered) or None,
        top5_concentration=(
            sum(p.market_value for p in ordered[:5]) / total if total > 0 else None
        ),
        hhi=sum((p.market_value / total) ** 2 for p in ordered) if total > 0 else None,
    )


def _return_over(prices: list[float], quarters: int) -> float | None:
    """Return across `quarters` of disclosed prices, or None when the history is too short.

    None rather than a partial-window figure: a "four-quarter return" computed over two quarters
    is a different quantity wearing the same name, and a model has no way to tell.
    """
    if len(prices) <= quarters or prices[-1 - quarters] <= 0:
        return None
    return prices[-1] / prices[-1 - quarters] - 1.0


def _drawdown(prices: list[float]) -> float | None:
    """Current implied price against the highest ever disclosed. Zero or negative."""
    if len(prices) < 2:
        return None
    peak = max(prices)
    return prices[-1] / peak - 1.0 if peak > 0 else None


def _relative_return(
    history: list[PublicQuarterView], security: SecurityKey, quarters: int
) -> float | None:
    """Position return less the book's own return, so a rising tide is netted out.

    Holding through a 30% fall means something different when everything fell 30% and when
    nothing else did, and the absolute figure cannot distinguish them.
    """
    prices = [p for q in history if (p := implied_price(q, security)) is not None]
    position = _return_over(prices, quarters)
    if position is None or len(history) <= quarters:
        return None

    then, now = history[-1 - quarters], history[-1]
    book = _book_return(then, now)
    return position - book if book is not None else None


def _book_return(then: PublicQuarterView, now: PublicQuarterView) -> float | None:
    """Value-weighted implied-price return of everything held in both books.

    Restricted to the intersection on purpose: comparing total values would mostly measure
    positions opening and closing, which is turnover rather than performance.
    """
    shared = {p.identity.key for p in then.positions} & {p.identity.key for p in now.positions}
    if not shared:
        return None

    weighted, weight_total = 0.0, 0.0
    for position in then.positions:
        security = position.identity.key
        if security not in shared:
            continue
        start, end = implied_price(then, security), implied_price(now, security)
        if start is None or end is None or start <= 0:
            continue
        weighted += position.market_value * (end / start - 1.0)
        weight_total += position.market_value

    return weighted / weight_total if weight_total > 0 else None


def _consecutive_tail(history: list[PublicQuarterView], security: SecurityKey) -> int:
    """How many consecutive recent quarters this has been held.

    Counted backwards from the latest book and stopping at the first gap, because a position
    that was held, sold, and re-bought has not been held throughout — and the whole point of the
    feature is duration of conviction.
    """
    count = 0
    for quarter in reversed(history):
        if any(p.identity.key == security for p in quarter.positions):
            count += 1
        else:
            break
    return count


def regime_features(visible_history: list[PublicQuarterView]) -> dict[str, float | None]:
    """Coarse market context, derived from the book rather than from an index.

    A proxy and labelled as one: the return of what this manager held is not the market's
    return, and it is computed from the public view so the regime bucket cannot be moved by a
    holding nobody could see. It is enough to separate "everything was falling" from "only this was", which is the
    distinction matched controls actually need, and it introduces no external series whose
    as-of semantics would have to be re-verified.
    """
    if len(visible_history) < 2:
        return {"book_return_1q": None, "book_return_4q": None, "book_drawdown": None}

    latest = visible_history[-1]
    totals = [sum(p.market_value for p in q.positions) for q in visible_history]
    peak = max(totals) if totals else 0.0

    return {
        "book_return_1q": _book_return(visible_history[-2], latest),
        "book_return_4q": (
            _book_return(visible_history[-5], latest) if len(visible_history) >= 5 else None
        ),
        "book_drawdown": (totals[-1] / peak - 1.0) if peak > 0 else None,
    }


def regime_bucket(book_return_1q: float | None) -> str:
    """Three buckets, used only to match a hold against a comparable trade.

    Coarse on purpose. Finer buckets would leave too few candidates in each cell to match on,
    and the thresholds would be inventing precision the quarterly series cannot support.
    """
    if book_return_1q is None or math.isnan(book_return_1q):
        return "unknown"
    if book_return_1q <= -0.08:
        return "falling"
    if book_return_1q >= 0.08:
        return "rising"
    return "flat"
