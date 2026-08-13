"""Deterministic analytics and guardrails."""

from __future__ import annotations

import pytest

from app.analytics.guardrails import (
    evaluate_guardrails,
    render_guardrails,
    verify_report_against_guardrails,
)
from app.analytics.portfolio_analytics import analyze_portfolio, compute_series_metrics
from app.analytics.profile_analytics import analyze_profile
from app.domain.portfolio import AssetClass, Holding, Portfolio, PriceSeries
from app.domain.profile import (
    AccountType,
    Asset,
    Debt,
    Expenses,
    FinancialProfile,
    Income,
    LifeStage,
)
from app.domain.report import GuardrailSeverity


def test_analytics_are_deterministic(stressed_profile, concentrated_portfolio) -> None:
    a = analyze_profile(stressed_profile, concentrated_portfolio)
    b = analyze_profile(stressed_profile, concentrated_portfolio)
    assert a.model_dump() == b.model_dump()


def test_core_ratios(stressed_profile) -> None:
    a = analyze_profile(stressed_profile)
    # Net income defaults to 75% of gross: 108,750. Annual expenses: 68,400.
    assert a.annual_savings == pytest.approx(108_750 - 68_400)
    assert a.savings_rate == pytest.approx((108_750 - 68_400) / 145_000)
    # Liquid assets 11,000 / 4,200 essential per month.
    assert a.emergency_fund_months == pytest.approx(11_000 / 4_200)
    assert a.high_apr_debt_balance == 9_000
    assert a.weighted_avg_apr == pytest.approx(0.229)
    assert a.life_stage is LifeStage.accumulation


def test_need_vector_is_bounded_and_responsive(stressed_profile, healthy_profile) -> None:
    stressed = analyze_profile(stressed_profile).need_vector
    healthy = analyze_profile(healthy_profile).need_vector

    for v in (*stressed.as_tuple(), *healthy.as_tuple()):
        assert 0.0 <= v <= 1.0

    assert stressed.debt_pressure > healthy.debt_pressure
    assert stressed.liquidity_risk > healthy.liquidity_risk
    assert healthy.longevity_risk > stressed.longevity_risk


def test_zero_income_does_not_divide_by_zero() -> None:
    profile = FinancialProfile(
        age=70,
        income=Income(annual_gross=0),
        expenses=Expenses(monthly_essential=0),
        assets=[],
    )
    a = analyze_profile(profile)
    assert a.savings_rate == pytest.approx(0.0, abs=1.0)
    assert a.emergency_fund_months >= 0


def test_guardrails_fire_on_stressed_profile(analyzed) -> None:
    _, _, rails = analyzed
    codes = {g.code for g in rails}
    assert "EMERGENCY_FUND_THIN" in codes
    assert "HIGH_APR_DEBT" in codes
    assert "POSITION_CONCENTRATION" in codes
    assert "SHORT_HORIZON_GOAL" in codes
    # Blocking items sort first so prompts lead with them.
    assert rails[0].severity is GuardrailSeverity.blocking


def test_healthy_profile_triggers_no_blocking_guardrails(healthy_profile) -> None:
    a = analyze_profile(healthy_profile)
    rails = evaluate_guardrails(healthy_profile, a)
    assert not [g for g in rails if g.severity is GuardrailSeverity.blocking]


def test_render_guardrails_states_they_are_binding(analyzed) -> None:
    _, _, rails = analyzed
    text = render_guardrails(rails)
    assert "BLOCKING" in text
    assert "must not recommend" in text


def test_guardrail_verification_catches_contradiction(analyzed) -> None:
    _, _, rails = analyzed
    bad = "You should invest all your cash into the index fund immediately."
    violations = verify_report_against_guardrails(bad, rails)
    assert any("EMERGENCY_FUND_THIN" in v for v in violations)

    good = "Rebuild the emergency fund to three months before adding market risk."
    assert verify_report_against_guardrails(good, rails) == []


def test_portfolio_weights_and_concentration(concentrated_portfolio) -> None:
    pa = analyze_portfolio(concentrated_portfolio)
    assert pa.total_value == 88_000
    assert pa.largest_holding_symbol == "NVDA"
    assert pa.largest_weight == pytest.approx(60_000 / 88_000)
    assert pa.hhi == pytest.approx((60 / 88) ** 2 + (28 / 88) ** 2)
    assert pa.effective_holdings == pytest.approx(1 / pa.hhi)
    assert pa.equity_share == pytest.approx(1.0)
    assert pa.unrealized_gain == pytest.approx(48_000)


def test_empty_portfolio_is_safe() -> None:
    pa = analyze_portfolio(Portfolio())
    assert pa.total_value == 0
    assert pa.holding_count == 0
    assert pa.largest_weight == 0.0


def test_series_metrics_drawdown_and_volatility() -> None:
    series = PriceSeries(symbol="X", periods_per_year=12, returns=[0.1, -0.2, 0.1, 0.05])
    m = compute_series_metrics(series)
    assert m.periods == 4
    assert m.worst_period == -0.2
    assert m.best_period == 0.1
    # Peak after +10%, trough after -20%: drawdown is -20%.
    assert m.max_drawdown == pytest.approx(-0.2)
    assert m.annualized_volatility > 0


def test_correlation_matrix_is_symmetric_with_unit_diagonal(concentrated_portfolio) -> None:
    pa = analyze_portfolio(concentrated_portfolio)
    corr = pa.correlations
    assert corr["NVDA"]["NVDA"] == pytest.approx(1.0)
    assert corr["NVDA"]["VTI"] == pytest.approx(corr["VTI"]["NVDA"])


def test_weighted_portfolio_series_computed_when_all_holdings_have_data(
    concentrated_portfolio,
) -> None:
    pa = analyze_portfolio(concentrated_portfolio)
    assert pa.portfolio_metrics is not None
    assert pa.portfolio_metrics.periods == 8


def test_weighted_portfolio_series_skipped_when_data_incomplete() -> None:
    portfolio = Portfolio(
        holdings=[
            Holding(symbol="A", asset_class=AssetClass.us_equity, market_value=100),
            Holding(symbol="B", asset_class=AssetClass.bonds, market_value=100),
        ],
        price_series=[PriceSeries(symbol="A", returns=[0.01, 0.02])],
    )
    assert analyze_portfolio(portfolio).portfolio_metrics is None


def test_tax_advantaged_share() -> None:
    profile = FinancialProfile(
        age=40,
        income=Income(annual_gross=100_000),
        expenses=Expenses(monthly_essential=3_000),
        assets=[
            Asset(name="ira", value=75_000, account_type=AccountType.roth_ira),
            Asset(name="brokerage", value=25_000, account_type=AccountType.taxable),
        ],
    )
    assert analyze_profile(profile).tax_advantaged_share == pytest.approx(0.75)


def test_debt_validation_rejects_impossible_apr() -> None:
    with pytest.raises(ValueError):
        Debt(name="x", balance=100, apr=1.5)


def test_income_validation_rejects_net_above_gross() -> None:
    with pytest.raises(ValueError):
        Income(annual_gross=100, annual_net=200)
