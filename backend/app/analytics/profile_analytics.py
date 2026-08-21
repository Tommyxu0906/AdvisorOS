"""Deterministic investor analytics.

No LLM. No network. Given an investor profile and their book, produce the numbers and the need
vector that drive advisor routing and prompt content.

**Rewritten when the product narrowed to the portfolio.** This module used to compute savings
rate, debt-service ratio, emergency-fund months, and weighted average APR — fifteen fields of
household finance, most of which never reached a recommendation about which stocks to hold. What
is left is what actually changes an investment decision:

    horizon and how much risk the book is taking against it
    concentration
    what cash is available to deploy
    how much of the book sits in accounts where a sale is a taxable event

The need vector keeps its shape, because advisor routing is arithmetic over it and that seam is
still the right one. Only `horizon_pressure` changed meaning, and it changed because a platform
that does not ask about debt has no business routing on debt.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from app.domain.needs import NeedVector
from app.domain.portfolio import Portfolio
from app.domain.profile import FinancialProfile, HorizonBand, LifeStage

# Above this single-name weight, concentration is a finding rather than a preference.
CONCENTRATION_NOTABLE = 0.25
# A book this equity-heavy against a near-term need is the one hard constraint the house keeps.
NEAR_TERM_EQUITY_CEILING = 0.4

_GROWTH_CLASSES = {"us_equity", "intl_developed_equity", "emerging_equity", "crypto", "reit"}


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


class ProfileAnalytics(BaseModel):
    """Everything derivable from an investor profile and their book with arithmetic alone."""

    model_config = ConfigDict(extra="forbid")

    life_stage: LifeStage
    horizon_years: float
    horizon_band: HorizonBand

    portfolio_value: float
    investable_cash: float = Field(description="Account cash available to deploy")
    total_capital: float = Field(description="Portfolio value plus deployable cash")

    growth_asset_share: float = Field(
        default=0.0, description="Share of the book in equity-like assets, 0..1"
    )
    cash_share: float = Field(default=0.0, description="Deployable cash over total capital")
    taxable_share: float = Field(
        default=0.0, description="Share of the book where a sale is a taxable event"
    )
    largest_position_weight: float = Field(default=0.0)
    position_count: int = 0

    need_vector: NeedVector
    notable_findings: list[str] = Field(default_factory=list)

    def summary_lines(self) -> list[str]:
        """Compact, prompt-ready facts. Deterministic ordering."""
        lines = [
            f"Age: {self.life_stage.value.replace('_', ' ')} ({self.horizon_band.label})",
            f"Portfolio: {self.portfolio_value:,.0f} across {self.position_count} positions",
            f"Deployable cash: {self.investable_cash:,.0f} ({self.cash_share:.0%} of capital)",
            f"Growth assets: {self.growth_asset_share:.0%} of the book",
        ]
        if self.largest_position_weight:
            lines.append(f"Largest position: {self.largest_position_weight:.0%}")
        if self.taxable_share:
            lines.append(f"Held in taxable accounts: {self.taxable_share:.0%}")
        return lines


def analyze_profile(
    profile: FinancialProfile, portfolio: Portfolio | None = None
) -> ProfileAnalytics:
    """Compute the deterministic investor metrics and the need vector."""
    holdings = portfolio.holdings if portfolio else []
    portfolio_value = sum(h.market_value for h in holdings)
    cash = profile.investable_cash
    total_capital = portfolio_value + cash

    # Aggregated by symbol, never per holding: the same stock in two accounts is one investment
    # decision. `portfolio_analytics` uses the same convention, and two different answers to
    # "how concentrated is this" would be worse than either.
    by_symbol: dict[str, float] = {}
    for holding in holdings:
        symbol = holding.symbol.strip().upper()
        if symbol:
            by_symbol[symbol] = by_symbol.get(symbol, 0.0) + holding.market_value

    largest = (
        max(by_symbol.values()) / portfolio_value if portfolio_value > 0 and by_symbol else 0.0
    )
    growth = (
        sum(h.market_value for h in holdings if h.asset_class.value in _GROWTH_CLASSES)
        / portfolio_value
        if portfolio_value > 0
        else 0.0
    )
    taxable = (
        sum(h.market_value for h in holdings if h.account_type.value == "taxable") / portfolio_value
        if portfolio_value > 0
        else 0.0
    )
    cash_share = cash / total_capital if total_capital > 0 else 0.0

    need = _need_vector(profile, growth, largest, taxable, cash_share, len(by_symbol))

    return ProfileAnalytics(
        life_stage=profile.life_stage,
        horizon_years=profile.horizon_years,
        horizon_band=profile.horizon_band,
        portfolio_value=round(portfolio_value, 2),
        investable_cash=round(cash, 2),
        total_capital=round(total_capital, 2),
        growth_asset_share=round(growth, 4),
        cash_share=round(cash_share, 4),
        taxable_share=round(taxable, 4),
        largest_position_weight=round(largest, 4),
        position_count=len(by_symbol),
        need_vector=need,
        notable_findings=_notable_findings(profile, growth, largest, len(by_symbol)),
    )


def _need_vector(
    profile: FinancialProfile,
    growth_share: float,
    largest: float,
    taxable_share: float,
    cash_share: float,
    position_count: int,
) -> NeedVector:
    """Where this investor most needs a view. Routing consumes this and nothing else."""
    # The nearer the money is needed and the more risk the book carries, the more this matters.
    horizon = 0.0
    if profile.horizon_band is HorizonBand.near:
        horizon = 0.5 + 0.5 * growth_share
    elif profile.horizon_band is HorizonBand.medium:
        horizon = 0.25 * growth_share

    # Cash that cannot cover a sensible purchase, or a book with nothing liquid in it.
    liquidity = _clamp(1.0 - cash_share * 5) * (
        0.6 if profile.horizon_band is HorizonBand.near else 0.3
    )

    concentration = _clamp((largest - 0.1) / 0.4)

    # A concentrated growth book is where valuation matters most.
    valuation = _clamp(growth_share * 0.6 + concentration * 0.4)

    # Inexperience and a concentrated book both push this up; so does a near-term need, because
    # that is when people sell at the worst moment.
    behavioral = _clamp(
        (1.0 - profile.self_reported_experience) * 0.5
        + concentration * 0.3
        + (0.2 if profile.horizon_band is HorizonBand.near else 0.0)
    )

    # Selling in a taxable account has a cost that selling in an IRA does not.
    tax = _clamp(taxable_share * (0.4 + 0.6 * concentration))

    # A long horizon is where compounding and staying invested dominate.
    longevity = _clamp(profile.horizon_years / 30.0)

    if position_count and position_count < 4:
        concentration = _clamp(concentration + 0.15)

    return NeedVector(
        liquidity_risk=_clamp(liquidity),
        horizon_pressure=_clamp(horizon),
        concentration_risk=_clamp(concentration),
        valuation_sensitivity=_clamp(valuation),
        behavioral_risk=_clamp(behavioral),
        tax_complexity=_clamp(tax),
        longevity_risk=_clamp(longevity),
    )


def _notable_findings(
    profile: FinancialProfile, growth_share: float, largest: float, position_count: int
) -> list[str]:
    """Plain sentences a prompt can carry. Stated as observations, never as instructions."""
    out: list[str] = []

    if profile.horizon_band is HorizonBand.near and growth_share > NEAR_TERM_EQUITY_CEILING:
        out.append(
            f"Money is needed within three years while {growth_share:.0%} of the book sits in "
            "growth assets."
        )
    if largest > CONCENTRATION_NOTABLE:
        out.append(f"The largest single position is {largest:.0%} of the book.")
    if position_count and position_count < 4:
        out.append(
            f"The book holds {position_count} position{'s' if position_count != 1 else ''}, which "
            "is a deliberate choice at best and an accident at worst."
        )
    if profile.investable_cash <= 0:
        out.append("There is no deployable cash, so anything bought has to be funded by a sale.")
    return out
