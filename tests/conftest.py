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
from app.domain.profile import FinancialProfile, RiskTolerance
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
    """Money needed soon, little cash, and an aggressive book. Trips the one blocking rule."""
    return FinancialProfile(
        age=34,
        horizon_years=2.0,
        investable_cash=4_000,
        risk_tolerance=RiskTolerance.moderate_aggressive,
        self_reported_experience=0.35,
    )


@pytest.fixture
def healthy_profile() -> FinancialProfile:
    """Long horizon, plenty of deployable cash, experienced. Trips nothing blocking."""
    return FinancialProfile(
        age=61,
        horizon_years=20.0,
        investable_cash=90_000,
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


@pytest.fixture
def scenario_with_actions():
    """A concentrated book with card debt — the situation the whole demo is built around."""
    from app.analytics.guardrails import evaluate_guardrails
    from app.analytics.portfolio_analytics import analyze_portfolio
    from app.analytics.profile_analytics import analyze_profile
    from app.domain.portfolio import Holding, Portfolio
    from app.domain.profile import FinancialProfile
    from app.policy.engine import compute_scenario

    profile = FinancialProfile(
        age=38,
    )
    portfolio = Portfolio(
        holdings=[
            Holding(
                symbol=sym,
                name=sym,
                asset_class="us_equity",
                quantity=qty,
                market_value=value,
                cost_basis=basis,
                account_type="taxable",
            )
            for sym, qty, value, basis in [
                ("NVDA", 300, 96_000, 31_000),
                ("VTI", 200, 58_000, 44_000),
                ("BND", 400, 29_000, 30_500),
            ]
        ]
    )
    analytics = analyze_profile(profile)
    pa = analyze_portfolio(portfolio)
    return compute_scenario(
        profile, analytics, portfolio, pa, evaluate_guardrails(profile, analytics)
    )
