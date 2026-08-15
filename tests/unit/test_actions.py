"""Feasibility arithmetic for proposed actions.

The case this module exists for is `test_cannot_spend_money_that_is_not_there`: before actions
were typed, a report could tell someone to move $40,000 into bonds while they held $12,000, and
nothing in the system could notice. Every other test here guards a way of reintroducing that.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.domain.action import (
    ActionKind,
    ActionSet,
    InfeasibleReason,
    ProposedAction,
)
from app.domain.portfolio import AssetClass, Holding, Portfolio
from app.domain.profile import AccountType


def _portfolio() -> Portfolio:
    return Portfolio(
        holdings=[
            Holding(
                symbol="NVDA",
                asset_class=AssetClass.us_equity,
                quantity=100,
                market_value=22516,
                cost_basis=8000,
            ),
            Holding(
                symbol="VTI", asset_class=AssetClass.us_equity, quantity=50, market_value=19192
            ),
        ]
    )


def _action(**kwargs) -> ProposedAction:
    base = {"action_id": kwargs.pop("action_id", "a1"), "kind": ActionKind.trim_position}
    base.update(kwargs)
    return ProposedAction(**base)


# --- sizing --------------------------------------------------------------------------


def test_exactly_one_sizing_form_is_required():
    with pytest.raises(ValidationError, match="exactly one of"):
        ProposedAction(action_id="a", kind=ActionKind.trim_position, symbol="NVDA")

    with pytest.raises(ValidationError, match="exactly one of"):
        ProposedAction(
            action_id="a",
            kind=ActionKind.trim_position,
            symbol="NVDA",
            shares=10,
            amount_usd=2000,
        )


def test_a_hold_carries_no_size():
    ProposedAction(action_id="a", kind=ActionKind.hold, rationale="nothing to do")
    with pytest.raises(ValidationError, match="cannot carry a size"):
        ProposedAction(action_id="a", kind=ActionKind.hold, amount_usd=100)


# --- the failure this module was written for -----------------------------------------


def test_cannot_spend_money_that_is_not_there():
    """The $40,000-from-$12,000 recommendation, which used to pass every check."""
    plan = ActionSet(
        actions=[
            _action(
                action_id="bonds",
                kind=ActionKind.add_position,
                symbol="BND",
                amount_usd=40_000,
                rationale="move into bonds",
            )
        ]
    )

    problems = plan.check_feasible(_portfolio(), liquid_assets=12_000)

    assert [p.reason for p in problems] == [InfeasibleReason.insufficient_cash]
    assert "$40,000.00" in problems[0].message
    assert "$12,000.00" in problems[0].message


def test_the_same_purchase_is_feasible_once_a_sale_funds_it():
    """Sequence is the difference between a plan that works and one that does not."""
    plan = ActionSet(
        actions=[
            _action(
                action_id="buy",
                kind=ActionKind.add_position,
                symbol="BND",
                amount_usd=20_000,
                sequence=1,
            ),
            _action(action_id="sell", symbol="NVDA", amount_usd=20_000, sequence=0),
        ]
    )
    assert plan.check_feasible(_portfolio(), liquid_assets=1_000) == []

    # Reverse the two steps and the money is not there yet.
    reversed_plan = ActionSet(
        actions=[
            _action(
                action_id="buy",
                kind=ActionKind.add_position,
                symbol="BND",
                amount_usd=20_000,
                sequence=0,
            ),
            _action(action_id="sell", symbol="NVDA", amount_usd=20_000, sequence=1),
        ]
    )
    problems = reversed_plan.check_feasible(_portfolio(), liquid_assets=1_000)
    assert [p.reason for p in problems] == [InfeasibleReason.insufficient_cash]


# --- disposals -----------------------------------------------------------------------


def test_cannot_sell_more_shares_than_are_held():
    plan = ActionSet(actions=[_action(symbol="NVDA", shares=250)])
    problems = plan.check_feasible(_portfolio(), liquid_assets=0)
    assert [p.reason for p in problems] == [InfeasibleReason.oversells_holding]
    assert "100 remain" in problems[0].message


def test_cannot_sell_a_holding_that_is_not_owned():
    plan = ActionSet(actions=[_action(symbol="TSLA", shares=10)])
    problems = plan.check_feasible(_portfolio(), liquid_assets=0)
    assert [p.reason for p in problems] == [InfeasibleReason.no_such_holding]


def test_two_actions_cannot_dispose_of_the_same_shares():
    plan = ActionSet(
        actions=[
            _action(action_id="first", symbol="NVDA", shares=60, sequence=0),
            _action(action_id="second", symbol="NVDA", shares=60, sequence=1),
        ]
    )
    problems = plan.check_feasible(_portfolio(), liquid_assets=0)
    assert [p.reason for p in problems] == [InfeasibleReason.oversells_holding]
    assert problems[0].action_id == "second"


def test_a_symbol_split_across_accounts_is_one_position():
    """VTI in a taxable account and a Roth is 50 + 25 shares, not two separate 50s."""
    split = Portfolio(
        holdings=[
            Holding(
                symbol="VTI", quantity=50, market_value=19192, account_type=AccountType.taxable
            ),
            Holding(
                symbol="VTI", quantity=25, market_value=9596, account_type=AccountType.roth_ira
            ),
        ]
    )
    assert ActionSet(actions=[_action(symbol="VTI", shares=70)]).check_feasible(split, 0) == []
    assert ActionSet(actions=[_action(symbol="VTI", shares=80)]).check_feasible(split, 0)


def test_a_holding_with_no_share_count_is_still_checked_by_value():
    """Quantity is optional — a private business has none — so value is the fallback."""
    no_quantity = Portfolio(holdings=[Holding(symbol="PRIVATECO", market_value=5_000)])
    plan = ActionSet(actions=[_action(symbol="PRIVATECO", amount_usd=9_000)])
    problems = plan.check_feasible(no_quantity, liquid_assets=0)
    assert [p.reason for p in problems] == [InfeasibleReason.oversells_holding]


# --- weights and hygiene -------------------------------------------------------------


def test_rebalance_targets_must_describe_a_whole_portfolio():
    plan = ActionSet(
        actions=[
            _action(
                action_id="eq",
                kind=ActionKind.rebalance_to_target,
                asset_class=AssetClass.us_equity,
                target_weight=0.6,
            ),
            _action(
                action_id="bond",
                kind=ActionKind.rebalance_to_target,
                asset_class=AssetClass.bonds,
                target_weight=0.2,
            ),
        ]
    )
    problems = plan.check_feasible(_portfolio(), liquid_assets=0)
    assert [p.reason for p in problems] == [InfeasibleReason.weights_do_not_sum]
    assert "0.800" in problems[0].message


def test_rebalance_targets_summing_to_one_are_accepted():
    plan = ActionSet(
        actions=[
            _action(
                action_id="eq",
                kind=ActionKind.rebalance_to_target,
                asset_class=AssetClass.us_equity,
                target_weight=0.8,
            ),
            _action(
                action_id="bond",
                kind=ActionKind.rebalance_to_target,
                asset_class=AssetClass.bonds,
                target_weight=0.2,
            ),
        ]
    )
    assert plan.check_feasible(_portfolio(), liquid_assets=0) == []


def test_duplicate_action_ids_are_rejected():
    plan = ActionSet(
        actions=[
            _action(action_id="same", symbol="NVDA", shares=1),
            _action(action_id="same", symbol="VTI", shares=1),
        ]
    )
    problems = plan.check_feasible(_portfolio(), liquid_assets=0)
    assert InfeasibleReason.duplicate_action_id in {p.reason for p in problems}


def test_every_problem_is_reported_not_just_the_first():
    plan = ActionSet(
        actions=[
            _action(action_id="a", symbol="TSLA", shares=10),
            _action(
                action_id="b",
                kind=ActionKind.add_position,
                symbol="BND",
                amount_usd=99_999,
                sequence=1,
            ),
        ]
    )
    problems = plan.check_feasible(_portfolio(), liquid_assets=100)
    assert {p.reason for p in problems} == {
        InfeasibleReason.no_such_holding,
        InfeasibleReason.insufficient_cash,
    }


def test_a_hold_only_plan_is_feasible():
    plan = ActionSet(
        actions=[_action(action_id="h", kind=ActionKind.hold, rationale="already balanced")]
    )
    assert plan.check_feasible(_portfolio(), liquid_assets=0) == []


def test_ordering_is_stable_within_a_sequence():
    plan = ActionSet(
        actions=[
            _action(action_id="second", symbol="NVDA", shares=1, sequence=0),
            _action(action_id="first", symbol="VTI", shares=1, sequence=0),
            _action(action_id="last", symbol="NVDA", shares=1, sequence=5),
        ]
    )
    assert [a.action_id for a in plan.ordered] == ["second", "first", "last"]
