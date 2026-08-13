"""Labelled evaluation fixtures.

Each case carries the profile, the question, and a hand-labelled expectation about what the
deterministic layer *should* conclude. These labels are the ground truth for selection accuracy
and guardrail coverage; they are not model outputs.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.domain.portfolio import AssetClass, Holding, Portfolio, PriceSeries
from app.domain.profile import (
    AccountType,
    Asset,
    Debt,
    Expenses,
    FinancialProfile,
    Goal,
    GoalType,
    Income,
    RiskTolerance,
)


@dataclass(slots=True)
class EvalCase:
    case_id: str
    description: str
    profile: FinancialProfile
    question: str
    portfolio: Portfolio | None = None
    #: Guardrail codes that must fire.
    expected_guardrails: set[str] = field(default_factory=set)
    #: Need dimensions that must be among the top scores.
    expected_top_needs: set[str] = field(default_factory=set)
    #: At least one of these advisors must be selected.
    expected_any_advisor: set[str] = field(default_factory=set)


def _series(symbol: str, returns: list[float]) -> PriceSeries:
    return PriceSeries(symbol=symbol, periods_per_year=12, returns=returns)


NVDA_RETURNS = [0.10, -0.15, 0.22, 0.05, -0.08, 0.30, 0.02, -0.12, 0.18, -0.09, 0.07, 0.11]
VTI_RETURNS = [0.02, -0.03, 0.04, 0.01, -0.02, 0.05, 0.01, -0.01, 0.03, -0.02, 0.02, 0.01]
BND_RETURNS = [0.003, -0.004, 0.006, 0.002, -0.001, 0.004, 0.001, 0.002, -0.003, 0.005, 0.001, 0.0]


def all_cases() -> list[EvalCase]:
    return [
        EvalCase(
            case_id="concentrated_with_card_debt",
            description="Thin buffer, 22.9% APR card, 68% of portfolio in one stock, house in 2 years",
            profile=FinancialProfile(
                age=34,
                dependents=1,
                income=Income(annual_gross=145_000, employer_match_pct=0.04, stability=0.7),
                expenses=Expenses(monthly_essential=4_200, monthly_discretionary=1_500),
                debts=[
                    Debt(name="credit card", balance=9_000, apr=0.229, minimum_monthly_payment=280)
                ],
                assets=[
                    Asset(name="savings", value=11_000, account_type=AccountType.cash),
                    Asset(
                        name="401k",
                        value=88_000,
                        account_type=AccountType.traditional_401k,
                        is_liquid=False,
                    ),
                ],
                goals=[
                    Goal(name="house down payment", years_until_needed=2.0, target_amount=80_000)
                ],
                risk_tolerance=RiskTolerance.moderate_aggressive,
                self_reported_experience=0.35,
            ),
            portfolio=Portfolio(
                holdings=[
                    Holding(
                        symbol="NVDA",
                        asset_class=AssetClass.us_equity,
                        market_value=60_000,
                        cost_basis=12_000,
                    ),
                    Holding(
                        symbol="VTI",
                        asset_class=AssetClass.us_equity,
                        market_value=28_000,
                        expense_ratio=0.0003,
                    ),
                ],
                price_series=[_series("NVDA", NVDA_RETURNS), _series("VTI", VTI_RETURNS)],
            ),
            question="Should I sell some NVDA right now and pay off my credit card, or keep riding it?",
            expected_guardrails={"EMERGENCY_FUND_THIN", "HIGH_APR_DEBT", "POSITION_CONCENTRATION"},
            expected_top_needs={"concentration_risk", "liquidity_risk", "debt_pressure"},
            expected_any_advisor={"housel", "munger", "buffett"},
        ),
        EvalCase(
            case_id="pre_retiree_valuation_worry",
            description="Well-funded 61-year-old, four years from retirement, worried about valuations",
            profile=FinancialProfile(
                age=61,
                income=Income(annual_gross=180_000, annual_net=128_000, stability=0.95),
                expenses=Expenses(monthly_essential=5_000, monthly_discretionary=2_000),
                assets=[
                    Asset(name="cash", value=90_000, account_type=AccountType.cash),
                    Asset(name="ira", value=1_400_000, account_type=AccountType.traditional_ira),
                    Asset(name="brokerage", value=600_000, account_type=AccountType.taxable),
                ],
                goals=[
                    Goal(
                        name="retirement",
                        goal_type=GoalType.retirement,
                        years_until_needed=4,
                        priority=1,
                    )
                ],
                risk_tolerance=RiskTolerance.moderate_conservative,
                self_reported_experience=0.8,
            ),
            portfolio=Portfolio(
                holdings=[
                    Holding(
                        symbol="VTI",
                        asset_class=AssetClass.us_equity,
                        market_value=1_200_000,
                        expense_ratio=0.0003,
                    ),
                    Holding(
                        symbol="BND",
                        asset_class=AssetClass.bonds,
                        market_value=800_000,
                        expense_ratio=0.0003,
                    ),
                ],
                price_series=[_series("VTI", VTI_RETURNS), _series("BND", BND_RETURNS)],
            ),
            question="Is the market overvalued right now? Should I shift more into bonds before I retire?",
            expected_top_needs={"valuation_sensitivity", "longevity_risk"},
            expected_any_advisor={"marks", "damodaran", "bogle"},
        ),
        EvalCase(
            case_id="novice_cash_heavy",
            description="26-year-old, no debt, everything in cash, wants long-term growth",
            profile=FinancialProfile(
                age=26,
                income=Income(annual_gross=72_000, stability=0.85),
                expenses=Expenses(monthly_essential=2_600, monthly_discretionary=900),
                assets=[Asset(name="savings", value=42_000, account_type=AccountType.cash)],
                goals=[
                    Goal(
                        name="long-term wealth",
                        goal_type=GoalType.wealth_growth,
                        years_until_needed=25,
                    )
                ],
                risk_tolerance=RiskTolerance.aggressive,
                self_reported_experience=0.1,
            ),
            question="All my money is sitting in a savings account. How should I invest it for the long term?",
            expected_top_needs={"behavioral_risk"},
            expected_any_advisor={"bogle", "housel"},
        ),
        EvalCase(
            case_id="cash_flow_negative",
            description="Spending exceeds income; a blocking guardrail must fire",
            profile=FinancialProfile(
                age=41,
                dependents=2,
                income=Income(annual_gross=95_000, annual_net=68_000, stability=0.6),
                expenses=Expenses(monthly_essential=5_200, monthly_discretionary=1_400),
                debts=[
                    Debt(
                        name="auto",
                        balance=28_000,
                        apr=0.079,
                        minimum_monthly_payment=560,
                        is_secured=True,
                    ),
                    Debt(name="card", balance=14_000, apr=0.244, minimum_monthly_payment=420),
                ],
                assets=[Asset(name="checking", value=4_000, account_type=AccountType.cash)],
                goals=[Goal(name="stability", years_until_needed=1)],
                risk_tolerance=RiskTolerance.conservative,
                self_reported_experience=0.2,
            ),
            question="Should I start investing in index funds to catch up on retirement?",
            expected_guardrails={"CASH_FLOW_NEGATIVE", "EMERGENCY_FUND_THIN", "HIGH_APR_DEBT"},
            expected_top_needs={"debt_pressure", "liquidity_risk"},
            expected_any_advisor={"housel", "munger", "buffett"},
        ),
        EvalCase(
            case_id="retiree_withdrawal",
            description="Retiree drawing down; longevity and sequence risk dominate",
            profile=FinancialProfile(
                age=71,
                income=Income(annual_gross=42_000, annual_net=38_000, stability=1.0),
                expenses=Expenses(monthly_essential=4_100, monthly_discretionary=800),
                assets=[
                    Asset(name="cash", value=120_000, account_type=AccountType.cash),
                    Asset(name="ira", value=890_000, account_type=AccountType.traditional_ira),
                ],
                goals=[
                    Goal(
                        name="lifetime income",
                        goal_type=GoalType.income,
                        years_until_needed=0,
                        priority=1,
                    )
                ],
                risk_tolerance=RiskTolerance.conservative,
                self_reported_experience=0.6,
            ),
            question="How much can I safely withdraw each year without running out of money?",
            expected_top_needs={"longevity_risk"},
            expected_any_advisor={"housel", "bogle", "marks"},
        ),
        EvalCase(
            case_id="high_income_tax",
            description="High earner, taxable-heavy, asking about tax placement",
            profile=FinancialProfile(
                age=45,
                income=Income(annual_gross=420_000, annual_net=250_000, stability=0.8),
                expenses=Expenses(monthly_essential=9_000, monthly_discretionary=4_000),
                assets=[
                    Asset(name="cash", value=180_000, account_type=AccountType.cash),
                    Asset(name="brokerage", value=1_900_000, account_type=AccountType.taxable),
                    Asset(
                        name="401k",
                        value=340_000,
                        account_type=AccountType.traditional_401k,
                        is_liquid=False,
                    ),
                ],
                goals=[
                    Goal(name="retirement", goal_type=GoalType.retirement, years_until_needed=18),
                    Goal(name="college", goal_type=GoalType.education, years_until_needed=8),
                ],
                risk_tolerance=RiskTolerance.moderate,
                self_reported_experience=0.7,
            ),
            question="Should I do a Roth conversion this year, and where should I place my bonds for tax purposes?",
            expected_top_needs={"tax_complexity"},
            expected_any_advisor={"damodaran", "bogle", "buffett"},
        ),
    ]
