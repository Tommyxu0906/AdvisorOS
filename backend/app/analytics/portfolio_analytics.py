"""Deterministic portfolio analytics.

Weights, concentration, and — when return series are supplied — annualized return, volatility,
max drawdown, and a correlation matrix. Pure stdlib; no numpy.
"""

from __future__ import annotations

import math
from statistics import fmean, pstdev

from pydantic import BaseModel, ConfigDict, Field

from app.domain.portfolio import AssetClass, Portfolio, PriceSeries


class SeriesMetrics(BaseModel):
    model_config = ConfigDict(extra="forbid")

    symbol: str
    periods: int
    annualized_return: float
    annualized_volatility: float
    max_drawdown: float = Field(description="Most negative peak-to-trough decline, e.g. -0.34")
    best_period: float
    worst_period: float

    @property
    def sharpe_like(self) -> float:
        """Return / volatility. Not a true Sharpe ratio — no risk-free rate is assumed."""
        return (
            self.annualized_return / self.annualized_volatility
            if self.annualized_volatility
            else 0.0
        )


class PortfolioAnalytics(BaseModel):
    model_config = ConfigDict(extra="forbid")

    total_value: float
    holding_count: int
    weights: dict[str, float] = Field(default_factory=dict)
    asset_class_weights: dict[str, float] = Field(default_factory=dict)

    largest_holding_symbol: str = ""
    largest_weight: float = 0.0
    hhi: float = Field(default=0.0, description="Herfindahl-Hirschman index of holding weights")
    effective_holdings: float = Field(
        default=0.0, description="1/HHI — effective number of positions"
    )

    equity_share: float = 0.0
    defensive_share: float = 0.0

    weighted_expense_ratio: float | None = None
    unrealized_gain: float | None = None
    taxable_share: float = 0.0

    series_metrics: list[SeriesMetrics] = Field(default_factory=list)
    correlations: dict[str, dict[str, float]] = Field(default_factory=dict)
    portfolio_metrics: SeriesMetrics | None = Field(
        default=None, description="Weighted portfolio series, when every holding has a series"
    )

    def summary_lines(self) -> list[str]:
        lines = [
            f"Portfolio value: {self.total_value:,.0f} across {self.holding_count} positions",
            f"Largest position: {self.largest_holding_symbol} at {self.largest_weight:.1%}",
            f"Concentration (HHI): {self.hhi:.3f} — effective positions: {self.effective_holdings:.1f}",
            f"Equity-like share: {self.equity_share:.0%}; defensive share: {self.defensive_share:.0%}",
            f"Held in taxable accounts: {self.taxable_share:.0%}",
        ]
        if self.weighted_expense_ratio is not None:
            lines.append(f"Weighted expense ratio: {self.weighted_expense_ratio:.2%}")
        if self.portfolio_metrics:
            m = self.portfolio_metrics
            lines.append(
                f"Historical (from supplied series): {m.annualized_return:.1%} annualized return, "
                f"{m.annualized_volatility:.1%} volatility, {m.max_drawdown:.1%} max drawdown"
            )
        return lines


def analyze_portfolio(portfolio: Portfolio) -> PortfolioAnalytics:
    total = portfolio.total_value
    if total <= 0 or not portfolio.holdings:
        return PortfolioAnalytics(total_value=total, holding_count=len(portfolio.holdings))

    # Weights are aggregated by symbol before anything reads them. The same ticker legitimately
    # appears more than once — VTI held in both a taxable account and a Roth is ordinary — and
    # a plain dict comprehension keyed on symbol would keep only the last lot. That silently
    # broke three things at once: the weights no longer summed to 1, HHI and effective_holdings
    # were computed from the surviving fragment, and the per-holding loops below double-counted
    # the duplicated symbol because they looked up a shared weight once per lot.
    value_by_symbol: dict[str, float] = {}
    for h in portfolio.holdings:
        value_by_symbol[h.symbol] = value_by_symbol.get(h.symbol, 0.0) + h.market_value
    weights = {symbol: value / total for symbol, value in value_by_symbol.items()}

    class_weights: dict[str, float] = {}
    for h in portfolio.holdings:
        class_weights[h.asset_class.value] = (
            class_weights.get(h.asset_class.value, 0.0) + h.market_value / total
        )

    largest_symbol, largest_weight = max(weights.items(), key=lambda kv: kv[1])
    hhi = sum(w * w for w in weights.values())

    # These sum each holding's own share rather than a per-symbol weight, so a symbol split
    # across accounts contributes its true total exactly once.
    equity_share = sum(
        h.market_value / total for h in portfolio.holdings if h.asset_class.is_equity_like
    )
    defensive_share = sum(
        h.market_value / total for h in portfolio.holdings if h.asset_class.is_defensive
    )
    taxable_share = sum(
        h.market_value / total for h in portfolio.holdings if not h.account_type.is_tax_advantaged
    )

    with_er = [h for h in portfolio.holdings if h.expense_ratio is not None]
    er_weight_total = sum(h.market_value for h in with_er)
    weighted_er = (
        sum(h.market_value * (h.expense_ratio or 0.0) for h in with_er) / er_weight_total
        if with_er and er_weight_total > 0
        else None
    )

    gains = [h.unrealized_gain for h in portfolio.holdings if h.unrealized_gain is not None]
    unrealized = sum(gains) if gains else None

    series_metrics = [
        compute_series_metrics(s) for s in portfolio.price_series if len(s.returns) >= 2
    ]
    correlations = _correlation_matrix(portfolio.price_series)
    portfolio_series = _weighted_portfolio_series(portfolio, weights)
    portfolio_metrics = (
        compute_series_metrics(portfolio_series)
        if portfolio_series and len(portfolio_series.returns) >= 2
        else None
    )

    return PortfolioAnalytics(
        total_value=total,
        holding_count=len(portfolio.holdings),
        weights=weights,
        asset_class_weights=class_weights,
        largest_holding_symbol=largest_symbol,
        largest_weight=largest_weight,
        hhi=hhi,
        effective_holdings=1.0 / hhi if hhi > 0 else 0.0,
        equity_share=equity_share,
        defensive_share=defensive_share,
        weighted_expense_ratio=weighted_er,
        unrealized_gain=unrealized,
        taxable_share=taxable_share,
        series_metrics=series_metrics,
        correlations=correlations,
        portfolio_metrics=portfolio_metrics,
    )


