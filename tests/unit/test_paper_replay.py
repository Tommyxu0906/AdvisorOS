"""The offline replay engine: time semantics, the lookahead gate, and path behaviour.

The lookahead tests are the reason this file exists. Everything else here could be wrong and
produce a visibly broken run; a lookahead bug produces a run that looks *better* than it should,
and nothing about the output says so.
"""

from __future__ import annotations

import csv
from datetime import date

import pytest

from app.domain.portfolio import Holding, Portfolio
from app.domain.profile import Asset, Expenses, FinancialProfile, Income
from app.paper.attribution import ActionOrigin
from app.paper.clock import ExecutionRule, ReplayClock, ReplayStep, periodic_decision_dates
from app.paper.comparison import build_providers, compare
from app.paper.engine_only import BASELINE_PROVIDER_ID, DecisionEngineOnlyProvider
from app.paper.frozen_policy import FrozenPolicyProvider
from app.paper.market import LocalHistoricalMarketDataProvider
from app.paper.mock_policy import MockInvestorPolicy
from app.paper.quant_policy import QuantBehaviorProvider
from app.paper.replay import OfflineReplayEngine

FIXTURES = "data/market/fixtures"


# --- fixtures ----------------------------------------------------------------------------


@pytest.fixture(scope="module")
def market() -> LocalHistoricalMarketDataProvider:
    return LocalHistoricalMarketDataProvider.from_directory(FIXTURES)


@pytest.fixture(scope="module")
def sessions(market) -> list[date]:
    return sorted({o.trade_date for series in market.bars.values() for o in series})


def profile(cash: float = 20_000.0) -> FinancialProfile:
    return FinancialProfile(
        age=41,
        income=Income(annual_gross=180_000),
        expenses=Expenses(monthly_essential=7_000),
        assets=[Asset(name="Cash", value=cash, account_type="cash")],
    )


def book(*spec: tuple[str, float]) -> Portfolio:
    return Portfolio(
        holdings=[
            Holding(
                symbol=symbol,
                name=symbol.title(),
                asset_class="us_equity",
                quantity=quantity,
                market_value=quantity * 100.0,
                cost_basis=quantity * 90.0,
                account_type="taxable",
            )
            for symbol, quantity in spec
        ]
    )


def six_position_book() -> Portfolio:
    """Six, so the 1/n floor (16.7%) sits below the house cap and the caps genuinely differ."""
    return book(
        ("RISE", 400), ("FALL", 200), ("FLAT", 150), ("WAVE", 100), ("SLOW", 120), ("DIPS", 90)
    )


def engine_for(market, sessions, *, every: int = 20, end: date = date(2024, 8, 30), cash=20_000.0):
    window = [d for d in sessions if d <= end]
    clock = ReplayClock.build(periodic_decision_dates(window, every), sessions)
    return OfflineReplayEngine(market=market, clock=clock, starting_cash=cash)


# =========================================================================================
# LOOKAHEAD — the hard gate
# =========================================================================================


def test_no_observation_after_as_of_is_ever_visible(market, sessions):
    """The information boundary, asserted directly on the accessor everything else goes through."""
    cutoff = date(2024, 3, 15)
    for symbol in market.bars:
        visible = market._visible(symbol, cutoff)
        assert all(o.trade_date <= cutoff for o in visible), symbol


def test_price_at_a_date_is_the_last_close_at_or_before_it(market):
    cutoff = date(2024, 3, 15)
    price = market.get_price("RISE", cutoff)
    observed = market.observation_date("RISE", cutoff)
    assert price is not None
    assert observed is not None and observed <= cutoff
    # And it is genuinely not tomorrow's price.
    assert price != market.get_price("RISE", date(2024, 3, 22))


def test_a_price_series_built_at_a_date_contains_no_later_return(market):
    cutoff = date(2024, 4, 1)
    early = market.build_price_series("RISE", cutoff, lookback=252)
    late = market.build_price_series("RISE", date(2024, 8, 1), lookback=252)
    assert early is not None and late is not None
    assert len(early.returns) < len(late.returns)
    # Every return the early series carries also appears, in order, at the head of the late one.
    assert late.returns[: len(early.returns)] == pytest.approx(early.returns)


