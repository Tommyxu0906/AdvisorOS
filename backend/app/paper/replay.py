"""The offline historical replay engine.

One round, in order:

    1. rebuild the portfolio from what the simulator actually holds
    2. hydrate only what was observable at `decision_date`
    3. ask the provider for an InvestorView
    4. compute_scenario() on those thresholds
    5. merge stances against the post-trim book, attribute every action
    6. execute at the close named by the execution rule
    7. mark the result at `next_mark_date`
    8. record the round, and feed the executed state into the next one

The loop never re-reads the starting portfolio. That was a real bug in the first paper harness —
round two decided against the original book while the broker had already moved, re-proposed
round one's trims, and the simulator correctly rejected every order. `state_t -> decision_t ->
execution_t -> state_t+1` is the invariant, and `test_round_two_uses_executed_round_one_state`
is what keeps it.

**On the information boundary.** The engine never touches raw market data. It asks
`MarketFeatureProvider` for a hydrated portfolio at `decision_date`, and that provider filters
every read on `as_of`. There is no accessor here that could see past it, which is what makes
`test_adding_future_rows_cannot_change_an_earlier_decision` a structural claim rather than a
spot check.

**Marking versus deciding.** Prices at `next_mark_date` are used only to value the resulting
portfolio, never to decide anything — the next round re-hydrates at its own `decision_date`.
Equity is what the book was worth; it is not an input to any decision that produced it.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from app.analytics.counterfactual import evaluate as evaluate_counterfactual
from app.analytics.guardrails import evaluate_guardrails
from app.analytics.portfolio_analytics import analyze_portfolio
from app.analytics.profile_analytics import analyze_profile
from app.domain.action import ActionSet, Infeasibility
from app.domain.portfolio import Portfolio
from app.domain.profile import FinancialProfile
from app.paper.attribution import (
    ActionOrigin,
    AttributionSet,
    RefusedAction,
    attribute,
)
from app.paper.broker import PaperFill, PaperOrder, RejectedOrder
from app.paper.clock import ExecutionRule, ReplayClock, ReplayStep
from app.paper.harness import _merge, broker_from_portfolio, portfolio_from_account, to_orders
from app.paper.market import LocalHistoricalMarketDataProvider
from app.paper.metrics import SIMULATION_DISCLAIMER, PathMetrics, compute_path_metrics
from app.paper.mock_broker import MockPaperBroker
from app.paper.provider import InvestorDecisionProvider, InvestorStance, InvestorView
from app.policy.engine import compute_scenario

REPLAY_VERSION = "offline-replay-v1"


class RoundRecord(BaseModel):
    """One replay round, in full. Structured rather than logged, so it can be diffed."""

    model_config = ConfigDict(extra="forbid")

    index: int
    decision_date: date
    execution_date: date
    next_mark_date: date | None = None

    # --- what was visible
    priced_coverage: float = 0.0
    unpriced_symbols: list[str] = Field(default_factory=list)
    stale_symbols: list[str] = Field(default_factory=list)
    feature_coverage: float = 0.0

    # --- what the provider said
    stances: list[InvestorStance] = Field(default_factory=list)
    abstentions: int = 0

    # --- what the engine decided
    scenario_headline: str = ""
    scenario_worth_showing: bool = False
    counterfactual_holds_up: bool | None = None
    sensitivity_fragile: bool | None = None
    guardrails: list[str] = Field(default_factory=list)

    action_set: ActionSet = Field(default_factory=ActionSet)
    attribution: AttributionSet = Field(default_factory=AttributionSet)
    infeasibilities: list[Infeasibility] = Field(default_factory=list)

    # --- what happened
    submitted: list[PaperOrder] = Field(default_factory=list)
    fills: list[PaperFill] = Field(default_factory=list)
    rejections: list[RejectedOrder] = Field(default_factory=list)

    # --- resulting state, marked at next_mark_date
    cash: float = 0.0
    positions_value: float = 0.0
    equity: float = 0.0
    largest_weight: float = 0.0
    cash_weight: float = 0.0
    traded_notional: float = 0.0

    notes: list[str] = Field(default_factory=list)

    @property
    def action_ids(self) -> set[str]:
        return {a.action_id for a in self.action_set.actions}


class ReplayRun(BaseModel):
    """A complete run: what produced it, what happened, and what it does not prove."""

    model_config = ConfigDict(extra="forbid")

    replay_version: str = REPLAY_VERSION
    disclaimer: str = SIMULATION_DISCLAIMER

    provider_id: str
    provider_display_name: str
    provider_determinism_key: str = ""
    is_language_model: bool = False

    market_data_provider: str = ""
    market_data_sha256: str = ""
    execution_rule: ExecutionRule = ExecutionRule.next_close
    execution_rule_description: str = ""

    start_date: date | None = None
    end_date: date | None = None
    starting_equity: float = 0.0
    ending_equity: float = 0.0

    rounds: list[RoundRecord] = Field(default_factory=list)
    metrics: PathMetrics | None = None

    def to_json(self, indent: int = 2) -> str:
        return self.model_dump_json(indent=indent)

    def write_json(self, path: str | Path) -> Path:
        resolved = Path(path)
        resolved.parent.mkdir(parents=True, exist_ok=True)
        resolved.write_text(self.to_json())
        return resolved

    def digest(self) -> str:
        """Content hash of the run, for asserting two runs are byte-identical."""
        import hashlib

        return hashlib.sha256(self.to_json().encode()).hexdigest()


class OfflineReplayEngine(BaseModel):
    """Reusable, deterministic, offline. No network, no credentials, no clock of its own."""

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    market: LocalHistoricalMarketDataProvider
    clock: ReplayClock
    lookback: int = Field(default=252, gt=0)
    starting_cash: float = 0.0

    def run(
        self,
        profile: FinancialProfile,
        starting_portfolio: Portfolio,
        provider: InvestorDecisionProvider,
    ) -> ReplayRun:
        if not self.clock.steps:
            raise ValueError("the replay clock has no steps; check the date range against the data")

        broker = self._seed_broker(starting_portfolio)
        run = ReplayRun(
            provider_id=provider.provider_id,
            provider_display_name=provider.display_name,
            market_data_provider=self.market.provider_id,
            market_data_sha256=self.market.content_sha256,
            execution_rule=self.clock.rule,
            execution_rule_description=self.clock.rule.description,
            start_date=self.clock.start_date,
            end_date=self.clock.end_date,
        )

        # The opening mark, taken before any decision runs. The curve has to start here rather
        # than at the end of round 0, or the reported return silently excludes the first round —
        # which is the round that does the most trading.
        opening_equity = self._equity_at(broker, self.clock.steps[0].decision_date)

        equity_curve: list[float] = [opening_equity]
        cash_weights: list[float] = []
        concentrations: list[float] = []
        coverages: list[float] = []
        traded_notional = 0.0
        trades = rejected = refused = abstentions = stance_count = 0
        investor_actions = engine_actions = 0

        template = starting_portfolio
        for step in self.clock.steps:
            record, template, view = self._run_step(step, profile, template, provider, broker)
            run.rounds.append(record)

            # Taken from the first round's own view rather than by calling the provider again:
            # a second call would decide against the final book and could report a different key.
            if step.index == 0:
                run.provider_determinism_key = view.determinism_key
                run.is_language_model = view.is_language_model

            equity_curve.append(record.equity)
            cash_weights.append(record.cash_weight)
            concentrations.append(record.largest_weight)
            coverages.append(record.feature_coverage)

            traded_notional += record.traded_notional
            trades += len(record.fills)
            rejected += len(record.rejections)
            refused += len(record.attribution.refused)
            abstentions += record.abstentions
            stance_count += len(record.stances)
            investor_actions += len(record.attribution.investor_originated)
            engine_actions += len(record.attribution.by_origin(ActionOrigin.decision_engine))

        run.starting_equity = round(equity_curve[0], 2)
        run.ending_equity = round(equity_curve[-1], 2)

        days = 0
        if run.start_date and run.end_date:
            days = (run.end_date - run.start_date).days

        run.metrics = compute_path_metrics(
            provider_id=provider.provider_id,
            provider_display_name=provider.display_name,
            equity_curve=equity_curve,
            cash_weights=cash_weights,
            concentrations=concentrations,
            traded_notional=traded_notional,
            trades=trades,
            rejected_actions=rejected,
            refused_actions=refused,
            abstentions=abstentions,
            stance_count=stance_count,
            feature_coverages=coverages,
            investor_actions=investor_actions,
            engine_actions=engine_actions,
            days=days,
        )
        return run

    # --- one round ----------------------------------------------------------------------

    def _run_step(
        self,
        step: ReplayStep,
        profile: FinancialProfile,
        template: Portfolio,
        provider: InvestorDecisionProvider,
        broker: MockPaperBroker,
    ) -> tuple[RoundRecord, Portfolio, InvestorView]:
        record = RoundRecord(
            index=step.index,
            decision_date=step.decision_date,
            execution_date=step.execution_date,
            next_mark_date=step.next_mark_date,
        )

        # 1. state_t comes from the simulator, never from the starting portfolio.
        current = portfolio_from_account(broker.get_account(), template)

        # 2. only what was observable at the decision date.
        hydrated = self.market.hydrate_portfolio(
            current, step.decision_date, lookback=self.lookback
        )
        decision_portfolio = hydrated.portfolio
        record.priced_coverage = hydrated.priced_coverage
        record.unpriced_symbols = hydrated.unpriced
        record.stale_symbols = hydrated.stale

        analytics = analyze_profile(profile)
        portfolio_analytics = (
            analyze_portfolio(decision_portfolio) if decision_portfolio.holdings else None
        )
        guardrails = evaluate_guardrails(profile, analytics)
        record.guardrails = [g.code for g in guardrails]

        # 3. the provider's view of that book.
        view = provider.decide(profile, decision_portfolio)
        record.stances = list(view.stances)
        record.abstentions = len(view.abstentions)
        record.feature_coverage = view.coverage

        # 4. the deterministic engine, on this provider's thresholds.
        scenario = compute_scenario(
            profile,
            analytics,
            decision_portfolio,
            portfolio_analytics,
            guardrails,
            policy_profile=view.policy,
            advisor_id=view.provider_id,
            display_name=view.display_name,
        )
        record.scenario_headline = scenario.headline
        record.scenario_worth_showing = scenario.worth_showing
        record.sensitivity_fragile = scenario.sensitivity.fragile if scenario.sensitivity else None

        # 5. merge and attribute.
        merged, merge_notes = _merge(scenario.action_set, view, profile, decision_portfolio)
        record.action_set = merged
        record.notes.extend(merge_notes)
        record.attribution = AttributionSet(
            attributions=attribute(merged, view.provider_id),
            refused=_refusals(merge_notes, view.provider_id),
        )
        record.infeasibilities = merged.check_feasible(decision_portfolio, analytics.liquid_assets)
        counterfactual = evaluate_counterfactual(profile, decision_portfolio, merged)
        record.counterfactual_holds_up = counterfactual.holds_up

        # 6. execute at the close the rule names, not the one the decision saw.
        if not record.infeasibilities:
            broker.prices = self._prices_at(decision_portfolio, step.execution_date)
            orders = to_orders(merged, decision_portfolio)
            record.submitted = orders
            fills, rejections = broker.submit(orders)
            record.fills = fills
            record.rejections = rejections
            record.traded_notional = round(sum(f.notional for f in fills), 2)
        else:
            record.notes.append(
                "not submitted: the plan failed its own feasibility check, and a simulator that "
                "accepted it would hide the bug the check exists to find"
            )

        # 7. mark the result. Valuation only — no decision reads these prices.
        mark_date = step.next_mark_date or step.execution_date
        executed = portfolio_from_account(broker.get_account(), decision_portfolio)
        marked = self.market.hydrate_portfolio(
            executed, mark_date, lookback=self.lookback
        ).portfolio
        broker.prices = self._prices_at(marked, mark_date)

        account = broker.get_account()
        positions_value = round(
            sum(
                (self.market.get_price(p.symbol, mark_date) or p.average_price) * p.quantity
                for p in account.positions
            ),
            2,
        )
        record.cash = round(account.cash, 2)
        record.positions_value = positions_value
        record.equity = round(account.cash + positions_value, 2)
        record.cash_weight = round(account.cash / record.equity, 6) if record.equity > 0 else 0.0
        record.largest_weight = _largest_weight(marked)

        return record, marked, view

    # --- helpers ------------------------------------------------------------------------

    def _seed_broker(self, portfolio: Portfolio) -> MockPaperBroker:
        first = self.clock.steps[0]
        hydrated = self.market.hydrate_portfolio(
            portfolio, first.decision_date, lookback=self.lookback
        )
        broker = broker_from_portfolio(hydrated.portfolio, cash=self.starting_cash)
        broker.prices = self._prices_at(hydrated.portfolio, first.decision_date)
        return broker

    def _equity_at(self, broker: MockPaperBroker, as_of: date) -> float:
        """Cash plus positions marked at one date. Valuation only; no decision reads this."""
        account = broker.get_account()
        positions = sum(
            (self.market.get_price(p.symbol, as_of) or p.average_price) * p.quantity
            for p in account.positions
        )
        return round(account.cash + positions, 2)

    def _prices_at(self, portfolio: Portfolio, as_of: date) -> dict[str, float]:
        """Reference prices the simulator fills against. One date, no mixing."""
        prices: dict[str, float] = {}
        for holding in portfolio.holdings:
            symbol = holding.symbol.strip().upper()
            if not symbol:
                continue
            price = self.market.get_price(symbol, as_of)
            if price is not None:
                prices[symbol] = price
        return prices


def _largest_weight(portfolio: Portfolio) -> float:
    total = portfolio.total_value
    if total <= 0:
        return 0.0
    values: dict[str, float] = {}
    for holding in portfolio.holdings:
        symbol = holding.symbol.strip().upper()
        if symbol:
            values[symbol] = values.get(symbol, 0.0) + holding.market_value
    return round(max(values.values()) / total, 6) if values else 0.0


def _refusals(notes: list[str], provider_id: str) -> list[RefusedAction]:
    """Turn the merge's refusal notes into structured records.

    The note text is authored in `harness._merge`; parsing it back is not ideal, and the
    alternative — having `_merge` return structured refusals — would change a signature the
    existing paper tests pin. Recorded here as a known seam rather than left implicit.
    """
    out: list[RefusedAction] = []
    for note in notes:
        if "ceiling" not in note or ":" not in note:
            continue
        symbol = note.split(":", 1)[0].strip()
        out.append(
            RefusedAction(
                symbol=symbol,
                proposed_by=provider_id,
                refused_by="decision engine",
                reason=note.split(":", 1)[1].strip(),
            )
        )
    return out


def load_run(path: str | Path) -> ReplayRun:
    return ReplayRun.model_validate(json.loads(Path(path).read_text()))
