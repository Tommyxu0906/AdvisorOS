"""Connected brokerage data becoming something the existing engine can analyze.

The mock connector's household is deliberately awkward, and each awkwardness maps to a test
here: the same symbol in three accounts, a position with no cost basis, a plan fund with no
ticker or price, and a broken connection still serving three-week-old holdings.

`test_the_same_symbol_in_three_accounts_aggregates_once` is the load-bearing one. Aggregating by
symbol across accounts is required for household concentration and wrong for tax, so both views
have to survive the trip.
"""

from __future__ import annotations

from datetime import UTC

import pytest

from app.analytics.portfolio_analytics import analyze_portfolio
from app.connectors.mock import MockPortfolioConnector
from app.connectors.normalize import CASH_SYMBOL, to_portfolio
from app.domain.connection import ConnectedPortfolio, ConnectionStatus
from app.domain.profile import AccountType

USER = "6f2a1b3c-0000-4000-8000-000000000001"


async def _connected(**kw) -> ConnectedPortfolio:
    connector = MockPortfolioConnector(**kw)
    accounts = await connector.list_accounts(USER)
    positions = [p for a in accounts for p in await connector.get_positions(USER, a.account_id)]
    transactions = [
        t for a in accounts for t in await connector.get_transactions(USER, a.account_id)
    ]
    return ConnectedPortfolio(accounts=accounts, positions=positions, transactions=transactions)


# --- household view vs account view ---------------------------------------------------------


async def test_the_same_symbol_in_three_accounts_aggregates_once():
    """VTI sits in a taxable account, a Roth, and a 401(k). Concentration must see one position."""
    connected = await _connected()
    result = to_portfolio(connected)
    analytics = analyze_portfolio(result.portfolio)

    vti_rows = [h for h in result.portfolio.holdings if h.symbol == "VTI"]
    assert len(vti_rows) == 3  # the projection keeps one row per account

    # ...and the analytics collapse them into a single household weight.
    assert sum(h.market_value for h in vti_rows) == pytest.approx(86_800.0)
    assert analytics.weights["VTI"] == pytest.approx(86_800.0 / analytics.total_value)


async def test_account_provenance_survives_aggregation():
    """Knowing VTI is 39% is useless for tax without knowing where the 39% lives."""
    connected = await _connected()
    result = to_portfolio(connected)

    assert result.symbol_accounts["VTI"] == ["fid_roth", "fid_taxable", "schwab_401k"]
    assert connected.accounts_holding("VTI") == ["fid_roth", "fid_taxable", "schwab_401k"]
    # The connected model stays the source of record for account-level questions.
    assert [p.symbol for p in connected.positions_for("fid_roth")] == ["VTI", "BND"]


async def test_tax_treatment_is_taken_from_the_account_not_guessed():
    connected = await _connected()
    result = to_portfolio(connected)

    by_account = {(h.symbol, h.account_type) for h in result.portfolio.holdings}
    assert ("VTI", AccountType.taxable) in by_account
    assert ("VTI", AccountType.roth_ira) in by_account
    assert ("VTI", AccountType.traditional_401k) in by_account


async def test_uninvested_cash_is_counted_in_the_denominator():
    """Dropping account cash shrinks the base every weight is measured against, inflating
    concentration for exactly the people deliberately holding cash — and the sensitivity engine
    measures distance to a threshold in single points, so an inflated weight can manufacture
    fragility that is not there."""
    connected = await _connected()
    result = to_portfolio(connected)
    analytics = analyze_portfolio(result.portfolio)

    assert analytics.total_value == pytest.approx(connected.total_value)
    assert analytics.weights[CASH_SYMBOL] > 0
    assert result.symbol_accounts[CASH_SYMBOL] == ["fid_roth", "fid_taxable"]


# --- what the provider did not tell us --------------------------------------------------------


async def test_a_position_with_no_cost_basis_stays_unknown():
    """The RSU case. A confident $0 basis would invent a 100% gain and tax to match."""
    connected = await _connected()
    msft = next(p for p in connected.positions if p.symbol == "MSFT")

    assert msft.cost_basis is None
    assert msft.cost_basis_source is None
    assert msft.unrealized_gain is None

    projected = next(h for h in to_portfolio(connected).portfolio.holdings if h.symbol == "MSFT")
    assert projected.cost_basis is None


async def test_missing_tax_lots_do_not_become_fake_precision():
    connected = await _connected()
    nvda = next(p for p in connected.positions if p.symbol == "NVDA")
    vti = next(p for p in connected.positions if p.symbol == "VTI")

    assert nvda.has_usable_tax_lots  # lots reported, with basis
    assert not vti.has_usable_tax_lots  # aggregate basis only
    msft = next(p for p in connected.positions if p.symbol == "MSFT")
    assert not msft.has_usable_tax_lots  # nothing at all


async def test_holding_period_is_unknown_without_an_acquisition_date():
    from datetime import datetime

    from app.domain.connection import TaxLot

    now = datetime(2026, 8, 15, tzinfo=UTC)
    assert TaxLot(quantity=10).is_long_term(now) is None

    connected = await _connected()
    nvda = next(p for p in connected.positions if p.symbol == "NVDA")
    held = [lot.is_long_term(now) for lot in nvda.tax_lots]
    # One lot from 2021, one from this April — this is the case a range can legitimately narrow.
    assert held == [True, False]


