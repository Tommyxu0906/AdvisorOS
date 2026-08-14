"""save / load for a user's financial profile and portfolio, against a real Postgres.

Skipped when DATABASE_URL is unset, same as test_run_persistence.py — see
scripts/validate_migrations.sh for standing up a scratch database locally.

Two things these tests exist to catch: that saving twice upserts rather than accumulating
profiles (the partial unique index on `is_default` is what makes that work, and an ON CONFLICT
clause that stops matching it would fail loudly here), and that `scrub_for_storage()` is on the
write path for `notes` — the one free-text field a user could paste an API key into.
"""

from __future__ import annotations

import os
import uuid

import pytest

from app.db import pool
from app.db.repositories import profiles as profiles_repo
from app.domain.portfolio import AssetClass, Holding, Portfolio
from app.domain.profile import (
    AccountType,
    Asset,
    Debt,
    Expenses,
    FinancialProfile,
    Goal,
    GoalType,
    Income,
    RiskTolerance,
)

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="no DATABASE_URL — see scripts/validate_migrations.sh to stand up a scratch database",
)


@pytest.fixture(autouse=True)
async def _clean_pool():
    yield
    await pool.close_pool()


@pytest.fixture
async def owner_id() -> str:
    new_id = str(uuid.uuid4())
    await pool.execute(
        "insert into auth.users (id, email) values ($1, $2)", new_id, f"{new_id}@advisoros.dev"
    )
    return new_id


def _profile(**overrides) -> FinancialProfile:
    base = dict(
        age=34,
        dependents=1,
        income=Income(annual_gross=145000, employer_match_pct=0.04),
        expenses=Expenses(monthly_essential=4200, monthly_discretionary=1500),
        debts=[Debt(name="credit card", balance=9000, apr=0.229, minimum_monthly_payment=280)],
        assets=[Asset(name="savings", value=11000, account_type=AccountType.cash, is_liquid=True)],
        goals=[
            Goal(
                name="house down payment",
                goal_type=GoalType.home_purchase,
                years_until_needed=2,
                priority=1,
            )
        ],
        risk_tolerance=RiskTolerance.moderate_aggressive,
        self_reported_experience=0.35,
    )
    base.update(overrides)
    return FinancialProfile(**base)


async def test_profile_round_trips_with_children_in_order(owner_id):
    profile = _profile(
        debts=[
            Debt(name="card", balance=9000, apr=0.229, minimum_monthly_payment=280),
            Debt(name="student loan", balance=22000, apr=0.055, minimum_monthly_payment=310),
        ]
    )
    portfolio = Portfolio(
        holdings=[
            Holding(
                symbol="NVDA",
                asset_class=AssetClass.us_equity,
                quantity=266.5,
                market_value=60000,
            ),
            # No quantity: a holding the price feed cannot recompute must survive untouched.
            Holding(symbol="VTI", asset_class=AssetClass.us_equity, market_value=28000),
        ]
    )

    await profiles_repo.save(owner_id, profile, portfolio)
    loaded = await profiles_repo.load(owner_id)

    assert loaded is not None
    got_profile, got_portfolio = loaded
    assert got_profile.age == profile.age
    assert got_profile.risk_tolerance is RiskTolerance.moderate_aggressive
    assert got_profile.income.annual_gross == 145000
    # `position` is what preserves the order the user arranged the rows in.
    assert [d.name for d in got_profile.debts] == ["card", "student loan"]
    assert [a.name for a in got_profile.assets] == ["savings"]
    assert got_profile.goals[0].goal_type is GoalType.home_purchase

    assert [h.symbol for h in got_portfolio.holdings] == ["NVDA", "VTI"]
    assert got_portfolio.holdings[0].quantity == 266.5
    assert got_portfolio.holdings[1].quantity is None


async def test_saving_twice_updates_rather_than_accumulating(owner_id):
    await profiles_repo.save(owner_id, _profile(), None)
    await profiles_repo.save(owner_id, _profile(age=35, debts=[]), None)

    profile_count = await pool.fetchval(
        "select count(*) from public.financial_profiles where user_id = $1", owner_id
    )
    assert profile_count == 1

    loaded = await profiles_repo.load(owner_id)
    assert loaded is not None
    assert loaded[0].age == 35
    # Child rows are replaced wholesale, so a removed debt is actually gone.
    assert loaded[0].debts == []


async def test_notes_are_scrubbed_before_they_reach_the_database(owner_id):
    planted_key = "sk-ant-api03-" + "P" * 60
    await profiles_repo.save(
        owner_id, _profile(notes=f"remember my key {planted_key}"), None
    )

    stored = await pool.fetchval(
        "select notes from public.financial_profiles where user_id = $1", owner_id
    )
    assert planted_key not in stored
    assert "[REDACTED]" in stored
    # Targeted, not a wholesale wipe of the field.
    assert "remember my key" in stored


async def test_one_users_profile_is_invisible_to_another(owner_id):
    await profiles_repo.save(owner_id, _profile(), None)

    stranger = str(uuid.uuid4())
    await pool.execute(
        "insert into auth.users (id, email) values ($1, $2)", stranger, f"{stranger}@advisoros.dev"
    )
    assert await profiles_repo.load(stranger) is None


async def test_held_symbols_is_the_union_across_users(owner_id):
    await profiles_repo.save(
        owner_id,
        _profile(),
        Portfolio(holdings=[Holding(symbol="ZZZA", market_value=1000)]),
    )

    other = str(uuid.uuid4())
    await pool.execute(
        "insert into auth.users (id, email) values ($1, $2)", other, f"{other}@advisoros.dev"
    )
    await profiles_repo.save(
        other,
        _profile(),
        Portfolio(holdings=[Holding(symbol="ZZZB", market_value=500)]),
    )

    # A superset check, not equality: this query is global by design — it is the price fetcher's
    # work queue across every user — so whatever else the suite has stored is legitimately in it.
    symbols = await profiles_repo.held_symbols()
    assert {"ZZZA", "ZZZB"} <= set(symbols)
    assert symbols == sorted(symbols)
