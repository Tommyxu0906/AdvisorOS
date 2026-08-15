"""Applying a plan and measuring what it did.

The load-bearing tests here are `test_a_trim_actually_reduces_concentration` (the claim a
concentration policy makes must survive arithmetic) and
`test_spending_the_emergency_fund_is_caught_as_a_new_blocking_guardrail` (the thing the old
regex check was trying and failing to do).
"""

from __future__ import annotations

import pytest

from app.analytics import counterfactual
from app.analytics.portfolio_analytics import analyze_portfolio
from app.domain.action import ActionKind, ActionSet, ProposedAction, TaxRange
from app.domain.portfolio import AssetClass, Holding, Portfolio
from app.domain.profile import (
    AccountType,
    Asset,
    Debt,
    Expenses,
    FinancialProfile,
    Income,
    RiskTolerance,
)


def _profile(**overrides) -> FinancialProfile:
    base = dict(
        age=34,
        dependents=1,
        income=Income(annual_gross=145_000),
        expenses=Expenses(monthly_essential=4_200, monthly_discretionary=1_500),
        debts=[Debt(name="credit card", balance=9_000, apr=0.229, minimum_monthly_payment=280)],
        assets=[Asset(name="savings", value=30_000, account_type=AccountType.cash, is_liquid=True)],
        risk_tolerance=RiskTolerance.moderate,
    )
    base.update(overrides)
    return FinancialProfile(**base)


def _concentrated() -> Portfolio:
    return Portfolio(
        holdings=[
            Holding(
                symbol="NVDA",
                asset_class=AssetClass.us_equity,
                quantity=400,
                market_value=90_000,
                cost_basis=30_000,
            ),
            Holding(
                symbol="VTI", asset_class=AssetClass.us_equity, quantity=26, market_value=10_000
            ),
        ]
    )


def _trim(symbol: str, amount: float, **kw) -> ProposedAction:
    return ProposedAction(
        action_id=kw.pop("action_id", "trim"),
        kind=ActionKind.trim_position,
        symbol=symbol,
        amount_usd=amount,
        **kw,
    )


# --- the claims a policy makes must survive arithmetic -------------------------------


def test_a_trim_actually_reduces_concentration():
    result = counterfactual.evaluate(
        _profile(), _concentrated(), ActionSet(actions=[_trim("NVDA", 45_000)])
    )

    assert result.feasible
    assert result.unapplied == []
    assert result.ineffective_actions == []

    largest = next(c for c in result.changes if c.label == "largest position weight")
    assert largest.before == 0.9
    assert largest.after < largest.before
    assert largest.improved is True


def test_selling_a_position_does_not_look_like_getting_richer():
    """`ProfileAnalytics.net_worth` counts only `profile.assets`; the portfolio is a separate
    ledger. Reporting that field alone made a sale read as a gain, because value moved from an
    uncounted list into a counted one. The reported figure sums both."""
    result = counterfactual.evaluate(
        _profile(), _concentrated(), ActionSet(actions=[_trim("NVDA", 45_000)])
    )

    worth = next(c for c in result.changes if c.label.startswith("net worth"))
    assert worth.before == worth.after
    # And it stays a direction-free measure: converting a holding to cash is neither.
    assert worth.higher_is_better is None


def test_the_tax_cost_of_a_plan_is_totalled_but_not_subtracted():
    plan = ActionSet(
        actions=[
            ProposedAction(
                action_id="t",
                kind=ActionKind.trim_position,
                symbol="NVDA",
                amount_usd=45_000,
                estimated_tax=TaxRange(low_usd=4_500, high_usd=9_600),
            )
        ]
    )
    result = counterfactual.evaluate(_profile(), _concentrated(), plan)
    assert result.estimated_tax == TaxRange(low_usd=4_500, high_usd=9_600)
    # Reported alongside the balances, never folded into them.
    worth = next(c for c in result.changes if c.label.startswith("net worth"))
    assert worth.after == worth.before


def test_the_ends_of_a_plans_tax_range_are_summed_independently():
    """Every action in a plan settles in the same year for the same filer, so the treatments move
    together. Narrowing the total would claim a diversification that does not exist."""
    plan = ActionSet(
        actions=[
            ProposedAction(
                action_id="a",
                kind=ActionKind.trim_position,
                symbol="NVDA",
                amount_usd=20_000,
                estimated_tax=TaxRange(low_usd=1_000, high_usd=2_000),
            ),
            ProposedAction(
                action_id="b",
                kind=ActionKind.trim_position,
                symbol="VTI",
                amount_usd=5_000,
                estimated_tax=TaxRange(low_usd=300, high_usd=700),
            ),
        ]
    )
    total = counterfactual.evaluate(_profile(), _concentrated(), plan).estimated_tax
    assert total == TaxRange(low_usd=1_300, high_usd=2_700)


