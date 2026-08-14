"""Portfolio domain models."""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.domain.profile import AccountType


class AssetClass(str, Enum):
    us_equity = "us_equity"
    intl_developed_equity = "intl_developed_equity"
    emerging_equity = "emerging_equity"
    bonds = "bonds"
    tips = "tips"
    reit = "reit"
    commodities = "commodities"
    crypto = "crypto"
    cash = "cash"
    other = "other"

    @property
    def is_equity_like(self) -> bool:
        return self in (
            AssetClass.us_equity,
            AssetClass.intl_developed_equity,
            AssetClass.emerging_equity,
            AssetClass.reit,
            AssetClass.crypto,
        )

    @property
    def is_defensive(self) -> bool:
        return self in (AssetClass.bonds, AssetClass.tips, AssetClass.cash)


class Holding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    symbol: str = Field(min_length=1, max_length=32)
    name: str = ""
    asset_class: AssetClass = AssetClass.other
    # Optional, and never the authoritative figure: `market_value` is. A holding with no share
    # count — a private business, a savings account, a symbol no provider covers — has to keep
    # working, so quantity is what lets the UI recompute value from a live price when it can,
    # not a second source of truth for what the position is worth.
    quantity: float | None = Field(default=None, ge=0)
    market_value: float = Field(ge=0)
    cost_basis: float | None = Field(default=None, ge=0)
    account_type: AccountType = AccountType.taxable
    expense_ratio: float | None = Field(default=None, ge=0, le=0.1)

    @property
    def unrealized_gain(self) -> float | None:
        if self.cost_basis is None:
            return None
        return self.market_value - self.cost_basis


class PriceSeries(BaseModel):
    """Periodic returns for a symbol, used for volatility/drawdown/correlation.

    `returns` are simple periodic returns (0.01 == +1%), oldest first.
    """

    model_config = ConfigDict(extra="forbid")

    symbol: str
    periods_per_year: int = Field(default=12, ge=1, le=366)
    returns: list[float] = Field(default_factory=list)

    @model_validator(mode="after")
    def _sane_returns(self) -> PriceSeries:
        for r in self.returns:
            if r <= -1.0:
                raise ValueError(f"{self.symbol}: period return {r} implies total loss or worse")
        return self


class Portfolio(BaseModel):
    model_config = ConfigDict(extra="forbid")

    holdings: list[Holding] = Field(default_factory=list)
    price_series: list[PriceSeries] = Field(default_factory=list)
    currency: str = Field(default="USD", min_length=3, max_length=3)

    @property
    def total_value(self) -> float:
        return sum(h.market_value for h in self.holdings)

    def series_for(self, symbol: str) -> PriceSeries | None:
        return next((s for s in self.price_series if s.symbol == symbol), None)
