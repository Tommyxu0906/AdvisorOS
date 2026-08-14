"""Free, unauthenticated market data — Yahoo Finance's public chart endpoint.

No API key, no signup, no operator cost. In exchange: it is an unofficial endpoint (the same
one every "yfinance"-style library reads), can change shape or start blocking without notice,
and its quotes are exchange-delayed rather than real-time. See
supabase/migrations/0007_market_data.sql for why daily bars are enough for what this product
actually computes from price data (volatility, drawdown, correlation — not a live ticker).

Every function here raises one of the errors below rather than letting httpx exceptions leak, so
callers (market_data/service.py) can classify a failure into the `market_data_fetches.status`
enum ('rate_limited' | 'error' | 'not_found') without knowing anything about HTTP.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime

import httpx

_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"

# Yahoo's chart endpoint rejects requests with no browser-like User-Agent.
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}

_TIMEOUT = httpx.Timeout(10.0, connect=5.0)


class MarketDataError(RuntimeError):
    """Base for every provider failure. Never carries a stack trace to a caller."""


class SymbolNotFound(MarketDataError):
    pass


class RateLimited(MarketDataError):
    pass


@dataclass(frozen=True)
class Bar:
    trade_date: date
    open: float | None
    high: float | None
    low: float | None
    close: float
    # Split/dividend adjusted. Returns must be computed from this, never from `close`.
    adj_close: float
    volume: int | None


@dataclass(frozen=True)
class ChartData:
    symbol: str
    name: str
    currency: str
    exchange: str
    bars: list[Bar]
    # From the same response's `meta`, so one request serves both the bar history and a quote.
    regular_market_price: float | None
    previous_close: float | None
    regular_market_time: datetime | None


async def fetch_chart(symbol: str, *, range_: str = "1y", interval: str = "1d") -> ChartData:
    """Daily bars plus the latest known price for `symbol`, in a single request.

    Raises SymbolNotFound, RateLimited, or MarketDataError. A response Yahoo returned but that
    could not be parsed into at least one bar is treated as SymbolNotFound rather than silently
    returned as an empty series.
    """
    url = _CHART_URL.format(symbol=symbol)
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT, headers=_HEADERS) as client:
            resp = await client.get(url, params={"range": range_, "interval": interval})
    except httpx.TimeoutException as exc:
        raise MarketDataError(f"timed out fetching {symbol}") from exc
    except httpx.HTTPError as exc:
        raise MarketDataError(f"network error fetching {symbol}: {exc}") from exc

    if resp.status_code == 404:
        raise SymbolNotFound(symbol)
    if resp.status_code == 429:
        raise RateLimited(symbol)
    if resp.status_code != 200:
        raise MarketDataError(f"{symbol}: unexpected status {resp.status_code}")

    try:
        payload = resp.json()
    except ValueError as exc:
        raise MarketDataError(f"{symbol}: response was not JSON") from exc

    chart = payload.get("chart") or {}
    if chart.get("error"):
        err = chart["error"] or {}
        if err.get("code") in ("Not Found", "No Data Found"):
            raise SymbolNotFound(symbol)
        raise MarketDataError(f"{symbol}: {err}")

    results = chart.get("result") or []
    if not results:
        raise SymbolNotFound(symbol)

    return _parse_result(symbol, results[0])


def _parse_result(symbol: str, result: dict) -> ChartData:
    meta = result.get("meta") or {}
    timestamps = result.get("timestamp") or []
    indicators = result.get("indicators") or {}
    quote_list = indicators.get("quote") or [{}]
    quote = quote_list[0] if quote_list else {}
    adjclose_list = indicators.get("adjclose") or [{}]
    # Falls back to raw close when Yahoo omits the adjusted series (seen occasionally for some
    # ETFs/indices) — unadjusted returns are still usable, just wrong across a split/dividend.
    adjcloses = (adjclose_list[0] if adjclose_list else {}).get("adjclose") or quote.get("close")

    opens = quote.get("open") or [None] * len(timestamps)
    highs = quote.get("high") or [None] * len(timestamps)
    lows = quote.get("low") or [None] * len(timestamps)
    closes = quote.get("close") or [None] * len(timestamps)
    volumes = quote.get("volume") or [None] * len(timestamps)
    adjcloses = adjcloses or [None] * len(timestamps)

    bars: list[Bar] = []
    for i, ts in enumerate(timestamps):
        close = closes[i] if i < len(closes) else None
        adj = adjcloses[i] if i < len(adjcloses) else None
        # A trading day with no close is a holiday/gap in Yahoo's own data, not ours to invent.
        if close is None or adj is None or adj <= 0:
            continue
        bars.append(
            Bar(
                trade_date=datetime.fromtimestamp(ts, tz=UTC).date(),
                open=opens[i] if i < len(opens) else None,
                high=highs[i] if i < len(highs) else None,
                low=lows[i] if i < len(lows) else None,
                close=float(close),
                adj_close=float(adj),
                volume=volumes[i] if i < len(volumes) else None,
            )
        )

    if not bars:
        raise SymbolNotFound(symbol)

    regular_time = meta.get("regularMarketTime")
    return ChartData(
        symbol=symbol,
        name=meta.get("shortName") or meta.get("longName") or symbol,
        currency=meta.get("currency") or "USD",
        exchange=meta.get("exchangeName") or "",
        bars=bars,
        regular_market_price=meta.get("regularMarketPrice"),
        # Deliberately from the bars, not from meta: `meta.previousClose` is absent on a
        # multi-day range and `meta.chartPreviousClose` is the close before the *range* starts,
        # so using either turns a day's move into a year's move. The last bar is the current
        # (possibly still-open) session, making the one before it the prior close.
        previous_close=bars[-2].close if len(bars) >= 2 else None,
        regular_market_time=(
            datetime.fromtimestamp(regular_time, tz=UTC) if regular_time else None
        ),
    )
