"""A brokerage that exists only in memory, and the awkward household it reports.

This is not a placeholder to be replaced by the real thing. It is permanent infrastructure with
two production jobs: it is what CI runs against, because no test suite should require a
brokerage login, and it is the engine behind demo mode, because a visitor should be able to see
the product work without connecting real money.

Both jobs demand the same thing — that mock data travels the *same* path as live data. A demo
mode with its own rendering shortcut proves nothing about the pipeline it is demonstrating, and
a fixture that produces tidier data than the real world tests the easy case only.

So the seeded household is deliberately awkward, and every awkwardness is a bug this project has
already hit or a case the normalizer must handle:

  **VTI in three accounts.** Taxable, Roth, and 401(k). Household concentration must sum them;
  account-level tax treatment must not. This is the duplicate-symbol shape that silently
  corrupted `analyze_portfolio` before it was fixed.

  **NVDA with real tax lots**, one long-held and one recent — the case where a tax estimate can
  legitimately narrow instead of spanning both treatments.

  **An employer position with no cost basis at all.** Extremely common for RSUs, and the case
  that must stay `None` rather than becoming a confident $0 basis and a fabricated 100% gain.

  **A 401(k) fund with no ticker and no price**, valued only in dollars. Weight arithmetic has
  to survive a position it cannot price per-share.

  **A broken Schwab connection still serving its last snapshot.** Real money, real holdings,
  three weeks stale. The portfolio is neither empty nor current, and the UI must be handed
  enough to say so.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.connectors.base import (
    READ_ONLY_CONNECTION_TYPE,
    BrokerageConnection,
    ConnectionSession,
    ConnectorError,
)
from app.domain.connection import (
    ConnectedAccount,
    ConnectedPosition,
    ConnectedTransaction,
    ConnectionStatus,
    DataSource,
    Freshness,
    TaxLot,
    TransactionType,
)
from app.domain.portfolio import AssetClass
from app.domain.profile import AccountType

PROVIDER = "mock"

# Fixed so tests and the demo are reproducible. Everything else is derived from it, so the
# seeded household ages consistently rather than drifting field by field.
_NOW = datetime(2026, 8, 15, 7, 42, tzinfo=UTC)
_STALE_SINCE = _NOW - timedelta(days=21)

_FIDELITY = "fid_taxable"
_FIDELITY_ROTH = "fid_roth"
_SCHWAB = "schwab_401k"


def _fresh(status: ConnectionStatus = ConnectionStatus.active) -> Freshness:
    if status.may_serve_cached_data:
        return Freshness(
            provider=PROVIDER,
            status=status,
            as_of=_STALE_SINCE,
            last_successful_sync=_STALE_SINCE,
        )
    return Freshness(provider=PROVIDER, status=status, as_of=_NOW, last_successful_sync=_NOW)


class MockPortfolioConnector:
    """An in-memory `PortfolioConnector`. No network, no credentials, no configuration."""

    provider_name = PROVIDER

    def __init__(self, *, connected: bool = True) -> None:
        # `connected=False` models a user who has authenticated to AdvisorOS but linked nothing
        # yet — the state the dashboard has to render before any brokerage exists, which is
        # easy to forget when every fixture starts out fully populated.
        self._connected = connected
        self.disconnected: list[str] = []

    # --- linking -------------------------------------------------------------------------

    async def create_connection_session(self, user_id: str, redirect_url: str) -> ConnectionSession:
        if not user_id:
            raise ConnectorError("a connection session needs an AdvisorOS user id")
        return ConnectionSession(
            redirect_uri=f"https://mock.invalid/portal?user={user_id}&return={redirect_url}",
            session_id=f"sess_{user_id[:8]}",
            connection_type=READ_ONLY_CONNECTION_TYPE,
        )

    async def list_connections(self, user_id: str) -> list[BrokerageConnection]:
        if not self._connected:
            return []
        return [
            BrokerageConnection(
                connection_id="conn_fidelity",
                provider=PROVIDER,
                institution="Fidelity",
                status=ConnectionStatus.active,
                last_successful_sync=_NOW.isoformat(),
            ),
            BrokerageConnection(
                connection_id="conn_schwab",
                provider=PROVIDER,
                institution="Schwab",
                status=ConnectionStatus.broken,
                last_successful_sync=_STALE_SINCE.isoformat(),
                needs_reconnect=True,
            ),
        ]

    async def disconnect(self, user_id: str, connection_id: str) -> None:
        self.disconnected.append(connection_id)

    # --- reading -------------------------------------------------------------------------

    async def list_accounts(self, user_id: str) -> list[ConnectedAccount]:
        if not self._connected:
            return []
        return [
            ConnectedAccount(
                account_id=_FIDELITY,
                connection_id="conn_fidelity",
                institution="Fidelity",
                account_name="Individual Brokerage",
                account_type=AccountType.taxable,
                account_subtype="INDIVIDUAL",
                total_value=128_410.0,
                cash_value=7_900.0,
                freshness=_fresh(),
            ),
            ConnectedAccount(
                account_id=_FIDELITY_ROTH,
                connection_id="conn_fidelity",
                institution="Fidelity",
                account_name="Roth IRA",
                account_type=AccountType.roth_ira,
                account_subtype="ROTH",
                total_value=31_800.0,
                cash_value=800.0,
                freshness=_fresh(),
            ),
            ConnectedAccount(
                account_id=_SCHWAB,
                connection_id="conn_schwab",
                institution="Schwab",
                account_name="Company 401(k)",
                account_type=AccountType.traditional_401k,
                account_subtype="401K",
                total_value=64_200.0,
                cash_value=0.0,
                # Broken three weeks ago and still serving its last snapshot.
                freshness=_fresh(ConnectionStatus.broken),
            ),
        ]

    async def get_balances(self, user_id: str, account_id: str) -> ConnectedAccount:
        for account in await self.list_accounts(user_id):
            if account.account_id == account_id:
                return account
        raise ConnectorError(f"no such account: {account_id}")

    async def get_positions(self, user_id: str, account_id: str) -> list[ConnectedPosition]:
        if not self._connected:
            return []
        return list(_POSITIONS.get(account_id, ()))

    async def get_transactions(self, user_id: str, account_id: str) -> list[ConnectedTransaction]:
        if not self._connected:
            return []
        return list(_TRANSACTIONS.get(account_id, ()))


def _position(
    account_id: str,
    symbol: str,
    *,
    asset_class: AssetClass,
    quantity: float | None,
    price: float | None,
    market_value: float,
    cost_basis: float | None = None,
    lots: list[TaxLot] | None = None,
    name: str = "",
    note: str = "",
    status: ConnectionStatus = ConnectionStatus.active,
) -> ConnectedPosition:
    return ConnectedPosition(
        account_id=account_id,
        symbol=symbol,
        security_name=name,
        asset_class=asset_class,
        quantity=quantity,
        price=price,
        market_value=market_value,
        cost_basis=cost_basis,
        cost_basis_source=None if cost_basis is None else DataSource.provider_reported,
        tax_lots=lots or [],
        freshness=_fresh(status),
        user_note=note,
    )


_POSITIONS: dict[str, tuple[ConnectedPosition, ...]] = {
    _FIDELITY: (
        # Lots present and genuinely mixed: one held for years, one bought this spring. This is
        # the case where a tax estimate can narrow rather than spanning both treatments.
        _position(
            _FIDELITY,
            "NVDA",
            name="NVIDIA Corporation",
            asset_class=AssetClass.us_equity,
            quantity=400,
            price=150.0,
            market_value=60_000.0,
            cost_basis=20_000.0,
            lots=[
                TaxLot(
                    quantity=300,
                    cost_basis=9_000.0,
                    acquired_at=datetime(2021, 3, 8, tzinfo=UTC),
                ),
                TaxLot(
                    quantity=100,
                    cost_basis=11_000.0,
                    acquired_at=datetime(2026, 4, 2, tzinfo=UTC),
                ),
            ],
        ),
        _position(
            _FIDELITY,
            "VTI",
            name="Vanguard Total Stock Market ETF",
            asset_class=AssetClass.us_equity,
            quantity=110,
            price=280.0,
            market_value=30_800.0,
            cost_basis=24_000.0,
        ),
        _position(
            _FIDELITY,
            "VXUS",
            name="Vanguard Total International Stock ETF",
            asset_class=AssetClass.intl_developed_equity,
            quantity=180,
            price=62.0,
            market_value=11_160.0,
            cost_basis=10_400.0,
        ),
        # Employer stock with no basis reported at all — the RSU case. Must stay unknown.
        _position(
            _FIDELITY,
            "MSFT",
            name="Microsoft Corporation",
            asset_class=AssetClass.us_equity,
            quantity=45,
            price=412.0,
            market_value=18_540.0,
            cost_basis=None,
            note="Employer RSU grant, vested 2025 — cost basis not reported by the plan",
        ),
    ),
    _FIDELITY_ROTH: (
        # Same symbol as the taxable account. Household view must sum; tax view must not.
        _position(
            _FIDELITY_ROTH,
            "VTI",
            name="Vanguard Total Stock Market ETF",
            asset_class=AssetClass.us_equity,
            quantity=75,
            price=280.0,
            market_value=21_000.0,
            cost_basis=15_500.0,
        ),
        _position(
            _FIDELITY_ROTH,
            "BND",
            name="Vanguard Total Bond Market ETF",
            asset_class=AssetClass.bonds,
            quantity=137,
            price=73.0,
            market_value=10_001.0,
            cost_basis=10_400.0,
        ),
    ),
    _SCHWAB: (
        _position(
            _SCHWAB,
            "VTI",
            name="Vanguard Total Stock Market ETF",
            asset_class=AssetClass.us_equity,
            quantity=125,
            price=280.0,
            market_value=35_000.0,
            cost_basis=22_000.0,
            status=ConnectionStatus.broken,
        ),
        # A plan fund with no ticker to price and no share count — dollars are all there is.
        _position(
            _SCHWAB,
            "STABLE-VALUE-FUND",
            name="Company Stable Value Fund",
            asset_class=AssetClass.bonds,
            quantity=None,
            price=None,
            market_value=29_200.0,
            cost_basis=None,
            status=ConnectionStatus.broken,
        ),
    ),
}

_TRANSACTIONS: dict[str, tuple[ConnectedTransaction, ...]] = {
    _FIDELITY: (
        ConnectedTransaction(
            transaction_id="tx_1",
            account_id=_FIDELITY,
            symbol="NVDA",
            transaction_type=TransactionType.buy,
            quantity=100,
            price=110.0,
            amount=-11_000.0,
            trade_date=datetime(2026, 4, 2, tzinfo=UTC),
            description="Bought 100 NVDA",
        ),
        ConnectedTransaction(
            transaction_id="tx_2",
            account_id=_FIDELITY,
            symbol="VTI",
            transaction_type=TransactionType.dividend,
            amount=182.40,
            trade_date=datetime(2026, 6, 27, tzinfo=UTC),
            description="Dividend",
        ),
    ),
    _FIDELITY_ROTH: (
        ConnectedTransaction(
            transaction_id="tx_3",
            account_id=_FIDELITY_ROTH,
            transaction_type=TransactionType.contribution,
            amount=7_000.0,
            trade_date=datetime(2026, 1, 14, tzinfo=UTC),
            description="Annual Roth contribution",
        ),
    ),
}
