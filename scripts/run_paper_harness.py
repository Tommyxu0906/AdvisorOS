#!/usr/bin/env python
"""Run the forward paper loop locally. No network, no API key, no cost.

    python scripts/run_paper_harness.py --provider frozen --mode paper_execute --rounds 3

Every provider here is deterministic, so two runs with the same arguments produce the same
output. That is the property the whole harness is for: when something changes between runs, it
changed because the code changed.

**What a run here does and does not show.** It demonstrates that a decision flows through the
policy engine, survives feasibility and counterfactual checks, and settles against a simulator.
It is not a backtest and not evidence that any policy makes money. The simulator fills
everything instantly at the mark, so the P&L of a round is arithmetic about the prices it was
handed, not a market outcome. See `SIMULATION_LIMITS` in `app/paper/mock_broker.py`.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.domain.portfolio import Holding, Portfolio, PriceSeries  # noqa: E402
from app.domain.profile import Asset, Expenses, FinancialProfile, Income  # noqa: E402
from app.paper.broker import HarnessMode  # noqa: E402
from app.paper.frozen_policy import FrozenPolicyProvider  # noqa: E402
from app.paper.harness import (  # noqa: E402
    broker_from_portfolio,
    portfolio_from_account,
    run_once,
)
from app.paper.mock_broker import SIMULATION_LIMITS  # noqa: E402
from app.paper.mock_policy import MockInvestorPolicy  # noqa: E402
from app.paper.quant_policy import QuantBehaviorProvider  # noqa: E402


def demo_profile() -> FinancialProfile:
    return FinancialProfile(
        age=41,
        income=Income(annual_gross=180_000),
        expenses=Expenses(monthly_essential=7_000),
        assets=[Asset(name="Cash", value=25_000, account_type="cash")],
    )


def demo_portfolio(with_prices: bool) -> Portfolio:
    """A concentrated book, because one that trips no policy would demonstrate nothing."""
    import random

    holdings = [
        Holding(
            symbol="NVDA",
            name="NVIDIA",
            asset_class="us_equity",
            quantity=900,
            market_value=270_000,
            cost_basis=40_000,
            account_type="taxable",
        ),
        Holding(
            symbol="AAPL",
            name="Apple",
            asset_class="us_equity",
            quantity=400,
            market_value=90_000,
            cost_basis=55_000,
            account_type="taxable",
        ),
        Holding(
            symbol="VTI",
            name="Vanguard Total Market",
            asset_class="us_equity",
            quantity=200,
            market_value=58_000,
            cost_basis=50_000,
            account_type="taxable",
        ),
        Holding(
            symbol="TINY",
            name="Vestigial position",
            asset_class="us_equity",
            quantity=10,
            market_value=800,
            cost_basis=700,
            account_type="taxable",
        ),
    ]

    series: list[PriceSeries] = []
    if with_prices:
        rng = random.Random(11)
        for symbol, drift in (("NVDA", 0.004), ("AAPL", 0.0005), ("VTI", 0.0003), ("TINY", -0.001)):
            series.append(
                PriceSeries(
                    symbol=symbol,
                    periods_per_year=252,
                    returns=[rng.gauss(drift, 0.02) for _ in range(300)],
                )
            )
    return Portfolio(holdings=holdings, price_series=series)


def build_provider(name: str):
    if name == "mock":
        return MockInvestorPolicy()
    if name == "frozen":
        return FrozenPolicyProvider.from_path()
    if name == "quant":
        return QuantBehaviorProvider.load()
    raise SystemExit(f"unknown provider {name!r}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", choices=("mock", "frozen", "quant"), default="frozen")
    parser.add_argument(
        "--mode",
        choices=tuple(m.value for m in HarnessMode),
        default=HarnessMode.recommend_only.value,
        help="paper_execute is the only mode that submits anything, and only to the simulator",
    )
    parser.add_argument("--rounds", type=int, default=1)
    parser.add_argument("--cash", type=float, default=25_000.0)
    parser.add_argument(
        "--prices",
        action="store_true",
        help="attach a synthetic price history; the quant provider abstains without one",
    )
    args = parser.parse_args()

    mode = HarnessMode(args.mode)
    profile = demo_profile()
    portfolio = demo_portfolio(args.prices or args.provider == "quant")
    provider = build_provider(args.provider)
    broker = broker_from_portfolio(portfolio, cash=args.cash)

    print(f"provider   {provider.display_name}")
    print(f"mode       {mode.value}")
    print(f"rounds     {args.rounds}")
    print()

    for round_number in range(1, args.rounds + 1):
        print("=" * 78)
        print(f"ROUND {round_number}")
        print("=" * 78)

        result = run_once(profile, portfolio, provider, broker, mode=mode)
        print(result.render())

        # Close the loop: the next round decides against what the simulator actually holds.
        # Without this, round 2 re-proposes round 1's trims and every order is rejected.
        if result.account_after is not None and mode.may_execute:
            portfolio = portfolio_from_account(result.account_after, portfolio)

        if result.view is not None:
            print()
            print("stances")
            for stance in result.view.stances:
                verdict = "ABSTAIN" if stance.abstain else stance.action.value
                print(
                    f"  {stance.symbol:<6} {verdict:<9} conf={stance.confidence:<6} {stance.note[:60]}"
                )

        if result.action_set.actions:
            print()
            print("actions")
            for action in result.action_set.ordered:
                print(
                    f"  [{action.sequence:>3}] {action.kind.value:<18} {action.symbol or '-':<6} "
                    f"by={action.proposed_by}"
                )

        print()

    print("-" * 78)
    print("This is a simulator. Its limits, so no number above is read as a backtest:")
    for limit in SIMULATION_LIMITS:
        print(f"  - {limit}")
    print()
    print("Paper trading shows whether the system runs on a live data flow. It cannot show")
    print("whether a policy is any good; only the historical replay speaks to fidelity, and")
    print("neither speaks to whether the advice suits a particular person.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