async def test_a_position_that_cannot_be_valued_is_dropped_and_reported():
    """Never zero: a zero-valued holding is counted as real money at zero by every weight."""
    connected = await _connected()
    unpriceable = next(p for p in connected.positions if p.symbol == "STABLE-VALUE-FUND")
    # This one has a market value, so it survives; strip it to make the failure case.
    stripped = unpriceable.model_copy(update={"market_value": None})
    assert stripped.effective_value() is None

    broken = ConnectedPortfolio(
        accounts=connected.accounts,
        positions=[p for p in connected.positions if p.symbol != "STABLE-VALUE-FUND"] + [stripped],
    )
    result = to_portfolio(broken)

    assert [u.symbol for u in result.unpriced] == ["STABLE-VALUE-FUND"]
    # A plan fund with no ticker reports neither a share count nor a price, so the reason names
    # all three missing fields rather than blaming the price alone.
    assert result.unpriced[0].reason == "no market value, quantity, or price reported"
    assert all(h.symbol != "STABLE-VALUE-FUND" for h in result.portfolio.holdings)
    assert result.priced_coverage < 1.0
    assert "are not worth zero" in " ".join(result.caveats())


async def test_a_share_count_alone_is_not_a_value():
    connected = await _connected()
    vti = next(p for p in connected.positions if p.symbol == "VTI")
    assert vti.model_copy(update={"market_value": None, "price": None}).effective_value() is None
    # But quantity x price is a legitimate reconstruction.
    assert vti.model_copy(update={"market_value": None}).effective_value() == pytest.approx(30_800)


# --- freshness ---------------------------------------------------------------------------------


async def test_a_broken_connection_is_stale_not_empty():
    """The two opposite failures: showing old data as current, and showing a broken link as $0."""
    connected = await _connected()
    schwab = next(a for a in connected.accounts if a.account_id == "schwab_401k")

    assert schwab.freshness.status is ConnectionStatus.broken
    assert schwab.freshness.is_stale
    assert schwab.total_value == 64_200.0  # the money is still there
    assert connected.is_any_data_stale

    # And the holdings behind it still reach the analytics.
    result = to_portfolio(connected)
    assert result.stale_accounts == ["schwab_401k"]
    assert any(h.symbol == "STABLE-VALUE-FUND" for h in result.portfolio.holdings)


async def test_cached_data_is_never_described_as_current():
    connected = await _connected()
    schwab = next(a for a in connected.accounts if a.account_id == "schwab_401k")
    fidelity = next(a for a in connected.accounts if a.account_id == "fid_taxable")

    stale = schwab.freshness.describe()
    assert "not current data" in stale
    assert "last known state" in stale

    live = fidelity.freshness.describe()
    assert "synced" in live
    assert "not current data" not in live


async def test_a_pending_connection_does_not_read_as_an_empty_portfolio():
    from app.domain.connection import Freshness

    pending = Freshness(provider="mock", status=ConnectionStatus.pending)
    assert "first sync has not completed" in pending.describe()
    assert not pending.is_stale


# --- the engine runs on it unmodified -----------------------------------------------------------


async def test_the_existing_policies_run_on_connected_holdings():
    """The point of the whole normalizer: no policy code knows a brokerage was involved."""
    from app.analytics.guardrails import evaluate_guardrails
    from app.analytics.profile_analytics import analyze_profile
    from app.domain.policy import PolicyProfile
    from app.domain.profile import Expenses, FinancialProfile, Income
    from app.policy import concentration, sensitivity

    connected = await _connected()
    portfolio = to_portfolio(connected).portfolio
    profile = FinancialProfile(
        age=34,
        income=Income(annual_gross=145_000),
        expenses=Expenses(monthly_essential=4_200, monthly_discretionary=1_500),
    )
    analytics = analyze_profile(profile, portfolio)
    pa = analyze_portfolio(portfolio)
    rails = evaluate_guardrails(profile, analytics, portfolio, pa)

    actions = concentration.propose(profile, analytics, portfolio, pa, rails, PolicyProfile())
    assert any(a.symbol == "VTI" for a in actions)  # 39% is over the 20% house threshold

    sweep = sensitivity.sweep_concentration(
        profile, analytics, portfolio, pa, rails, PolicyProfile()
    )
    assert sweep.baseline_acts
    assert sweep.flip_at is not None
    assert sweep.summary_lines()


async def test_a_user_who_has_connected_nothing_gets_an_empty_not_an_error():
    connected = await _connected(connected=False)
    result = to_portfolio(connected)

    assert connected.accounts == []
    assert connected.total_value is None  # unknown, not zero
    assert result.portfolio.holdings == []
    assert result.caveats() == []


async def test_the_mock_connector_needs_no_credentials_or_network():
    """CI must never require a brokerage login, and demo mode must use the same path as live."""
    connector = MockPortfolioConnector()
    session = await connector.create_connection_session(USER, "https://app.example/return")

    assert session.connection_type == "read"
    assert await connector.list_connections(USER)
    await connector.disconnect(USER, "conn_schwab")
    assert connector.disconnected == ["conn_schwab"]


async def test_transactions_are_imported_but_kept_out_of_the_portfolio():
    """Observed behaviour is evidence about what someone did, not a statement of what they want."""
    connected = await _connected()
    assert len(connected.transactions) == 3

    result = to_portfolio(connected)
    # Nothing in the projection is derived from an activity.
    assert len(result.portfolio.holdings) == len(connected.included_positions) + 2