def test_adding_future_rows_cannot_change_an_earlier_decision(tmp_path, market, sessions):
    """The structural lookahead test.

    Copies the fixtures, runs a replay, then appends *later* rows and runs the same replay again.
    A decision dated before the new rows must be untouched. If this fails, something read past
    `as_of`, and every performance number the engine produces is worthless.
    """
    import shutil
    from pathlib import Path

    source = Path(FIXTURES)
    if not source.is_absolute():
        source = Path(__file__).resolve().parents[2] / FIXTURES

    original_dir = tmp_path / "original"
    extended_dir = tmp_path / "extended"
    shutil.copytree(source, original_dir)
    shutil.copytree(source, extended_dir)

    # Append wildly different future prices, well after the replay window. Dates are stepped
    # with timedelta rather than by incrementing a day-of-month, so they stay distinct across
    # the month boundary — the duplicate-date guard in the loader catches the lazy version.
    from datetime import timedelta

    with (extended_dir / "RISE.csv").open("a", newline="") as handle:
        writer = csv.writer(handle)
        cursor = date(2024, 10, 1)
        for i in range(40):
            writer.writerow([cursor.isoformat(), "RISE", 9999.0 + i, 9999.0 + i, "future"])
            cursor += timedelta(days=1)

    def run_for(directory):
        provider_market = LocalHistoricalMarketDataProvider.from_directory(directory)
        provider_sessions = sorted(
            {
                o.trade_date
                for s in provider_market.bars.values()
                for o in s
                if o.trade_date <= date(2024, 8, 30)
            }
        )
        clock = ReplayClock.build(periodic_decision_dates(provider_sessions, 20), provider_sessions)
        engine = OfflineReplayEngine(market=provider_market, clock=clock, starting_cash=20_000)
        return engine.run(profile(), six_position_book(), DecisionEngineOnlyProvider())

    before = run_for(original_dir)
    after = run_for(extended_dir)

    assert [r.equity for r in before.rounds] == [r.equity for r in after.rounds]
    assert [sorted(r.action_ids) for r in before.rounds] == [
        sorted(r.action_ids) for r in after.rounds
    ]


def test_execution_never_precedes_the_decision(market, sessions):
    engine = engine_for(market, sessions)
    for step in engine.clock.steps:
        assert step.execution_date > step.decision_date, (
            "next_close means strictly after; filling at the decision close is the classic "
            "replay bug"
        )


def test_a_step_that_fills_before_it_decides_is_rejected():
    with pytest.raises(ValueError, match="precedes decision"):
        ReplayStep(index=0, decision_date=date(2024, 3, 15), execution_date=date(2024, 3, 14))


def test_same_close_is_available_but_never_the_default():
    assert ReplayClock().rule is ExecutionRule.next_close
    sessions = [date(2024, 1, i) for i in (2, 3, 4, 5)]
    same = ReplayClock.build([date(2024, 1, 3)], sessions, rule=ExecutionRule.same_close)
    assert same.steps[0].execution_date == date(2024, 1, 3)
    assert "intraday" in ExecutionRule.same_close.description


def test_the_quant_provider_sees_only_point_in_time_features(market):
    """Hydration at a date must not leave a longer history than that date supports."""
    early = market.hydrate_portfolio(book(("RISE", 100)), date(2024, 2, 1))
    late = market.hydrate_portfolio(book(("RISE", 100)), date(2024, 7, 1))
    early_series = early.portfolio.price_series[0]
    late_series = late.portfolio.price_series[0]
    assert len(early_series.returns) < len(late_series.returns)


def test_comparison_runs_share_one_market_path_and_starting_state(market, sessions):
    engine = engine_for(market, sessions, every=40)
    result = compare(
        engine, profile(), six_position_book(), build_providers(include=("engine-only", "frozen"))
    )
    assert result.identical_inputs()
    assert len({r.market_data_sha256 for r in result.runs}) == 1
    assert len({r.starting_equity for r in result.runs}) == 1


# =========================================================================================
# TIME AND STATE
# =========================================================================================


def test_round_two_uses_executed_round_one_state(market, sessions):
    """The closed loop. Re-deciding from the starting book was a real bug once."""
    engine = engine_for(market, sessions, every=40)
    run = engine.run(profile(), six_position_book(), FrozenPolicyProvider.from_path())
    assert len(run.rounds) >= 2
    # Round 1 opens from what round 0 executed: if it re-read the original book it would
    # re-propose round 0's trims and the simulator would reject them.
    assert not run.rounds[1].rejections


def test_the_mark_date_follows_execution(market, sessions):
    engine = engine_for(market, sessions, every=40)
    for step in engine.clock.steps:
        if step.next_mark_date is not None:
            assert step.next_mark_date >= step.execution_date


