"""Shared fixtures for the paper harness tests.

A concentrated book on purpose: NVDA at roughly 65% is what makes the concentration policy
produce something, and a portfolio that triggers no policy would test the plumbing and none of
the decisions running through it.
"""

from __future__ import annotations

import random

from app.domain.portfolio import Holding, Portfolio, PriceSeries
from app.domain.profile import Asset, Expenses, FinancialProfile, Income


def sample_profile(cash: float = 25_000.0) -> FinancialProfile:
    return FinancialProfile(
        age=41,
        income=Income(annual_gross=180_000),
        expenses=Expenses(monthly_essential=7_000),
        assets=[Asset(name="Cash", value=cash, account_type="cash")],
    )


def sample_portfolio(with_prices: bool = False) -> Portfolio:
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
