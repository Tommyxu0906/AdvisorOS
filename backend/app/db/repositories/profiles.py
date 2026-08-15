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
from app.domain.profile import (
    Asset,
    Debt,
    Expenses,
    FinancialProfile,
    Goal,
    Income,
)

DEFAULT_PORTFOLIO_NAME = "Main"


async def load(user_id: str) -> tuple[FinancialProfile, Portfolio] | None:
    """The user's saved profile and portfolio, or None if they have never saved one."""
    row = await pool.fetchrow(
        """
        select id, age, dependents, currency,
               income_annual_gross, income_annual_net, income_stability, employer_match_pct,
               expenses_monthly_essential, expenses_monthly_discretionary,
               risk_tolerance, self_reported_experience, notes
        from public.financial_profiles
        where user_id = $1 and is_default
        """,
        user_id,
    )
    if row is None:
        return None

    profile_id = row["id"]
    debt_rows, asset_rows, goal_rows, holding_rows = (
        await pool.fetch(
            """
            select name, balance, apr, minimum_monthly_payment, is_secured
            from public.profile_debts where profile_id = $1 order by position
            """,
            profile_id,
        ),
        await pool.fetch(
            """
            select name, value, account_type, is_liquid
            from public.profile_assets where profile_id = $1 order by position
            """,
            profile_id,
        ),
        await pool.fetch(
            """
            select name, goal_type, target_amount, years_until_needed, priority
            from public.profile_goals where profile_id = $1 order by position
            """,
            profile_id,
        ),
        await pool.fetch(
            """
            select h.symbol, h.name, h.asset_class, h.quantity, h.market_value,
                   h.cost_basis, h.account_type, h.expense_ratio
            from public.portfolio_holdings h
            join public.portfolios p on p.id = h.portfolio_id
            where p.user_id = $1
            order by h.position
            """,
            user_id,
        ),
    )

    profile = FinancialProfile(
        age=row["age"],
        dependents=row["dependents"],
        currency=row["currency"],
        income=Income(
            annual_gross=float(row["income_annual_gross"]),
            annual_net=(
                float(row["income_annual_net"]) if row["income_annual_net"] is not None else None
            ),
            stability=row["income_stability"],
            employer_match_pct=row["employer_match_pct"],
        ),
        expenses=Expenses(
            monthly_essential=float(row["expenses_monthly_essential"]),
            monthly_discretionary=float(row["expenses_monthly_discretionary"]),
        ),
        debts=[
            Debt(
                name=d["name"],
                balance=float(d["balance"]),
                apr=d["apr"],
                minimum_monthly_payment=float(d["minimum_monthly_payment"]),
                is_secured=d["is_secured"],
            )
            for d in debt_rows
        ],
        assets=[
            Asset(
                name=a["name"],
                value=float(a["value"]),
                account_type=a["account_type"],
                is_liquid=a["is_liquid"],
            )
            for a in asset_rows
        ],
        goals=[
            Goal(
                name=g["name"],
                goal_type=g["goal_type"],
                target_amount=(
                    float(g["target_amount"]) if g["target_amount"] is not None else None
                ),
                years_until_needed=g["years_until_needed"],
                priority=g["priority"],
            )
            for g in goal_rows
        ],
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
        profile_id = await conn.fetchval(
            """
            insert into public.financial_profiles (
                user_id, name, is_default, age, dependents, currency,
                income_annual_gross, income_annual_net, income_stability, employer_match_pct,
                expenses_monthly_essential, expenses_monthly_discretionary,
                risk_tolerance, self_reported_experience, notes
            ) values ($1, 'Main', true, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13)
            -- The partial unique index on (user_id) where is_default is what makes this an
            -- upsert rather than a second profile: see migration 0003.
            on conflict (user_id) where is_default do update set
                age = excluded.age,
                dependents = excluded.dependents,
                currency = excluded.currency,
                income_annual_gross = excluded.income_annual_gross,
                income_annual_net = excluded.income_annual_net,
                income_stability = excluded.income_stability,
                employer_match_pct = excluded.employer_match_pct,
                expenses_monthly_essential = excluded.expenses_monthly_essential,
                expenses_monthly_discretionary = excluded.expenses_monthly_discretionary,
                risk_tolerance = excluded.risk_tolerance,
                self_reported_experience = excluded.self_reported_experience,
                notes = excluded.notes
            returning id
            """,
            user_id,
            profile.age,
            profile.dependents,
            profile.currency,
            profile.income.annual_gross,
            profile.income.annual_net,
            profile.income.stability,
            profile.income.employer_match_pct,
            profile.expenses.monthly_essential,
            profile.expenses.monthly_discretionary,
            profile.risk_tolerance.value,
            profile.self_reported_experience,
            notes,
        )

        await conn.execute("delete from public.profile_debts where profile_id = $1", profile_id)
        await conn.execute("delete from public.profile_assets where profile_id = $1", profile_id)
        await conn.execute("delete from public.profile_goals where profile_id = $1", profile_id)

        if profile.debts:
            await conn.executemany(
                """
                insert into public.profile_debts
                    (profile_id, position, name, balance, apr, minimum_monthly_payment, is_secured)
                values ($1, $2, $3, $4, $5, $6, $7)
                """,
                [
                    (
                        profile_id,
                        i,
                        d.name,
                        d.balance,
                        d.apr,
                        d.minimum_monthly_payment,
                        d.is_secured,
                    )
                    for i, d in enumerate(profile.debts)
                ],
            )
        if profile.assets:
            await conn.executemany(
                """
                insert into public.profile_assets
                    (profile_id, position, name, value, account_type, is_liquid)
                values ($1, $2, $3, $4, $5, $6)
                """,
                [
                    (profile_id, i, a.name, a.value, a.account_type.value, a.is_liquid)
                    for i, a in enumerate(profile.assets)
                ],
            )
        if profile.goals:
            await conn.executemany(
                """
                insert into public.profile_goals
                    (profile_id, position, name, goal_type, target_amount,
                     years_until_needed, priority)
                values ($1, $2, $3, $4, $5, $6, $7)
                """,
                [
                    (
                        profile_id,
                        i,
                        g.name,
                        g.goal_type.value,
                        g.target_amount,
                        g.years_until_needed,
                        g.priority,
                    )
                    for i, g in enumerate(profile.goals)
                ],
            )

        portfolio_id = await conn.fetchval(
            "select id from public.portfolios where user_id = $1 and name = $2",
            user_id,
            DEFAULT_PORTFOLIO_NAME,
        )
        if portfolio_id is None:
            portfolio_id = await conn.fetchval(
                """
                insert into public.portfolios (user_id, profile_id, name, currency)
                values ($1, $2, $3, $4) returning id
                """,
                user_id,
                profile_id,
                DEFAULT_PORTFOLIO_NAME,
                portfolio.currency if portfolio else "USD",
            )
        else:
            await conn.execute(
                "update public.portfolios set profile_id = $2 where id = $1",
                portfolio_id,
                profile_id,
            )

        await conn.execute(
            "delete from public.portfolio_holdings where portfolio_id = $1", portfolio_id
        )
        if portfolio and portfolio.holdings:
            await conn.executemany(
                """
                insert into public.portfolio_holdings
                    (portfolio_id, position, symbol, name, asset_class, quantity,
                     market_value, cost_basis, account_type, expense_ratio)
                values ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                """,
                [
                    (
                        portfolio_id,
                        i,
                        h.symbol,
                        h.name,
                        h.asset_class.value,
                        h.quantity,
                        h.market_value,
                        h.cost_basis,
                        h.account_type.value,
                        h.expense_ratio,
                    )
                    for i, h in enumerate(portfolio.holdings)
                ],
            )


async def held_symbols() -> list[str]:
    """Every distinct symbol anyone holds — the price fetcher's work queue."""
    rows: list[Any] = await pool.fetch(
        "select distinct symbol from public.portfolio_holdings order by symbol"
    )
    return [r["symbol"] for r in rows]