def test_a_replay_is_byte_identical_across_runs(market, sessions):
    engine = engine_for(market, sessions, every=40)
    first = engine.run(profile(), six_position_book(), FrozenPolicyProvider.from_path())
    second = engine.run(profile(), six_position_book(), FrozenPolicyProvider.from_path())
    assert first.digest() == second.digest()


def test_a_comparison_is_reproducible(market, sessions):
    engine = engine_for(market, sessions, every=40)
    providers = build_providers(include=("engine-only", "frozen", "quant", "mock"))
    first = compare(engine, profile(), six_position_book(), providers)
    second = compare(engine, profile(), six_position_book(), build_providers())
    assert first.digest() == second.digest()


def test_a_run_serializes_and_round_trips(market, sessions, tmp_path):
    from app.paper.replay import load_run

    engine = engine_for(market, sessions, every=40)
    run = engine.run(profile(), six_position_book(), DecisionEngineOnlyProvider())
    path = run.write_json(tmp_path / "run.json")
    assert load_run(path).digest() == run.digest()


# =========================================================================================
# PRICE PATHS
# =========================================================================================


def test_a_rising_path_ends_above_where_it_started(market, sessions):
    engine = engine_for(market, sessions, every=40)
    run = engine.run(profile(cash=0.0), book(("RISE", 1000)), DecisionEngineOnlyProvider())
    assert run.ending_equity > run.starting_equity


def test_a_falling_path_ends_below_where_it_started(market, sessions):
    engine = engine_for(market, sessions, every=40)
    run = engine.run(profile(cash=0.0), book(("FALL", 1000)), DecisionEngineOnlyProvider())
    assert run.ending_equity < run.starting_equity


def test_a_flat_path_does_not_move_equity(market, sessions):
    """FLAT is literally constant, so any drift is the engine's arithmetic, not the market."""
    engine = engine_for(market, sessions, every=40)
    run = engine.run(profile(cash=0.0), book(("FLAT", 1000)), DecisionEngineOnlyProvider())
    assert run.ending_equity == pytest.approx(run.starting_equity, rel=1e-6)


def test_a_cyclical_path_records_a_real_drawdown(market, sessions):
    engine = engine_for(market, sessions, every=10)
    run = engine.run(profile(cash=0.0), book(("WAVE", 1000)), DecisionEngineOnlyProvider())
    assert run.metrics is not None
    assert run.metrics.max_drawdown < 0


def test_a_symbol_with_no_local_history_is_reported_not_invented(market, sessions):
    engine = engine_for(market, sessions, every=40)
    run = engine.run(profile(), book(("RISE", 400), ("NOSUCH", 100)), DecisionEngineOnlyProvider())
    assert "NOSUCH" in run.rounds[0].unpriced_symbols
    assert run.rounds[0].priced_coverage < 1.0


def test_a_gap_in_history_does_not_fabricate_sessions(market):
    """GAP is missing two weeks. A read inside the gap returns the last real observation."""
    inside_gap = date(2024, 3, 29)
    observed = market.observation_date("GAP", inside_gap)
    assert observed is not None and observed <= inside_gap
    price = market.get_price("GAP", inside_gap)
    assert price is not None


# =========================================================================================
# PROVIDERS AND ATTRIBUTION
# =========================================================================================


def test_the_baseline_produces_no_investor_originated_action(market, sessions):
    """The whole point of DecisionEngineOnly."""
    engine = engine_for(market, sessions, every=40)
    run = engine.run(profile(), six_position_book(), DecisionEngineOnlyProvider())
    for record in run.rounds:
        assert record.stances == []
        assert record.attribution.investor_originated == []
        assert record.attribution.by_origin(ActionOrigin.investor_policy) == []


def test_the_baseline_is_not_a_persona_that_holds_everything(market, sessions):
    """A wall of holds would be a view. Zero stances is the absence of one."""
    view = DecisionEngineOnlyProvider().decide(profile(), six_position_book())
    assert view.stances == []
    assert view.provider_id == BASELINE_PROVIDER_ID
    assert not view.is_language_model


def test_the_engine_still_acts_under_the_baseline(market, sessions):
    """Removing the investor overlay must not remove the decision engine."""
    engine = engine_for(market, sessions, every=40)
    run = engine.run(profile(), six_position_book(), DecisionEngineOnlyProvider())
    assert any(r.action_set.actions for r in run.rounds)


