"""The forward loop, its three modes, and the plan-level checks that catch what per-action ones miss."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.distillation.finance_nuwa.prediction import BehavioralAction
from app.domain.action import ActionKind
from app.paper.broker import HarnessMode, OrderSide, PaperOrder, PaperPosition
from app.paper.frozen_policy import FrozenPolicyProvider
from app.paper.harness import (
    broker_from_portfolio,
    portfolio_from_account,
    run_once,
    to_orders,
)
from app.paper.mock_broker import MockPaperBroker
from app.paper.mock_policy import MockInvestorPolicy
from app.paper.provider import InvestorStance, InvestorView, held_symbols
from tests.unit.paper_fixtures import sample_portfolio, sample_profile

# --- the stance type ---------------------------------------------------------------------


def test_a_stance_cannot_both_abstain_and_answer():
    """Recording both would let a scorer keep whichever turned out right."""
    with pytest.raises(ValidationError, match="different answers|abstain"):
        InvestorStance(symbol="AAPL", abstain=True, action=BehavioralAction.hold)


def test_silence_must_be_declared_as_abstention_rather_than_left_blank():
    """An omitted action and a stated abstention are different claims; only one is auditable."""
    with pytest.raises(ValidationError, match="abstaining"):
        InvestorStance(symbol="AAPL")


def test_abstaining_is_not_holding():
    """At these class balances, folding abstention into hold hands out free correct answers."""
    abstained = InvestorStance(symbol="AAPL", abstain=True)
    assert abstained.action is not BehavioralAction.hold
    assert not abstained.is_actionable

    view = InvestorView(
        provider_id="p",
        display_name="P",
        stances=[abstained, InvestorStance(symbol="MSFT", action=BehavioralAction.hold)],
    )
    assert view.coverage == 0.5
    assert len(view.abstentions) == 1


def test_held_symbols_aggregates_the_same_stock_across_accounts():
    """One investment decision, two tax situations — not two chances to answer differently."""
    portfolio = sample_portfolio()
    portfolio.holdings.append(portfolio.holdings[0].model_copy(update={"account_type": "roth_ira"}))
    assert held_symbols(portfolio).count("NVDA") == 1


# --- modes -------------------------------------------------------------------------------


def test_observe_only_proposes_nothing_and_touches_nothing():
    profile, portfolio = sample_profile(), sample_portfolio()
    broker = broker_from_portfolio(portfolio, cash=25_000)
    before = broker.snapshot()

    result = run_once(
        profile, portfolio, MockInvestorPolicy(), broker, mode=HarnessMode.observe_only
    )

    assert result.view is None
    assert result.action_set.actions == []
    assert result.submitted == []
    assert broker.snapshot() == before


def test_recommend_only_produces_a_checked_plan_but_submits_nothing():
    profile, portfolio = sample_profile(), sample_portfolio()
    broker = broker_from_portfolio(portfolio, cash=25_000)
    before = broker.snapshot()

    result = run_once(
        profile,
        portfolio,
        FrozenPolicyProvider.from_path(),
        broker,
        mode=HarnessMode.recommend_only,
    )

    assert result.action_set.actions, "a 65% position should have produced something"
    assert result.counterfactual is not None
    assert result.submitted == []
    assert broker.snapshot() == before, "recommend-only must not move the account"


def test_paper_execute_without_a_broker_reports_rather_than_pretending():
    profile, portfolio = sample_profile(), sample_portfolio()
    result = run_once(
        profile, portfolio, MockInvestorPolicy(), None, mode=HarnessMode.paper_execute
    )
    assert result.submitted == []
    assert any("no broker" in n for n in result.notes)


def test_an_infeasible_plan_is_not_submitted():
    """A simulator that accepted it would hide the bug the feasibility check exists to find."""
    profile, portfolio = sample_profile(), sample_portfolio()
    broker = broker_from_portfolio(portfolio, cash=25_000)

    result = run_once(
        profile, portfolio, MockInvestorPolicy(), broker, mode=HarnessMode.paper_execute
    )
    if result.infeasibilities:
        assert result.submitted == []
        assert any("feasibility" in n for n in result.notes)


# --- the plan-level check ----------------------------------------------------------------


def test_a_stance_may_not_undo_the_deconcentration_the_trims_achieved():
    """The bug this test was written for.

    Sizing a stance against the pre-trim book lets a buy re-concentrate the portfolio the engine
    just spread out: with three positions the trims land everything on the 1/n floor of 33%, and
    a purchase sized on the old total takes one name back to 43%. Every action individually
    "worked", so the per-action counterfactual passes and the plan is still incoherent.
    """
    profile = sample_profile()
    portfolio = sample_portfolio()
    # Drop the vestigial position so the 1/n floor binds at three names.
    portfolio.holdings = [h for h in portfolio.holdings if h.symbol != "TINY"]

    class AlwaysIncreaseTheSmallest:
        provider_id = "test_increase"
        display_name = "Always increase the smallest"

        def decide(self, profile, portfolio):
            return InvestorView(
                provider_id=self.provider_id,
                display_name=self.display_name,
                stances=[
                    InvestorStance(symbol="NVDA", action=BehavioralAction.hold),
                    InvestorStance(symbol="AAPL", action=BehavioralAction.hold),
                    InvestorStance(symbol="VTI", action=BehavioralAction.increase),
                ],
            )

    broker = broker_from_portfolio(portfolio, cash=100_000)
    result = run_once(
        profile, portfolio, AlwaysIncreaseTheSmallest(), broker, mode=HarnessMode.paper_execute
    )

    assert any("exceed" in n and "ceiling" in n for n in result.notes), (
        "the refusal should be reported, not silently dropped"
    )
    assert not any(a.kind is ActionKind.add_position for a in result.action_set.actions)

    after = result.account_after
    total = after.positions_value
    assert total > 0
    largest = max(p.market_value / total for p in after.positions)
    assert largest <= 0.34, f"the plan re-concentrated to {largest:.1%}"


def test_the_engine_trim_wins_over_a_stance_on_the_same_symbol():
    """Emitting both would try to sell the same shares twice."""
    profile, portfolio = sample_profile(), sample_portfolio()
    result = run_once(profile, portfolio, FrozenPolicyProvider.from_path())

    by_symbol: dict[str, int] = {}
    for action in result.action_set.actions:
        if action.symbol:
            by_symbol[action.symbol] = by_symbol.get(action.symbol, 0) + 1
    assert all(count == 1 for count in by_symbol.values()), by_symbol


# --- orders ------------------------------------------------------------------------------


def test_orders_carry_the_action_that_produced_them():
    """A fill nobody can attribute is a fill nobody can review."""
    profile, portfolio = sample_profile(), sample_portfolio()
    result = run_once(profile, portfolio, FrozenPolicyProvider.from_path())
    orders = to_orders(result.action_set, portfolio)

    ids = {a.action_id for a in result.action_set.actions}
    assert orders
    assert all(o.action_id in ids for o in orders)


def test_a_hold_never_becomes_an_order():
    profile, portfolio = sample_profile(), sample_portfolio()
    result = run_once(profile, portfolio, MockInvestorPolicy())
    holds = {a.action_id for a in result.action_set.actions if a.kind is ActionKind.hold}
    orders = to_orders(result.action_set, portfolio)
    assert not (holds & {o.action_id for o in orders})


def test_the_simulator_is_seeded_from_the_book_the_decision_was_made_on():
    """Otherwise every rejection is an artefact of the mismatch rather than a finding."""
    portfolio = sample_portfolio()
    broker = broker_from_portfolio(portfolio, cash=1_000)
    account = broker.get_account()

    assert account.cash == 1_000
    assert {p.symbol for p in account.positions} == {"NVDA", "AAPL", "VTI", "TINY"}
    assert account.positions_value == pytest.approx(portfolio.total_value, rel=1e-6)


# --- the broker itself -------------------------------------------------------------------


def test_the_broker_refuses_to_sell_what_is_not_held():
    broker = MockPaperBroker(cash=0.0, prices={"AAPL": 100.0})
    _, rejected = broker.submit(
        [
            PaperOrder(
                client_order_id="o1", symbol="AAPL", side=OrderSide.sell, quantity=1, action_id="a"
            )
        ]
    )
    assert len(rejected) == 1
    assert "not held" in rejected[0].reason


def test_the_broker_refuses_to_spend_cash_it_does_not_have():
    broker = MockPaperBroker(cash=50.0, prices={"AAPL": 100.0})
    _, rejected = broker.submit(
        [
            PaperOrder(
                client_order_id="o1", symbol="AAPL", side=OrderSide.buy, quantity=10, action_id="a"
            )
        ]
    )
    assert len(rejected) == 1
    assert "cash" in rejected[0].reason


def test_the_broker_will_not_invent_a_price():
    broker = MockPaperBroker(cash=10_000.0, prices={})
    _, rejected = broker.submit(
        [
            PaperOrder(
                client_order_id="o1", symbol="AAPL", side=OrderSide.buy, quantity=1, action_id="a"
            )
        ]
    )
    assert "no reference price" in rejected[0].reason


def test_a_sale_settles_before_the_purchase_that_spends_it():
    """Sequential settlement is what makes a sell-to-fund-a-buy plan work, or visibly not."""
    broker = MockPaperBroker(
        cash=0.0,
        positions={"AAPL": PaperPosition(symbol="AAPL", quantity=10, average_price=100.0)},
        prices={"AAPL": 100.0, "MSFT": 50.0},
    )
    fills, rejected = broker.submit(
        [
            PaperOrder(
                client_order_id="s", symbol="AAPL", side=OrderSide.sell, quantity=10, action_id="a1"
            ),
            PaperOrder(
                client_order_id="b", symbol="MSFT", side=OrderSide.buy, quantity=20, action_id="a2"
            ),
        ]
    )
    assert not rejected, [r.reason for r in rejected]
    assert len(fills) == 2
    assert broker.cash == pytest.approx(0.0)


def test_a_frictionless_fill_leaves_equity_unchanged():
    """Not a claim about markets — a check that the simulator's own arithmetic balances."""
    portfolio = sample_portfolio()
    broker = broker_from_portfolio(portfolio, cash=10_000)
    before = broker.get_account().equity

    broker.submit(
        [
            PaperOrder(
                client_order_id="o", symbol="NVDA", side=OrderSide.sell, quantity=100, action_id="a"
            )
        ]
    )
    assert broker.get_account().equity == pytest.approx(before, rel=1e-6)


