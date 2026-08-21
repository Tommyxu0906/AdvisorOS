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
from app.domain.profile import AccountType
from app.domain.report import GuardrailSeverity


def test_analytics_are_deterministic(stressed_profile, concentrated_portfolio) -> None:
    a = analyze_profile(stressed_profile, concentrated_portfolio)
    b = analyze_profile(stressed_profile, concentrated_portfolio)
    assert a.model_dump() == b.model_dump()


def test_need_vector_is_bounded_and_responsive(stressed_profile, healthy_profile) -> None:
    stressed = analyze_profile(stressed_profile).need_vector
    healthy = analyze_profile(healthy_profile).need_vector

    for v in (*stressed.as_tuple(), *healthy.as_tuple()):
        assert 0.0 <= v <= 1.0

    assert stressed.horizon_pressure > healthy.horizon_pressure
    assert stressed.behavioral_risk > healthy.behavioral_risk
    assert healthy.longevity_risk > stressed.longevity_risk


def test_guardrails_fire_on_stressed_profile(analyzed) -> None:
    _, _, rails = analyzed
    codes = {g.code for g in rails}
    assert "HORIZON_RISK_MISMATCH" in codes
    assert "POSITION_CONCENTRATION" in codes
    assert "POSITION_CONCENTRATION" in codes
    assert "HORIZON_RISK_MISMATCH" in codes
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
    bad = "Put the whole balance into equities now and ignore when you need the money."
    violations = verify_report_against_guardrails(bad, rails)
    assert any("HORIZON_RISK_MISMATCH" in v for v in violations)

    good = "Bring the growth share down before adding any further market risk."
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


def test_same_symbol_in_two_accounts_is_aggregated_not_overwritten() -> None:
    """Holding one ticker in two accounts is ordinary and must not corrupt the weights.

    Keying weights on symbol alone used to keep only the last lot, which made the weights stop
    summing to 1, double-counted the symbol in equity_share, and computed HHI from the surviving
    fragment — reporting a two-lot portfolio as 6.25 effective holdings.
    """
    portfolio = Portfolio(
        holdings=[
            Holding(
                symbol="VTI",
                asset_class=AssetClass.us_equity,
                market_value=60_000,
                account_type=AccountType.taxable,
            ),
            Holding(
                symbol="VTI",
                asset_class=AssetClass.us_equity,
                market_value=40_000,
                account_type=AccountType.roth_ira,
            ),
        ]
    )
    pa = analyze_portfolio(portfolio)

    assert pa.total_value == 100_000
    assert pa.holding_count == 2
    assert sum(pa.weights.values()) == pytest.approx(1.0)
    # One symbol, therefore fully concentrated, therefore one effective holding.
    assert pa.weights == {"VTI": pytest.approx(1.0)}
    assert pa.largest_weight == pytest.approx(1.0)
    assert pa.hhi == pytest.approx(1.0)
    assert pa.effective_holdings == pytest.approx(1.0)
    # Counted once, not twice.
    assert pa.equity_share == pytest.approx(1.0)
    # Only the taxable lot is in a taxable account.
    assert pa.taxable_share == pytest.approx(0.6)


def test_weighted_expense_ratio_uses_value_not_deduped_weights() -> None:
    """The expense-ratio average weights by money, so duplicate symbols cannot skew it."""
    portfolio = Portfolio(
        holdings=[
            Holding(symbol="A", market_value=75_000, expense_ratio=0.04),
            Holding(symbol="B", market_value=25_000, expense_ratio=0.08),
        ]
    )
    pa = analyze_portfolio(portfolio)
    assert pa.weighted_expense_ratio == pytest.approx(0.75 * 0.04 + 0.25 * 0.08)


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
