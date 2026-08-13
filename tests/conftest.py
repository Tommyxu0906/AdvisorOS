"""Shared fixtures.

Note the `no_developer_key` autouse fixture: every test runs with `ANTHROPIC_API_KEY` removed
from the environment. If any production code path secretly depends on it, the suite fails.
"""

from __future__ import annotations

import pytest
from pydantic import SecretStr

from app.advisors.registry import AdvisorRegistry
from app.analytics.guardrails import evaluate_guardrails
from app.analytics.portfolio_analytics import analyze_portfolio
from app.analytics.profile_analytics import analyze_profile
from app.core.credentials import UserLLMCredentials
from app.core.run_context import RunContext
from app.domain.portfolio import AssetClass, Holding, Portfolio, PriceSeries
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
from app.domain.report import AnalysisDepth
from app.llm.mock_provider import MockLLMProvider

FAKE_KEY = "sk-ant-api03-" + "T" * 60


@pytest.fixture(autouse=True)
def no_developer_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """No project-owned credentials exist during any test."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("AIFA_ALLOW_DEV_KEY", raising=False)


@pytest.fixture
def credentials() -> UserLLMCredentials:
    return UserLLMCredentials(anthropic_api_key=SecretStr(FAKE_KEY))


@pytest.fixture
def registry() -> AdvisorRegistry:
    return AdvisorRegistry()


@pytest.fixture
def mock_provider() -> MockLLMProvider:
    return MockLLMProvider()


@pytest.fixture
def context(credentials: UserLLMCredentials) -> RunContext:
    return RunContext.create(credentials, depth=AnalysisDepth.balanced)


@pytest.fixture
def stressed_profile() -> FinancialProfile:
    """Thin buffer, high-APR debt, near-term goal, concentrated equity."""
    return FinancialProfile(
        age=34,
        dependents=1,
        income=Income(annual_gross=145_000, employer_match_pct=0.04, stability=0.7),
        expenses=Expenses(monthly_essential=4_200, monthly_discretionary=1_500),
        debts=[Debt(name="credit card", balance=9_000, apr=0.229, minimum_monthly_payment=280)],
        assets=[
            Asset(name="savings", value=11_000, account_type=AccountType.cash),
            Asset(
                name="401k",
                value=88_000,
                account_type=AccountType.traditional_401k,
                is_liquid=False,
            ),
        ],
        goals=[Goal(name="house down payment", years_until_needed=2.0, target_amount=80_000)],
        risk_tolerance=RiskTolerance.moderate_aggressive,
        self_reported_experience=0.35,
    )


@pytest.fixture
def healthy_profile() -> FinancialProfile:
    """Well-funded pre-retiree with no debt."""
    return FinancialProfile(
        age=61,
        income=Income(annual_gross=180_000, annual_net=128_000, stability=0.95),
        expenses=Expenses(monthly_essential=5_000, monthly_discretionary=2_000),
        assets=[
            Asset(name="cash", value=90_000, account_type=AccountType.cash),
            Asset(name="ira", value=1_400_000, account_type=AccountType.traditional_ira),
            Asset(name="brokerage", value=600_000, account_type=AccountType.taxable),
        ],
        goals=[
            Goal(name="retirement", goal_type=GoalType.retirement, years_until_needed=4, priority=1)
        ],
        risk_tolerance=RiskTolerance.moderate_conservative,
        self_reported_experience=0.8,
    )


@pytest.fixture
def concentrated_portfolio() -> Portfolio:
    return Portfolio(
        holdings=[
            Holding(
                symbol="NVDA",
                asset_class=AssetClass.us_equity,
                market_value=60_000,
                cost_basis=12_000,
            ),
            Holding(
                symbol="VTI",
                asset_class=AssetClass.us_equity,
                market_value=28_000,
                expense_ratio=0.0003,
            ),
        ],
        price_series=[
            PriceSeries(symbol="NVDA", returns=[0.10, -0.15, 0.22, 0.05, -0.08, 0.30, 0.02, -0.12]),
            PriceSeries(symbol="VTI", returns=[0.02, -0.03, 0.04, 0.01, -0.02, 0.05, 0.01, -0.01]),
        ],
    )


@pytest.fixture
def analyzed(stressed_profile: FinancialProfile, concentrated_portfolio: Portfolio):
    """(analytics, portfolio_analytics, guardrails) for the stressed fixture."""
    analytics = analyze_profile(stressed_profile, concentrated_portfolio)
    pa = analyze_portfolio(concentrated_portfolio)
    rails = evaluate_guardrails(stressed_profile, analytics, concentrated_portfolio, pa)
    return analytics, pa, rails