# --- the closed loop ---------------------------------------------------------------------


def test_executed_state_feeds_the_next_round():
    """Without this a second round re-proposes the first round's trims and is all rejections."""
    profile, portfolio = sample_profile(), sample_portfolio()
    broker = broker_from_portfolio(portfolio, cash=25_000)

    first = run_once(
        profile, portfolio, FrozenPolicyProvider.from_path(), broker, mode=HarnessMode.paper_execute
    )
    assert first.fills and not first.rejections

    updated = portfolio_from_account(first.account_after, portfolio)
    second = run_once(
        profile, updated, FrozenPolicyProvider.from_path(), broker, mode=HarnessMode.paper_execute
    )

    assert not second.rejections, [r.reason for r in second.rejections]
    assert "TINY" not in {h.symbol for h in updated.holdings}, "the exit should have removed it"


def test_rebuilding_the_portfolio_preserves_reference_data_execution_cannot_change():
    """Asset class and display name are not outputs of a trade."""
    portfolio = sample_portfolio()
    broker = broker_from_portfolio(portfolio, cash=0)
    rebuilt = portfolio_from_account(broker.get_account(), portfolio)

    original = {h.symbol: h for h in portfolio.holdings}
    for holding in rebuilt.holdings:
        assert holding.asset_class == original[holding.symbol].asset_class
        assert holding.name == original[holding.symbol].name


def test_rebuilding_round_trips_the_portfolio_value():
    portfolio = sample_portfolio()
    broker = broker_from_portfolio(portfolio, cash=0)
    rebuilt = portfolio_from_account(broker.get_account(), portfolio)
    assert rebuilt.total_value == pytest.approx(portfolio.total_value, rel=1e-6)


def test_the_loop_converges_rather_than_trading_forever():
    """A policy that kept proposing trades against a book it already fixed would be a bug."""
    profile, portfolio = sample_profile(), sample_portfolio()
    broker = broker_from_portfolio(portfolio, cash=25_000)

    counts = []
    for _ in range(5):
        result = run_once(
            profile,
            portfolio,
            FrozenPolicyProvider.from_path(),
            broker,
            mode=HarnessMode.paper_execute,
        )
        counts.append(len(result.action_set.actions))
        portfolio = portfolio_from_account(result.account_after, portfolio)

    assert counts[-1] <= counts[0], f"action count did not settle: {counts}"
