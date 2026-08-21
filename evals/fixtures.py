"""Labelled evaluation fixtures.

Each case carries the profile, the question, and a hand-labelled expectation about what the
deterministic layer *should* conclude. These labels are the ground truth for selection accuracy
and guardrail coverage; they are not model outputs.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.domain.portfolio import AssetClass, Holding, Portfolio, PriceSeries
from app.domain.profile import AccountType, FinancialProfile, RiskTolerance


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
            case_id="concentrated_single_name",
            description="Thin buffer, 22.9% APR card, 68% of portfolio in one stock, house in 2 years",
            profile=FinancialProfile(
                age=34,
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
            expected_guardrails={"POSITION_CONCENTRATION"},
            expected_top_needs={"concentration_risk"},
            expected_any_advisor={"housel", "munger", "buffett"},
        ),
        EvalCase(
            case_id="pre_retiree_valuation_worry",
            description="Well-funded 61-year-old, four years from retirement, worried about valuations",
            profile=FinancialProfile(
                age=61,
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
                risk_tolerance=RiskTolerance.aggressive,
                self_reported_experience=0.1,
            ),
            question="All my money is sitting in a savings account. How should I invest it for the long term?",
            expected_top_needs={"behavioral_risk"},
            expected_any_advisor={"bogle", "housel"},
        ),
        EvalCase(
            case_id="near_term_need_in_equities",
            description="Money needed in two years, sitting in equities. The one blocking rule.",
            profile=FinancialProfile(
                age=41,
                horizon_years=2.0,
                investable_cash=3_000,
                risk_tolerance=RiskTolerance.conservative,
                self_reported_experience=0.2,
            ),
            portfolio=Portfolio(
                holdings=[
                    Holding(
                        symbol="VTI",
                        name="Total market",
                        asset_class=AssetClass.us_equity,
                        quantity=300,
                        market_value=96_000,
                        account_type=AccountType.taxable,
                    ),
                    Holding(
                        symbol="BND",
                        name="Bonds",
                        asset_class=AssetClass.bonds,
                        quantity=200,
                        market_value=14_000,
                        account_type=AccountType.taxable,
                    ),
                ]
            ),
            question="I need this money in two years. Should I stay invested?",
            expected_guardrails={"HORIZON_RISK_MISMATCH"},
            expected_top_needs={"horizon_pressure"},
            expected_any_advisor={"housel", "munger", "buffett"},
        ),
        EvalCase(
            case_id="retiree_withdrawal",
            description="Retiree drawing down; longevity and sequence risk dominate",
            profile=FinancialProfile(
                age=71,
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
                risk_tolerance=RiskTolerance.moderate,
                self_reported_experience=0.7,
            ),
            question="Should I do a Roth conversion this year, and where should I place my bonds for tax purposes?",
            expected_top_needs={"tax_complexity"},
            expected_any_advisor={"damodaran", "bogle", "buffett"},
        ),
    ]
