"""Persistence for a user's financial profile and portfolio.

Unlike `runs.py`, which stores immutable audit records as JSONB, everything here is normalized —
see the reasoning in supabase/migrations/0003_financial_profiles.sql. These rows are edited field
by field in the UI and queried across users (the price fetcher's work queue is "every distinct
symbol anyone holds"), which is what a relational schema is for.

Nothing derived is stored. Net worth, life stage, the need vector and every ratio are recomputed
by analytics/profile_analytics.py on read; persisting them would create a second source of truth
that drifts silently the first time a formula changes.

Each user gets exactly one default profile and one portfolio here. The schema supports several
per user for scenario comparison, but nothing in the product asks for that yet, so `save` upserts
the row flagged `is_default` rather than inventing a picker no UI exposes.

Every write runs inside one transaction that deletes and re-inserts the child rows. A profile is
a small, whole document from the user's point of view — they edit five debts and press nothing —
so reconciling row-by-row would buy nothing and add a class of partial-update bugs.
"""

from __future__ import annotations

from typing import Any

from app.core.credentials import scrub_for_storage
from app.db import pool
from app.domain.portfolio import Holding, Portfolio
from app.domain.profile import FinancialProfile

DEFAULT_PORTFOLIO_NAME = "Main"


async def load(user_id: str) -> tuple[FinancialProfile, Portfolio] | None:
    """The user's saved profile and portfolio, or None if they have never saved one."""
    row = await pool.fetchrow(
        """
        select id, age, currency,
               horizon_years, investable_cash,
               risk_tolerance, self_reported_experience, notes
        from public.financial_profiles
        where user_id = $1 and is_default
        """,
        user_id,
    )
    if row is None:
        return None

    holding_rows = await pool.fetch(
        """
        select h.symbol, h.name, h.asset_class, h.quantity, h.market_value,
               h.cost_basis, h.account_type, h.expense_ratio
        from public.portfolio_holdings h
        join public.portfolios p on p.id = h.portfolio_id
        where p.user_id = $1
        order by h.position
        """,
        user_id,
    )

    profile = FinancialProfile(
        age=row["age"],
        currency=row["currency"],
        horizon_years=float(row["horizon_years"]),
        investable_cash=float(row["investable_cash"]),
        risk_tolerance=row["risk_tolerance"],
        self_reported_experience=row["self_reported_experience"],
        notes=row["notes"],
    )

    portfolio = Portfolio(
        holdings=[
            Holding(
                symbol=h["symbol"],
                name=h["name"],
                asset_class=h["asset_class"],
                quantity=h["quantity"],
                market_value=float(h["market_value"]),
                cost_basis=float(h["cost_basis"]) if h["cost_basis"] is not None else None,
                account_type=h["account_type"],
                expense_ratio=h["expense_ratio"],
            )
            for h in holding_rows
        ],
        currency=row["currency"],
    )
    return profile, portfolio


async def save(user_id: str, profile: FinancialProfile, portfolio: Portfolio | None) -> None:
    """Replace the user's saved profile and portfolio with these.

    `notes` is the one free-text field a user could paste an API key into, so it goes through
    the same `scrub_for_storage()` gate every run payload does — see core/credentials.py.
    """
    notes = scrub_for_storage({"notes": profile.notes})["notes"]

    async with pool.acquire() as conn, conn.transaction():
        await conn.fetchval(
            """
            insert into public.financial_profiles (
                user_id, name, is_default, age, currency,
                horizon_years, investable_cash,
                risk_tolerance, self_reported_experience, notes
            ) values ($1, 'Main', true, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13)
            -- The partial unique index on (user_id) where is_default is what makes this an
            -- upsert rather than a second profile: see migration 0003.
            on conflict (user_id) where is_default do update set
                age = excluded.age,
                                currency = excluded.currency,
                horizon_years = excluded.horizon_years,
                investable_cash = excluded.investable_cash,
                risk_tolerance = excluded.risk_tolerance,
                self_reported_experience = excluded.self_reported_experience,
                notes = excluded.notes
            returning id
            """,
            user_id,
            profile.age,
            profile.currency,
            profile.horizon_years,
            profile.investable_cash,
            profile.risk_tolerance.value,
            profile.self_reported_experience,
            notes,
        )


async def held_symbols() -> list[str]:
    """Every distinct symbol anyone holds — the price fetcher's work queue."""
    rows: list[Any] = await pool.fetch(
        "select distinct symbol from public.portfolio_holdings order by symbol"
    )
    return [r["symbol"] for r in rows]
