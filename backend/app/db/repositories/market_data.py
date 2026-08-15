"""Postgres-backed cache for market data — see supabase/migrations/0007_market_data.sql.

Shared reference data: one row per symbol regardless of how many users hold it, so the cache is
what keeps a free, unauthenticated, rate-limit-prone provider viable at more than one concurrent
user. Every function here raises `StorageUnavailable` like any other repository rather than
swallowing it — market_data/service.py decides per call site whether a caching failure is worth
logging, since the fetch itself may have already succeeded.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.db import pool
from app.market_data.provider import Bar


async def get_fresh(symbol: str, *, max_age: timedelta) -> tuple[list[Bar], dict | None] | None:
    """Cached bars + latest quote for `symbol`, or None if nothing is cache-fresh enough.

    Freshness is judged from `instruments.last_refreshed_at` alone: bars and the quote are
    always written together by `upsert_chart`/`upsert_quote`, so one timestamp speaks for both.
    """
    row = await pool.fetchrow(
        "select last_refreshed_at from public.instruments where symbol = $1", symbol
    )
    if row is None or row["last_refreshed_at"] is None:
        return None
    if datetime.now(UTC) - row["last_refreshed_at"] > max_age:
        return None

    bar_rows = await pool.fetch(
        """
        select trade_date, open, high, low, close, adj_close, volume
        from public.daily_bars
        where symbol = $1
        order by trade_date asc
        """,
        symbol,
    )
    if not bar_rows:
        return None

    bars = [
        Bar(
            trade_date=r["trade_date"],
            open=float(r["open"]) if r["open"] is not None else None,
            high=float(r["high"]) if r["high"] is not None else None,
            low=float(r["low"]) if r["low"] is not None else None,
            close=float(r["close"]),
            adj_close=float(r["adj_close"]),
            volume=r["volume"],
        )
        for r in bar_rows
    ]

    quote_row = await pool.fetchrow(
        """
        select price, previous_close, change_pct, as_of, is_delayed, source
        from public.latest_quotes
        where symbol = $1
        """,
        symbol,
    )
    return bars, (dict(quote_row) if quote_row else None)


async def upsert_chart(
    *, symbol: str, name: str, currency: str, exchange: str, bars: list[Bar], source: str
) -> None:
    """Replace the cached instrument + bar history for `symbol` wholesale.

    A full replace (rather than an upsert-per-date) means a bad row from a prior fetch can never
    linger once a fresh fetch succeeds, and there is only one write pattern to reason about.
    """
    now = datetime.now(UTC)
    last_bar_date = max((b.trade_date for b in bars), default=None)

    async with pool.acquire() as conn, conn.transaction():
        await conn.execute(
            """
            insert into public.instruments
                (symbol, name, exchange, currency, last_bar_date, last_refreshed_at)
            values ($1, $2, $3, $4, $5, $6)
            on conflict (symbol) do update set
                name = excluded.name,
                exchange = excluded.exchange,
                currency = excluded.currency,
                last_bar_date = excluded.last_bar_date,
                last_refreshed_at = excluded.last_refreshed_at
            """,
            symbol,
            name,
            exchange,
            currency,
            last_bar_date,
            now,
        )
        await conn.execute("delete from public.daily_bars where symbol = $1", symbol)
        if bars:
            await conn.executemany(
                """
                insert into public.daily_bars
                    (symbol, trade_date, open, high, low, close, adj_close, volume, source)
                values ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                """,
                [
                    (
                        symbol,
                        b.trade_date,
                        b.open,
                        b.high,
                        b.low,
                        b.close,
                        b.adj_close,
                        b.volume,
                        source,
                    )
                    for b in bars
                ],
            )


async def upsert_quote(
    *, symbol: str, price: float, previous_close: float | None, as_of: datetime, source: str
) -> None:
    change_pct = (
        (price - previous_close) / previous_close if previous_close and previous_close > 0 else None
    )
    await pool.execute(
        """
        insert into public.latest_quotes
            (symbol, price, previous_close, change_pct, as_of, is_delayed, source, fetched_at)
        values ($1, $2, $3, $4, $5, true, $6, now())
        on conflict (symbol) do update set
            price = excluded.price,
            previous_close = excluded.previous_close,
            change_pct = excluded.change_pct,
            as_of = excluded.as_of,
            source = excluded.source,
            fetched_at = excluded.fetched_at
        """,
        symbol,
        price,
        previous_close,
        change_pct,
        as_of,
        source,
    )


async def record_fetch(symbol: str, kind: str, status: str, detail: str | None) -> None:
    """Every fetch attempt, including failures — see the table comment in the migration for why:
    without this a provider outage or a rate-limit wall looks identical to "the market didn't
    move", with no visible cause.
    """
    await pool.execute(
        "insert into public.market_data_fetches (symbol, kind, status, detail) values ($1, $2, $3, $4)",
        symbol,
        kind,
        status,
        detail,
    )
