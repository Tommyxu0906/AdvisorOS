"""Investor profile domain models.

Everything here is plain data with validation. No analytics, no LLM, no I/O.

**Scope: the portfolio, and only the portfolio.** This product advises on an investment book. It
does not ask about mortgages, credit cards, household expenses, or income, and it must not — a
system that collects a person's whole balance sheet in order to comment on their equity
allocation has taken on a much larger duty of care than it can discharge, and most of what it
collected would never be used.

What survives is what actually changes an investment recommendation:

    age                   a rough proxy for how long the money can stay invested
    horizon_years         when it is needed, which is the real constraint
    risk_tolerance        how much drawdown is acceptable on the way
    investable_cash       what is available to buy with, so "add to X" can be checked
    experience            whether an explanation should assume any vocabulary

`investable_cash` is deliberately named for what it is: money in the brokerage account that
could be deployed. It is not a household cash position, and nothing here asks where it came
from.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, field_validator


class RiskTolerance(str, Enum):
    conservative = "conservative"
    moderate_conservative = "moderate_conservative"
    moderate = "moderate"
    moderate_aggressive = "moderate_aggressive"
    aggressive = "aggressive"

    @property
    def score(self) -> float:
        """0.0 (most conservative) .. 1.0 (most aggressive)."""
        return {
            RiskTolerance.conservative: 0.0,
            RiskTolerance.moderate_conservative: 0.25,
            RiskTolerance.moderate: 0.5,
            RiskTolerance.moderate_aggressive: 0.75,
            RiskTolerance.aggressive: 1.0,
        }[self]


class LifeStage(str, Enum):
    early_career = "early_career"
    accumulation = "accumulation"
    peak_earning = "peak_earning"
    pre_retirement = "pre_retirement"
    retirement = "retirement"


class AccountType(str, Enum):
    taxable = "taxable"
    traditional_401k = "traditional_401k"
    roth_401k = "roth_401k"
    traditional_ira = "traditional_ira"
    roth_ira = "roth_ira"
    hsa = "hsa"
    cash = "cash"
    other = "other"

    @property
    def is_tax_advantaged(self) -> bool:
        return self not in (AccountType.taxable, AccountType.cash, AccountType.other)


class HorizonBand(str, Enum):
    """When the money is needed. The single most load-bearing input this product takes."""

    near = "near"  # under 3 years
    medium = "medium"  # 3 to 10
    long = "long"  # over 10

    @property
    def label(self) -> str:
        return {
            HorizonBand.near: "needed within three years",
            HorizonBand.medium: "needed in three to ten years",
            HorizonBand.long: "not needed for over ten years",
        }[self]


class FinancialProfile(BaseModel):
    """What the product needs to know to advise on an investment book, and nothing more.

    Named `FinancialProfile` for continuity with the rest of the codebase; its scope is an
    investor, not a household. See the module docstring on why the balance sheet is absent.
    """

    model_config = ConfigDict(extra="forbid")

    age: int = Field(ge=16, le=120)
    currency: str = Field(default="USD", min_length=3, max_length=3)

    horizon_years: float = Field(
        default=10.0,
        ge=0,
        le=60,
        description="When this money is needed. Drives the only house-level hard constraint.",
    )
    investable_cash: float = Field(
        default=0.0,
        ge=0,
        description=(
            "Cash in the account available to deploy. Not a household cash position — nothing "
            "here asks where it came from or what else it is for."
        ),
    )

    risk_tolerance: RiskTolerance = RiskTolerance.moderate
    self_reported_experience: float = Field(
        default=0.5, ge=0, le=1, description="0 = complete novice, 1 = professional"
    )
    notes: str = Field(default="", max_length=4000)

    @field_validator("currency")
    @classmethod
    def _upper_currency(cls, v: str) -> str:
        return v.upper()

    @property
    def horizon_band(self) -> HorizonBand:
        if self.horizon_years < 3:
            return HorizonBand.near
        if self.horizon_years <= 10:
            return HorizonBand.medium
        return HorizonBand.long

    @property
    def life_stage(self) -> LifeStage:
        """Age alone. It used to consult retirement goals, which this product no longer asks for.

        Kept because it still shapes how an explanation is pitched, not because it is a strong
        signal — a 60-year-old investing money they will not touch for twenty years is in a
        different position from one spending it next year, and `horizon_band` is what carries
        that distinction.
        """
        if self.age >= 67:
            return LifeStage.retirement
        if self.age >= 55:
            return LifeStage.pre_retirement
        if self.age >= 45:
            return LifeStage.peak_earning
        if self.age >= 30:
            return LifeStage.accumulation
        return LifeStage.early_career
