"""Deterministic financial profile analytics.

No LLM. No network. Given a FinancialProfile, produce the numbers and the need vector that
drive advisor selection and prompt content.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from app.domain.needs import NeedVector
from app.domain.portfolio import Portfolio
from app.domain.profile import FinancialProfile, GoalType, LifeStage

# Debt above this APR is treated as materially urgent regardless of other factors.
HIGH_APR_THRESHOLD = 0.08
# Emergency fund target in months of essential expenses.
EMERGENCY_FUND_TARGET_MONTHS = 6.0


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


class ProfileAnalytics(BaseModel):
    """Everything derivable from a profile with arithmetic alone."""

    model_config = ConfigDict(extra="forbid")

    life_stage: LifeStage
    net_worth: float
    total_assets: float
    liquid_assets: float
    total_debt: float

    annual_savings: float
    savings_rate: float = Field(description="Annual savings / annual gross income")
    emergency_fund_months: float = Field(description="Liquid assets / monthly essential expenses")
    debt_to_income: float = Field(description="Total debt / annual gross income")
    debt_service_ratio: float = Field(description="Monthly debt minimums / monthly net income")
    high_apr_debt_balance: float
    weighted_avg_apr: float
    annual_interest_cost: float
    tax_advantaged_share: float = Field(description="Share of assets in tax-advantaged accounts")
    years_of_expenses_covered: float

    need_vector: NeedVector
    notable_findings: list[str] = Field(default_factory=list)

    def summary_lines(self) -> list[str]:
        """Compact, prompt-ready facts. Deterministic ordering."""
        return [
            f"Life stage: {self.life_stage.value}",
            f"Net worth: {self.net_worth:,.0f}",
            f"Savings rate: {self.savings_rate:.1%}",
            f"Emergency fund: {self.emergency_fund_months:.1f} months of essential expenses",
            f"Debt-to-income: {self.debt_to_income:.2f}x",
            f"Debt service ratio: {self.debt_service_ratio:.1%} of net income",
            f"High-APR debt (> {HIGH_APR_THRESHOLD:.0%}): {self.high_apr_debt_balance:,.0f}",
            f"Annual interest cost: {self.annual_interest_cost:,.0f}",
            f"Assets in tax-advantaged accounts: {self.tax_advantaged_share:.0%}",
            f"Years of expenses covered by assets: {self.years_of_expenses_covered:.1f}",
        ]


def analyze_profile(
    profile: FinancialProfile, portfolio: Portfolio | None = None
) -> ProfileAnalytics:
    """Compute all deterministic profile metrics and the need vector."""
    monthly_essential = max(profile.expenses.monthly_essential, 1e-9)
    annual_gross = max(profile.income.annual_gross, 1e-9)
    monthly_net = max(profile.income.effective_net / 12, 1e-9)

    annual_savings = profile.income.effective_net - profile.expenses.annual_total
    savings_rate = annual_savings / annual_gross
    emergency_fund_months = profile.liquid_assets / monthly_essential
    debt_to_income = profile.total_debt / annual_gross
    debt_service_ratio = profile.monthly_minimum_debt_payment / monthly_net

    high_apr_balance = sum(d.balance for d in profile.debts if d.apr > HIGH_APR_THRESHOLD)
    annual_interest = sum(d.annual_interest_cost for d in profile.debts)
    weighted_avg_apr = (annual_interest / profile.total_debt) if profile.total_debt > 0 else 0.0

    tax_adv_value = sum(a.value for a in profile.assets if a.account_type.is_tax_advantaged)
    tax_advantaged_share = tax_adv_value / profile.total_assets if profile.total_assets > 0 else 0.0
    annual_expenses = max(profile.expenses.annual_total, 1e-9)
    years_covered = profile.total_assets / annual_expenses

    need = _build_need_vector(
        profile=profile,
        portfolio=portfolio,
        emergency_fund_months=emergency_fund_months,
        savings_rate=savings_rate,
        debt_to_income=debt_to_income,
        debt_service_ratio=debt_service_ratio,
        high_apr_balance=high_apr_balance,
        weighted_avg_apr=weighted_avg_apr,
        tax_advantaged_share=tax_advantaged_share,
        years_covered=years_covered,
    )

    findings = _notable_findings(
        profile, emergency_fund_months, savings_rate, high_apr_balance, weighted_avg_apr
    )

    return ProfileAnalytics(
        life_stage=profile.life_stage,
        net_worth=profile.net_worth,
        total_assets=profile.total_assets,
        liquid_assets=profile.liquid_assets,
        total_debt=profile.total_debt,
        annual_savings=annual_savings,
        savings_rate=savings_rate,
        emergency_fund_months=emergency_fund_months,
        debt_to_income=debt_to_income,
        debt_service_ratio=debt_service_ratio,
        high_apr_debt_balance=high_apr_balance,
        weighted_avg_apr=weighted_avg_apr,
        annual_interest_cost=annual_interest,
        tax_advantaged_share=tax_advantaged_share,
        years_of_expenses_covered=years_covered,
        need_vector=need,
        notable_findings=findings,
    )


def _build_need_vector(
    *,
    profile: FinancialProfile,
    portfolio: Portfolio | None,
    emergency_fund_months: float,
    savings_rate: float,
    debt_to_income: float,
    debt_service_ratio: float,
    high_apr_balance: float,
    weighted_avg_apr: float,
    tax_advantaged_share: float,
    years_covered: float,
) -> NeedVector:
    # Liquidity: shortfall against the 6-month target, plus pressure from unstable income,
    # dependents, and any near-term goal.
    liquidity = _clamp(1.0 - emergency_fund_months / EMERGENCY_FUND_TARGET_MONTHS)
    liquidity += (1.0 - profile.income.stability) * 0.3
    liquidity += min(profile.dependents, 3) * 0.05
    if any(g.is_short_horizon for g in profile.goals):
        liquidity += 0.15
    if savings_rate < 0:
        liquidity += 0.2

    # Debt pressure: scaled by both leverage and rate.
    debt = _clamp(debt_to_income / 2.0) * 0.5
    debt += _clamp(debt_service_ratio / 0.36) * 0.3
    if high_apr_balance > 0:
        debt += 0.25 + _clamp(weighted_avg_apr / 0.25) * 0.15

    # Concentration: from the portfolio when present, otherwise from illiquid asset skew.
    concentration = 0.0
    if portfolio and portfolio.total_value > 0:
        weights = [h.market_value / portfolio.total_value for h in portfolio.holdings]
        top = max(weights, default=0.0)
        hhi = sum(w * w for w in weights)
        concentration = _clamp(top / 0.35) * 0.6 + _clamp((hhi - 0.1) / 0.4) * 0.4
    elif profile.total_assets > 0:
        # No holdings supplied. Estimate from account-level skew, which is a weak proxy — an
        # IRA is a wrapper, not a position — so cap the score to avoid overstating the signal.
        largest = max((a.value for a in profile.assets), default=0.0)
        concentration = min(0.6, _clamp((largest / profile.total_assets - 0.3) / 0.5))

    # Valuation sensitivity: matters more when the horizon is short or the pot is already large
    # relative to spending needs.
    horizon = profile.shortest_goal_horizon
    valuation = 0.3
    if horizon is not None:
        valuation += _clamp(1.0 - horizon / 10.0) * 0.5
    valuation += _clamp((years_covered - 15) / 20) * 0.2
    if profile.life_stage in (LifeStage.pre_retirement, LifeStage.retirement):
        valuation += 0.15

    # Behavioral risk: inexperience, aggressive stance without experience, thin buffer.
    behavioral = _clamp(1.0 - profile.self_reported_experience) * 0.5
    behavioral += _clamp(profile.risk_tolerance.score - profile.self_reported_experience) * 0.3
    if emergency_fund_months < 3:
        behavioral += 0.2

    # Tax complexity: taxable-heavy balance sheets, high income, many accounts.
    tax = _clamp(1.0 - tax_advantaged_share) * 0.4
    tax += _clamp((profile.income.annual_gross - 100_000) / 250_000) * 0.4
    tax += _clamp((len({a.account_type for a in profile.assets}) - 1) / 4) * 0.2

    # Longevity: later life stages, retirement goals, thin coverage relative to spending.
    longevity = {
        LifeStage.early_career: 0.15,
        LifeStage.accumulation: 0.3,
        LifeStage.peak_earning: 0.45,
        LifeStage.pre_retirement: 0.75,
        LifeStage.retirement: 0.9,
    }[profile.life_stage]
    if any(g.goal_type is GoalType.retirement for g in profile.goals):
        longevity += 0.1
    if years_covered < 10 and profile.life_stage in (
        LifeStage.pre_retirement,
        LifeStage.retirement,
    ):
        longevity += 0.15

    return NeedVector(
        liquidity_risk=_clamp(liquidity),
        debt_pressure=_clamp(debt),
        concentration_risk=_clamp(concentration),
        valuation_sensitivity=_clamp(valuation),
        behavioral_risk=_clamp(behavioral),
        tax_complexity=_clamp(tax),
        longevity_risk=_clamp(longevity),
    )


def _notable_findings(
    profile: FinancialProfile,
    emergency_fund_months: float,
    savings_rate: float,
    high_apr_balance: float,
    weighted_avg_apr: float,
) -> list[str]:
    out: list[str] = []
    if emergency_fund_months < 3:
        out.append(
            f"Emergency fund covers only {emergency_fund_months:.1f} months of essential expenses."
        )
    if savings_rate < 0:
        out.append("Spending exceeds after-tax income; the profile is cash-flow negative.")
    elif savings_rate > 0.3:
        out.append(f"Savings rate of {savings_rate:.0%} is unusually strong.")
    if high_apr_balance > 0:
        out.append(
            f"{high_apr_balance:,.0f} of debt carries an APR above {HIGH_APR_THRESHOLD:.0%} "
            f"(weighted average {weighted_avg_apr:.1%})."
        )
    if profile.income.employer_match_pct > 0:
        out.append(
            f"Employer match of {profile.income.employer_match_pct:.0%} of salary is available."
        )
    if profile.income.stability < 0.5:
        out.append("Income is self-reported as unstable, raising the required cash buffer.")
    return out