def test_the_frozen_policy_changes_the_path_through_its_threshold(market, sessions):
    """Its contribution is a parameter, not a trade — and the attribution shows which."""
    engine = engine_for(market, sessions, every=20)
    baseline = engine.run(profile(), six_position_book(), DecisionEngineOnlyProvider())
    frozen = engine.run(profile(), six_position_book(), FrozenPolicyProvider.from_path())

    assert baseline.metrics is not None and frozen.metrics is not None
    # The 25% cap is looser than the house 20%, so it ends more concentrated and trades less.
    assert frozen.metrics.ending_concentration > baseline.metrics.ending_concentration
    assert frozen.metrics.trades < baseline.metrics.trades


def test_an_investor_action_records_both_author_and_constrainer(market, sessions):
    """proposed_by stays the investor; constrained_by names the engine that sized it."""
    engine = engine_for(market, sessions, every=20)
    run = engine.run(profile(), six_position_book(), MockInvestorPolicy())

    composed = [
        a for record in run.rounds for a in record.attribution.by_origin(ActionOrigin.composed)
    ]
    assert composed, "the mock should have produced at least one directional action"
    for attribution in composed:
        assert attribution.proposed_by != "policy"
        assert attribution.constrained_by is not None
        assert attribution.was_constrained


def test_a_refused_action_is_recorded_with_a_reason(market, sessions):
    engine = engine_for(market, sessions, every=20)
    run = engine.run(profile(), six_position_book(), MockInvestorPolicy())
    refused = [r for record in run.rounds for r in record.attribution.refused]
    if refused:
        assert all(r.refused_by and r.reason for r in refused)


def test_the_quant_provider_answers_once_the_portfolio_is_hydrated(market, sessions):
    """The gap this whole pipeline was built to close."""
    engine = engine_for(market, sessions, every=40)
    run = engine.run(profile(), six_position_book(), QuantBehaviorProvider.load())

    assert run.metrics is not None
    assert run.metrics.average_feature_coverage > 0.5, (
        "with hydrated price history the quant provider should stop abstaining on everything"
    )
    answered = [s for record in run.rounds for s in record.stances if not s.abstain]
    assert answered


def test_a_provider_still_abstains_when_history_is_missing(market, sessions):
    """Hydration does not paper over a symbol with no data."""
    engine = engine_for(market, sessions, every=40)
    run = engine.run(
        profile(), book(("NOSUCH", 100), ("ALSONOT", 50)), QuantBehaviorProvider.load()
    )
    assert all(s.abstain for record in run.rounds for s in record.stances)


def test_the_comparison_reports_deltas_against_the_baseline(market, sessions):
    engine = engine_for(market, sessions, every=40)
    result = compare(engine, profile(), six_position_book(), build_providers())
    assert result.baseline is not None
    assert {d.provider_id for d in result.deltas} == {
        r.provider_id for r in result.runs if r.provider_id != BASELINE_PROVIDER_ID
    }
    rendered = result.render()
    assert "not ranked by return" in rendered
    assert "DecisionEngineOnly" in rendered


def test_no_provider_in_the_comparison_claims_to_be_a_language_model(market, sessions):
    engine = engine_for(market, sessions, every=40)
    result = compare(engine, profile(), six_position_book(), build_providers())
    assert all(not run.is_language_model for run in result.runs)


# =========================================================================================
# METRICS
# =========================================================================================


def test_a_short_run_refuses_to_annualize(market, sessions):
    engine = engine_for(market, sessions, every=5, end=date(2024, 1, 31))
    run = engine.run(profile(), six_position_book(), DecisionEngineOnlyProvider())
    assert run.metrics is not None
    assert run.metrics.annualized_return is None


def test_metrics_carry_their_own_disclaimer(market, sessions):
    engine = engine_for(market, sessions, every=40)
    run = engine.run(profile(), six_position_book(), DecisionEngineOnlyProvider())
    assert run.metrics is not None and run.metrics.is_simulation
    assert "not evidence of alpha" in run.disclaimer.lower() or "alpha" in run.disclaimer


def test_coverage_warnings_do_not_fire_for_a_provider_with_no_stances(market, sessions):
    """The baseline is not "low coverage"; it was never asked for a view."""
    engine = engine_for(market, sessions, every=40)
    run = engine.run(profile(), six_position_book(), DecisionEngineOnlyProvider())
    assert run.metrics is not None
    assert not any("coverage" in w for w in run.metrics.warnings())
    assert not any("abstained" in w for w in run.metrics.warnings())
