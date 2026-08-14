"""Market data parsing and enrichment. No network: every test drives a canned Yahoo payload.

The two behaviours worth locking down are both places where a plausible-looking shortcut
produces a wrong number rather than an error:

  - `previous_close` must come from the bars, not from `meta`. Yahoo omits `previousClose` on a
    multi-day range and its `chartPreviousClose` is the close before the *range* starts, so
    reading either turns a day's move into a year's move.
  - `enrich_portfolio` must not invent a zero-return series for a holding it could not price.
    Zeros would let `_weighted_portfolio_series` produce a portfolio volatility that understates
    reality; dropping the metric is the honest outcome.
"""

from __future__ import annotations

import pytest

from app.domain.portfolio import AssetClass, Holding, Portfolio, PriceSeries
from app.market_data import service
from app.market_data.provider import SymbolNotFound, _parse_result

# One year ago, so nothing here depends on today's date.
_TS = [1_700_000_000 + i * 86_400 for i in range(5)]


def _payload(closes: list[float | None], *, adj: list[float | None] | None = None) -> dict:
    return {
        "meta": {
            "shortName": "Test Corp",
            "currency": "USD",
            "exchangeName": "NMS",
            "regularMarketPrice": 110.0,
            # Deliberately present and wrong: the parser must ignore it.
            "chartPreviousClose": 50.0,
            "regularMarketTime": _TS[-1],
        },
        "timestamp": _TS[: len(closes)],
        "indicators": {
            "quote": [{"close": closes, "open": closes, "high": closes, "low": closes}],
            "adjclose": [{"adjclose": adj if adj is not None else closes}],
        },
    }


def test_previous_close_comes_from_the_bars_not_the_range_start():
    data = _parse_result("TEST", _payload([100.0, 102.0, 101.0, 105.0, 110.0]))
    assert data.previous_close == 105.0  # the bar before the latest, not chartPreviousClose


def test_days_with_no_close_are_skipped_rather_than_interpolated():
    data = _parse_result("TEST", _payload([100.0, None, 101.0, None, 110.0]))
    assert [b.close for b in data.bars] == [100.0, 101.0, 110.0]


def test_a_response_with_no_usable_bars_is_a_missing_symbol():
    with pytest.raises(SymbolNotFound):
        _parse_result("TEST", _payload([None, None]))


def test_returns_are_computed_from_adjusted_close(monkeypatch):
    """A 2:1 split halves `close` but not `adj_close`. Reading `close` would book a -50% day."""
    data = _parse_result(
        "TEST",
        _payload([100.0, 50.0, 51.0], adj=[50.0, 50.0, 51.0]),
    )
    entry = service._CacheEntry(bars=data.bars, price=51.0, previous_close=50.0, as_of=None)

    async def fake_fetch_many(symbols):
        return {"TEST": entry}

    monkeypatch.setattr(service, "_fetch_many", fake_fetch_many)

    import asyncio

    series = asyncio.run(service.get_price_series(["TEST"]))
    assert series["TEST"].returns == [0.0, pytest.approx(0.02)]
    assert series["TEST"].periods_per_year == 252


async def test_enrich_leaves_an_unpriceable_holding_without_a_series(monkeypatch):
    """A private business gets no fake series — the portfolio-level metric is dropped instead."""

    async def only_nvda(symbols):
        return {"NVDA": PriceSeries(symbol="NVDA", periods_per_year=252, returns=[0.01] * 10)}

    monkeypatch.setattr(service, "get_price_series", only_nvda)

    portfolio = Portfolio(
        holdings=[
            Holding(symbol="NVDA", asset_class=AssetClass.us_equity, market_value=1000),
            Holding(symbol="MY LLC", asset_class=AssetClass.other, market_value=500),
        ]
    )
    enriched = await service.enrich_portfolio(portfolio)
    assert {s.symbol for s in enriched.price_series} == {"NVDA"}


async def test_enrich_gives_cash_a_flat_series_so_the_portfolio_metric_survives(monkeypatch):
    """Cash genuinely does not move, so a zero series is a fact rather than a filler."""

    async def only_nvda(symbols):
        return {"NVDA": PriceSeries(symbol="NVDA", periods_per_year=252, returns=[0.01] * 10)}

    monkeypatch.setattr(service, "get_price_series", only_nvda)

    portfolio = Portfolio(
        holdings=[
            Holding(symbol="NVDA", asset_class=AssetClass.us_equity, market_value=1000),
            Holding(symbol="SAVINGS", asset_class=AssetClass.cash, market_value=500),
        ]
    )
    enriched = await service.enrich_portfolio(portfolio)
    cash = enriched.series_for("SAVINGS")
    assert cash is not None
    assert set(cash.returns) == {0.0}
    assert len(cash.returns) == 10


async def test_enrich_never_overwrites_series_the_caller_supplied(monkeypatch):
    async def should_not_run(symbols):
        raise AssertionError("fetched a symbol the caller already had a series for")

    monkeypatch.setattr(service, "get_price_series", should_not_run)

    supplied = PriceSeries(symbol="NVDA", periods_per_year=12, returns=[0.05, -0.02])
    portfolio = Portfolio(
        holdings=[Holding(symbol="NVDA", asset_class=AssetClass.us_equity, market_value=1000)],
        price_series=[supplied],
    )
    enriched = await service.enrich_portfolio(portfolio)
    assert enriched.price_series == [supplied]