def compute_series_metrics(series: PriceSeries) -> SeriesMetrics:
    rets = series.returns
    n = len(rets)
    ppy = series.periods_per_year

    growth = 1.0
    for r in rets:
        growth *= 1.0 + r
    years = n / ppy
    annualized_return = growth ** (1.0 / years) - 1.0 if years > 0 and growth > 0 else -1.0

    vol = pstdev(rets) * math.sqrt(ppy) if n >= 2 else 0.0

    peak = 1.0
    equity = 1.0
    max_dd = 0.0
    for r in rets:
        equity *= 1.0 + r
        peak = max(peak, equity)
        max_dd = min(max_dd, equity / peak - 1.0)

    return SeriesMetrics(
        symbol=series.symbol,
        periods=n,
        annualized_return=annualized_return,
        annualized_volatility=vol,
        max_drawdown=max_dd,
        best_period=max(rets),
        worst_period=min(rets),
    )


def _correlation_matrix(all_series: list[PriceSeries]) -> dict[str, dict[str, float]]:
    usable = [s for s in all_series if len(s.returns) >= 2]
    out: dict[str, dict[str, float]] = {}
    for a in usable:
        out[a.symbol] = {}
        for b in usable:
            out[a.symbol][b.symbol] = _pearson(a.returns, b.returns)
    return out


def _pearson(xs: list[float], ys: list[float]) -> float:
    n = min(len(xs), len(ys))
    if n < 2:
        return 0.0
    xs, ys = xs[-n:], ys[-n:]
    mx, my = fmean(xs), fmean(ys)
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys, strict=True))
    dx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    dy = math.sqrt(sum((y - my) ** 2 for y in ys))
    if dx == 0 or dy == 0:
        return 0.0
    return num / (dx * dy)


def _weighted_portfolio_series(
    portfolio: Portfolio, weights: dict[str, float]
) -> PriceSeries | None:
    """Build a synthetic portfolio return series, only if every holding has one."""
    series_by_symbol = {s.symbol: s for s in portfolio.price_series if len(s.returns) >= 2}
    if not series_by_symbol or any(h.symbol not in series_by_symbol for h in portfolio.holdings):
        return None

    length = min(len(series_by_symbol[h.symbol].returns) for h in portfolio.holdings)
    ppy = series_by_symbol[portfolio.holdings[0].symbol].periods_per_year
    if any(series_by_symbol[h.symbol].periods_per_year != ppy for h in portfolio.holdings):
        return None

    combined: list[float] = []
    for i in range(length):
        combined.append(
            sum(
                weights[h.symbol] * series_by_symbol[h.symbol].returns[-length:][i]
                for h in portfolio.holdings
            )
        )
    return PriceSeries(symbol="__PORTFOLIO__", periods_per_year=ppy, returns=combined)


def asset_class_summary(analytics: PortfolioAnalytics) -> str:
    if not analytics.asset_class_weights:
        return "No holdings supplied."
    ordered = sorted(analytics.asset_class_weights.items(), key=lambda kv: -kv[1])
    return ", ".join(f"{AssetClass(k).value} {v:.0%}" for k, v in ordered)