def test_an_unknown_tax_estimate_totals_to_none_rather_than_zero():
    result = counterfactual.evaluate(
        _profile(), _concentrated(), ActionSet(actions=[_trim("NVDA", 1_000)])
    )
    assert result.estimated_tax is None


def test_an_action_that_does_not_move_its_metric_is_reported():
    """A trim of nothing is a policy bug, not a valid recommendation."""
    result = counterfactual.evaluate(
        _profile(),
        _concentrated(),
        ActionSet(actions=[_trim("NVDA", 0.0, action_id="noop")]),
    )
    assert result.ineffective_actions == ["noop"]
    assert result.holds_up is False


def test_paying_down_debt_resolves_the_blocking_guardrail():
    plan = ActionSet(
        actions=[
            ProposedAction(
                action_id="card",
                kind=ActionKind.pay_down_debt,
                symbol="credit card",
                amount_usd=9_000,
            )
        ]
    )
    result = counterfactual.evaluate(_profile(), _concentrated(), plan)

    assert "HIGH_APR_DEBT" in result.resolved_guardrails
    assert result.introduced_guardrails == []
    interest = next(c for c in result.changes if c.label == "annual interest cost")
    assert interest.after == 0.0
    assert interest.improved is True


# --- what the regex check was trying to do -------------------------------------------


def test_spending_the_emergency_fund_is_caught_as_a_new_blocking_guardrail():
    """No phrase matching involved — the reserve simply drops below three months."""
    profile = _profile(
        assets=[Asset(name="savings", value=13_000, account_type=AccountType.cash, is_liquid=True)]
    )
    plan = ActionSet(
        actions=[
            ProposedAction(
                action_id="deploy",
                kind=ActionKind.add_position,
                symbol="VTI",
                asset_class=AssetClass.us_equity,
                amount_usd=12_000,
            )
        ]
    )

    result = counterfactual.evaluate(profile, _concentrated(), plan)

    assert "EMERGENCY_FUND_THIN" in result.introduced_guardrails
    assert result.introduces_blocking_guardrail is True
    assert result.holds_up is False


def test_the_same_purchase_is_fine_when_the_reserve_can_absorb_it():
    result = counterfactual.evaluate(
        _profile(),  # 30k of savings against 4.2k monthly essentials
        _concentrated(),
        ActionSet(
            actions=[
                ProposedAction(
                    action_id="deploy",
                    kind=ActionKind.add_position,
                    symbol="VTI",
                    asset_class=AssetClass.us_equity,
                    amount_usd=5_000,
                )
            ]
        ),
    )
    assert result.introduced_guardrails == []
    assert result.feasible


# --- infeasible plans ------------------------------------------------------------------


def test_an_infeasible_plan_is_reported_as_such():
    plan = ActionSet(
        actions=[
            ProposedAction(
                action_id="toobig",
                kind=ActionKind.add_position,
                symbol="BND",
                asset_class=AssetClass.bonds,
                amount_usd=400_000,
            )
        ]
    )
    result = counterfactual.evaluate(_profile(), _concentrated(), plan)
    assert result.feasible is False
    assert result.infeasibilities
    assert result.holds_up is False


def test_an_unmodellable_action_is_listed_rather_than_guessed():
    """Rebalancing into an asset class the portfolio does not hold would mean picking a ticker."""
    plan = ActionSet(
        actions=[
            ProposedAction(
                action_id="bonds",
                kind=ActionKind.rebalance_to_target,
                asset_class=AssetClass.bonds,
                target_weight=1.0,
            )
        ]
    )
    result = counterfactual.evaluate(_profile(), _concentrated(), plan)
    assert result.unapplied == ["bonds"]
    assert result.holds_up is False


# --- the applier itself ----------------------------------------------------------------


def test_the_originals_are_never_mutated():
    profile, portfolio = _profile(), _concentrated()
    counterfactual.apply(profile, portfolio, ActionSet(actions=[_trim("NVDA", 50_000)]))
    assert portfolio.holdings[0].market_value == 90_000
    assert profile.assets[0].value == 30_000


