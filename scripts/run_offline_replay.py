#!/usr/bin/env python
"""Offline historical replay. No network, no API key, no brokerage, no cost.

    python scripts/run_offline_replay.py --provider frozen --start 2024-01-02 --end 2024-06-28
    python scripts/run_offline_replay.py --compare-all --start 2024-01-02 --end 2024-08-30

Everything is deterministic: the same arguments over the same market files produce a
byte-identical `ReplayRun`, which `--digest` prints so two runs can be compared directly.

**This is a historical simulation, not a backtest of a tradable strategy.** Fills are instant,
complete, and at the mark. The columns that carry information are turnover, refusals,
concentration, coverage, and abstention — not the return.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT))

from app.domain.portfolio import Holding, Portfolio  # noqa: E402
from app.domain.profile import Asset, Expenses, FinancialProfile, Income  # noqa: E402
from app.paper.clock import ExecutionRule, ReplayClock, periodic_decision_dates  # noqa: E402
from app.paper.comparison import build_providers, compare  # noqa: E402
from app.paper.market import LocalHistoricalMarketDataProvider  # noqa: E402
from app.paper.replay import OfflineReplayEngine  # noqa: E402

DEFAULT_MARKET_DIR = "data/market/fixtures"

PROVIDER_CHOICES = ("mock", "frozen", "quant", "engine-only")


def demo_profile(cash: float) -> FinancialProfile:
    return FinancialProfile(
        age=41,
        income=Income(annual_gross=180_000),
        expenses=Expenses(monthly_essential=7_000),
        assets=[Asset(name="Cash", value=cash, account_type="cash")],
    )


def demo_portfolio() -> Portfolio:
    """Concentrated on the rising name, so the concentration policy has something to do.

    **Six positions on purpose.** With four, the engine's 1/n arithmetic floor is 25% — exactly
    the frozen policy's single-name cap — so the two coincide and the comparison reports a
    difference of zero for a reason that has nothing to do with either policy. At six the floor
    is 16.7%, the house 20% cap binds instead, and the caps genuinely differ.

    Quantities matter, prices do not: hydration marks every holding to the replay's own data, so
    the market values below are placeholders overwritten before the first decision.
    """
    spec = [
        ("RISE", 400),
        ("FALL", 200),
        ("FLAT", 150),
        ("WAVE", 100),
        ("SLOW", 120),
        ("DIPS", 90),
    ]
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


def build_engine(args) -> tuple[OfflineReplayEngine, list[date]]:
    market = LocalHistoricalMarketDataProvider.from_directory(args.market_dir)
    sessions = sorted({o.trade_date for series in market.bars.values() for o in series})

    window = [d for d in sessions if args.start <= d <= args.end]
    if not window:
        raise SystemExit(
            f"no sessions between {args.start} and {args.end}; "
            f"data covers {sessions[0]} to {sessions[-1]}"
        )

    decision_dates = periodic_decision_dates(window, args.every)
    clock = ReplayClock.build(decision_dates, sessions, rule=ExecutionRule(args.rule))
    if not clock.steps:
        raise SystemExit("the schedule produced no steps; widen the range or lower --every")

    engine = OfflineReplayEngine(
        market=market, clock=clock, lookback=args.lookback, starting_cash=args.cash
    )
    return engine, window


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", choices=PROVIDER_CHOICES, default="frozen")
    parser.add_argument("--compare-all", action="store_true", help="run all four modes")
    parser.add_argument("--start", type=date.fromisoformat, default=date(2024, 1, 2))
    parser.add_argument("--end", type=date.fromisoformat, default=date(2024, 8, 30))
    parser.add_argument("--every", type=int, default=20, help="decide every nth session")
    parser.add_argument("--lookback", type=int, default=252)
    parser.add_argument("--cash", type=float, default=20_000.0)
    parser.add_argument("--market-dir", default=DEFAULT_MARKET_DIR)
    parser.add_argument(
        "--rule",
        choices=tuple(r.value for r in ExecutionRule),
        default=ExecutionRule.next_close.value,
    )
    parser.add_argument("--json", help="write the run (or comparison) here")
    parser.add_argument("--digest", action="store_true", help="print the content hash")
    args = parser.parse_args()

    engine, window = build_engine(args)
    profile = demo_profile(args.cash)
    portfolio = demo_portfolio()

    print(f"market     {args.market_dir} ({engine.market.content_sha256[:12]})")
    print(f"sessions   {window[0]} -> {window[-1]} ({len(window)})")
    print(f"steps      {len(engine.clock.steps)} every {args.every} sessions")
    print(f"rule       {engine.clock.rule.value} — {engine.clock.rule.description}")
    print()

    if args.compare_all:
        result = compare(engine, profile, portfolio, build_providers())
        print(result.render())
        if args.digest:
            print()
            print(f"digest     {result.digest()}")
        if args.json:
            Path(args.json).write_text(result.to_json())
            print(f"\nwrote {args.json}")
        return 0

    provider = build_providers(include=(args.provider,))[0]
    run = engine.run(profile, portfolio, provider)

    print(f"provider   {run.provider_display_name}")
    print(f"equity     ${run.starting_equity:,.2f} -> ${run.ending_equity:,.2f}")
    print()

    header = f"{'#':>2}  {'decide':<12}{'exec':<12}{'acts':>5}{'fills':>6}{'rej':>5}{'equity':>13}{'conc':>7}{'cover':>7}"
    print(header)
    print("-" * len(header))
    for record in run.rounds:
        print(
            f"{record.index:>2}  {record.decision_date!s:<12}{record.execution_date!s:<12}"
            f"{len(record.action_set.actions):>5}{len(record.fills):>6}{len(record.rejections):>5}"
            f"{record.equity:>13,.2f}{record.largest_weight:>7.1%}{record.feature_coverage:>7.0%}"
        )

    metrics = run.metrics
    if metrics is not None:
        print()
        print(f"return     {metrics.cumulative_return:.2%}   maxDD {metrics.max_drawdown:.2%}")
        print(f"turnover   {metrics.turnover:.2f}x over {metrics.trades} trades")
        print(f"refused    {metrics.refused_actions}   rejected {metrics.rejected_actions}")
        for warning in metrics.warnings():
            print(f"  !! {warning}")

    if args.digest:
        print()
        print(f"digest     {run.digest()}")
    if args.json:
        run.write_json(args.json)
        print(f"\nwrote {args.json}")

    print()
    print(run.disclaimer)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
