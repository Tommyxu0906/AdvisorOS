"""Orchestrates the free Yahoo chart provider with a Postgres cache.

Every public function here degrades to "skip this symbol" rather than raising: a market-data
hiccup must never turn a portfolio analysis into a 500, and a symbol the provider doesn't
recognize (a private holding entered as a plain name, "CASH", a typo) is simply left unpriced
rather than failing the whole request. Callers see this as an absent dict entry, never an
exception — including when there is no database configured at all, in which case every fetch is
just done live with no cache.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from app.db import pool
from app.db.pool import StorageUnavailable
from app.db.repositories import market_data as repo
from app.domain.portfolio import AssetClass, Portfolio, PriceSeries
from app.market_data import provider
from app.market_data.provider import Bar

logger = logging.getLogger(__name__)

# One fetch refreshes both the daily-bar cache and the latest quote, so a single TTL governs
# both. 15 minutes matches the "delayed, not real-time" framing shown in the UI.
_REFRESH_TTL = timedelta(minutes=15)
_TRADING_DAYS_PER_YEAR = 252
# Politeness limit against a free, unauthenticated, unofficial endpoint — not a provider
# requirement, just good citizenship for a hobby-scale app sharing one IP.
_MAX_CONCURRENT_FETCHES = 4


@dataclass(frozen=True)
class QuoteInfo:
    symbol: str
    price: float
    previous_close: float | None
    change_pct: float | None
    as_of: datetime
    is_delayed: bool
    source: str


@dataclass(frozen=True)
class _CacheEntry:
    bars: list[Bar]
    price: float | None
    previous_close: float | None
    as_of: datetime


async def get_price_series(symbols: Iterable[str]) -> dict[str, PriceSeries]:
    """Best-effort daily-return series for each symbol that the provider actually priced."""
    entries = await _fetch_many(_normalize(symbols))
    out: dict[str, PriceSeries] = {}
    for symbol, entry in entries.items():
        if entry is None or len(entry.bars) < 3:
            continue
        returns = [
            cur.adj_close / prev.adj_close - 1
            for prev, cur in zip(entry.bars, entry.bars[1:], strict=False)
            if prev.adj_close > 0
        ]
        if len(returns) >= 2:
            out[symbol] = PriceSeries(
                symbol=symbol, periods_per_year=_TRADING_DAYS_PER_YEAR, returns=returns
            )
    return out


async def get_quotes(symbols: Iterable[str]) -> dict[str, QuoteInfo]:
    """Best-effort delayed quote for each symbol that the provider actually priced."""
    entries = await _fetch_many(_normalize(symbols))
    out: dict[str, QuoteInfo] = {}
    for symbol, entry in entries.items():
        if entry is None or entry.price is None:
            continue
        change_pct = (
            (entry.price - entry.previous_close) / entry.previous_close
            if entry.previous_close and entry.previous_close > 0
            else None
        )
        out[symbol] = QuoteInfo(
            symbol=symbol,
            price=entry.price,
            previous_close=entry.previous_close,
            change_pct=change_pct,
            as_of=entry.as_of,
            is_delayed=True,
            source="yahoo_chart",
        )
    return out


async def enrich_portfolio(portfolio: Portfolio) -> Portfolio:
    """Return a copy of `portfolio` carrying real daily-return series for its holdings.

    Existing `price_series` supplied by the caller win — nothing fetched here overwrites data the
    user provided themselves.

    The subtlety worth knowing: `_weighted_portfolio_series` in analytics/portfolio_analytics.py
    drops portfolio-level volatility and drawdown entirely unless *every* holding has a series.
    A cash position gets a flat zero-return series here, because cash genuinely does not move —
    that is a statement of fact, not a filler. Anything else the provider could not price is left
    without a series on purpose: inventing zeros for a private business or a mistyped symbol
    would understate portfolio volatility, and silently dropping the metric is the honest failure.
    """
    if not portfolio.holdings:
        return portfolio

    have = {s.symbol for s in portfolio.price_series}
    wanted = [h.symbol for h in portfolio.holdings if h.symbol not in have]
    if not wanted:
        return portfolio

    fetched = await get_price_series(wanted)
    if not fetched:
        return portfolio

    flat_length = max(len(s.returns) for s in fetched.values())
    added: list[PriceSeries] = list(fetched.values())
    for h in portfolio.holdings:
        if h.symbol in have or h.symbol in fetched or h.asset_class is not AssetClass.cash:
            continue
        added.append(
            PriceSeries(
                symbol=h.symbol,
                periods_per_year=_TRADING_DAYS_PER_YEAR,
                returns=[0.0] * flat_length,
            )
        )

    return portfolio.model_copy(update={"price_series": [*portfolio.price_series, *added]})


def _normalize(symbols: Iterable[str]) -> list[str]:
    return sorted({s.strip().upper() for s in symbols if s and s.strip()})


async def _fetch_many(symbols: list[str]) -> dict[str, _CacheEntry | None]:
    sem = asyncio.Semaphore(_MAX_CONCURRENT_FETCHES)

    async def bound(symbol: str) -> tuple[str, _CacheEntry | None]:
        async with sem:
            return symbol, await _get_or_refresh(symbol)

    results = await asyncio.gather(*(bound(s) for s in symbols))
    return dict(results)


async def _get_or_refresh(symbol: str) -> _CacheEntry | None:
    db_ok = pool.is_configured()

    if db_ok:
        try:
            cached = await repo.get_fresh(symbol, max_age=_REFRESH_TTL)
        except StorageUnavailable:
            cached = None
        if cached is not None:
            bars, quote = cached
            return _CacheEntry(
                bars=bars,
                price=float(quote["price"]) if quote and quote["price"] is not None else None,
                previous_close=(
                    float(quote["previous_close"])
                    if quote and quote["previous_close"] is not None
                    else None
                ),
                as_of=quote["as_of"] if quote else datetime.now(UTC),
            )

    try:
        chart = await provider.fetch_chart(symbol)
    except provider.SymbolNotFound:
        await _record_best_effort(db_ok, symbol, "not_found", None)
        return None
    except provider.RateLimited:
        await _record_best_effort(db_ok, symbol, "rate_limited", None)
        logger.info("market data rate limited for %s", symbol)
        return None
    except provider.MarketDataError as exc:
        await _record_best_effort(db_ok, symbol, "error", str(exc)[:500])
        logger.info("market data fetch failed for %s: %s", symbol, exc)
        return None

    as_of = chart.regular_market_time or datetime.now(UTC)

    if db_ok:
        try:
            await repo.upsert_chart(
                symbol=symbol,
                name=chart.name,
                currency=chart.currency,
                exchange=chart.exchange,
                bars=chart.bars,
                source="yahoo_chart",
            )
            if chart.regular_market_price is not None:
                await repo.upsert_quote(
                    symbol=symbol,
                    price=chart.regular_market_price,
                    previous_close=chart.previous_close,
                    as_of=as_of,
                    source="yahoo_chart",
                )
            await repo.record_fetch(symbol, "bars", "ok", None)
        except StorageUnavailable:
            pass  # The fetch itself still succeeded; only the cache write was lost.

    return _CacheEntry(
        bars=chart.bars,
        price=chart.regular_market_price,
        previous_close=chart.previous_close,
        as_of=as_of,
    )


async def _record_best_effort(db_ok: bool, symbol: str, status: str, detail: str | None) -> None:
    if not db_ok:
        return
    try:
        await repo.record_fetch(symbol, "bars", status, detail)
    except StorageUnavailable:
        pass