def test_a_sale_lands_in_liquid_assets_and_scales_the_lot():
    profile, portfolio = _profile(), _concentrated()
    after_profile, after_portfolio, unapplied = counterfactual.apply(
        profile, portfolio, ActionSet(actions=[_trim("NVDA", 45_000)])
    )

    assert unapplied == []
    nvda = next(h for h in after_portfolio.holdings if h.symbol == "NVDA")
    assert nvda.market_value == 45_000
    # Quantity and basis follow the value down, so cost basis stays meaningful.
    assert nvda.quantity == 200
    assert nvda.cost_basis == 15_000
    assert after_profile.assets[0].value == 75_000


def test_a_fully_sold_position_disappears():
    _, after_portfolio, _ = counterfactual.apply(
        _profile(), _concentrated(), ActionSet(actions=[_trim("NVDA", 90_000)])
    )
    assert [h.symbol for h in after_portfolio.holdings] == ["VTI"]


def test_proceeds_create_a_cash_asset_when_the_profile_has_none():
    profile = _profile(assets=[])
    after_profile, _, _ = counterfactual.apply(
        profile, _concentrated(), ActionSet(actions=[_trim("NVDA", 10_000)])
    )
    assert len(after_profile.assets) == 1
    assert after_profile.assets[0].value == 10_000
    assert after_profile.assets[0].is_liquid is True


def test_redirecting_cashflow_raises_the_savings_rate():
    plan = ActionSet(
        actions=[
            ProposedAction(action_id="redirect", kind=ActionKind.redirect_cashflow, amount_usd=500)
        ]
    )
    result = counterfactual.evaluate(_profile(), _concentrated(), plan)
    savings = next(c for c in result.changes if c.label == "savings rate")
    assert savings.improved is True
    assert result.ineffective_actions == []


def test_a_hold_changes_nothing_and_is_not_called_ineffective():
    plan = ActionSet(
        actions=[
            ProposedAction(action_id="h", kind=ActionKind.hold, rationale="already appropriate")
        ]
    )
    result = counterfactual.evaluate(_profile(), _concentrated(), plan)
    assert result.ineffective_actions == []
    assert result.unapplied == []
    assert all(abs(c.delta) < 1e-9 for c in result.changes)
    assert result.holds_up is True


def test_a_trim_is_judged_on_the_position_not_on_the_portfolio_percentages():
    """Weight was the obvious metric for a trim and it is wrong.

    A water-filling plan trims several positions onto the same target, so the whole portfolio
    shrinks by exactly what was sold and every weight comes out unchanged. Judging on weight
    called a $20,000 -> $19,000 trim ineffective, which blamed one action for what the rest of
    the plan did — and, because `holds_up` gates whether a scenario is shown at all, silently
    suppressed correct multi-position plans. Only running the wired engine surfaced it.
    """
    portfolio = Portfolio(
        holdings=[
            Holding(symbol="AAPL", quantity=100, market_value=22_000, cost_basis=15_000),
            Holding(symbol="VTI", quantity=26, market_value=20_000, cost_basis=18_000),
            Holding(symbol="BND", quantity=26, market_value=20_000, cost_basis=20_000),
            Holding(symbol="VXUS", quantity=26, market_value=19_000),
            Holding(symbol="VNQ", quantity=26, market_value=19_000),
        ]
    )
    plan = ActionSet(
        actions=[
            _trim("AAPL", 3_000, action_id="trim_aapl"),
            _trim("VTI", 1_000, action_id="trim_vti"),
            _trim("BND", 1_000, action_id="trim_bnd"),
        ]
    )
    result = counterfactual.evaluate(_profile(), portfolio, plan)

    assert result.ineffective_actions == []
    assert result.holds_up

    # Every position lands on 20%, so not one weight moved — which is exactly the trap.
    after = analyze_portfolio(counterfactual.apply(_profile(), portfolio, plan)[1])
    assert after.weights["VTI"] == pytest.approx(0.20)
    assert after.weights["AAPL"] == pytest.approx(0.20)


def test_a_trim_that_really_does_nothing_is_still_caught():
    """The fix must not turn the check off — a zero-sized trim is still a policy bug."""
    result = counterfactual.evaluate(
        _profile(), _concentrated(), ActionSet(actions=[_trim("NVDA", 0.0, action_id="noop")])
    )
    assert result.ineffective_actions == ["noop"]
