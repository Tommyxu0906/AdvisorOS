"""Financial profile domain models.

Everything here is plain data with validation. No analytics, no LLM, no I/O.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


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


class GoalType(str, Enum):
    retirement = "retirement"
    home_purchase = "home_purchase"
    education = "education"
    emergency_fund = "emergency_fund"
    wealth_growth = "wealth_growth"
    income = "income"
    debt_payoff = "debt_payoff"
    other = "other"


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


class Income(BaseModel):
    model_config = ConfigDict(extra="forbid")

    annual_gross: float = Field(ge=0, description="Annual pre-tax income")
    annual_net: float | None = Field(default=None, ge=0, description="Annual after-tax income")
    stability: float = Field(
        default=0.8, ge=0, le=1, description="0 = highly variable, 1 = fully stable"
    )
    employer_match_pct: float = Field(
        default=0.0, ge=0, le=1, description="Employer 401k match as fraction of salary"
    )

    @model_validator(mode="after")
    def _net_not_above_gross(self) -> Income:
        if self.annual_net is not None and self.annual_net > self.annual_gross:
            raise ValueError("annual_net cannot exceed annual_gross")
        return self

    @property
    def effective_net(self) -> float:
        """Net income, estimated at 75% of gross when not supplied."""
        return self.annual_net if self.annual_net is not None else self.annual_gross * 0.75


class Expenses(BaseModel):
    model_config = ConfigDict(extra="forbid")

    monthly_essential: float = Field(
        ge=0, description="Housing, food, utilities, insurance, debt minimums"
    )
    monthly_discretionary: float = Field(default=0.0, ge=0)

    @property
    def monthly_total(self) -> float:
        return self.monthly_essential + self.monthly_discretionary

    @property
    def annual_total(self) -> float:
        return self.monthly_total * 12


class Debt(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    balance: float = Field(ge=0)
    apr: float = Field(ge=0, le=1, description="Annual rate as a fraction, e.g. 0.229 for 22.9%")
    minimum_monthly_payment: float = Field(default=0.0, ge=0)
    is_secured: bool = False

    @property
    def annual_interest_cost(self) -> float:
        return self.balance * self.apr


class Asset(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    value: float = Field(ge=0)
    account_type: AccountType = AccountType.taxable
    is_liquid: bool = True


class Goal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    goal_type: GoalType = GoalType.other
    target_amount: float | None = Field(default=None, ge=0)
    years_until_needed: float = Field(ge=0)
    priority: int = Field(default=3, ge=1, le=5, description="1 = highest priority")

    @property
    def is_short_horizon(self) -> bool:
        return self.years_until_needed < 3


class FinancialProfile(BaseModel):
    """A complete snapshot of a user's financial situation.

    This is the only input the deterministic analytics layer needs.
    """

    model_config = ConfigDict(extra="forbid")

    age: int = Field(ge=16, le=120)
    dependents: int = Field(default=0, ge=0)
    currency: str = Field(default="USD", min_length=3, max_length=3)

    income: Income
    expenses: Expenses
    debts: list[Debt] = Field(default_factory=list)
    assets: list[Asset] = Field(default_factory=list)
    goals: list[Goal] = Field(default_factory=list)

    risk_tolerance: RiskTolerance = RiskTolerance.moderate
    self_reported_experience: float = Field(
        default=0.5, ge=0, le=1, description="0 = complete novice, 1 = professional"
    )
    notes: str = Field(default="", max_length=4000)

    @field_validator("currency")
    @classmethod
    def _upper_currency(cls, v: str) -> str:
        return v.upper()

    # --- Derived, purely arithmetic conveniences -------------------------------------

    @property
    def total_assets(self) -> float:
        return sum(a.value for a in self.assets)

    @property
    def liquid_assets(self) -> float:
        return sum(a.value for a in self.assets if a.is_liquid)

    @property
    def total_debt(self) -> float:
        return sum(d.balance for d in self.debts)

    @property
    def net_worth(self) -> float:
        return self.total_assets - self.total_debt

    @property
    def monthly_minimum_debt_payment(self) -> float:
        return sum(d.minimum_monthly_payment for d in self.debts)

    @property
    def life_stage(self) -> LifeStage:
        retired = any(
            g.goal_type is GoalType.retirement and g.years_until_needed == 0 for g in self.goals
        )
        if retired or self.age >= 67:
            return LifeStage.retirement
        if self.age >= 55:
            return LifeStage.pre_retirement
        if self.age >= 45:
            return LifeStage.peak_earning
        if self.age >= 30:
            return LifeStage.accumulation
        return LifeStage.early_career

    @property
    def shortest_goal_horizon(self) -> float | None:
        return min((g.years_until_needed for g in self.goals), default=None)
