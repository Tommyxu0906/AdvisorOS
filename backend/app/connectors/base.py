"""The contract every brokerage data provider is reduced to.

Seven read methods and a disconnect. That is the entire surface, and the shape of it is the
product decision: **there is no method here that moves money or places an order.**

That absence is load-bearing rather than incidental. SnapTrade, Plaid, and every comparable
aggregator expose trading. Declining to use it is not the same as declining to import it — a
connector class with a `place_order` the application merely never calls is one careless call
site away from executing a trade, and the protocol would have blessed it. So the capability is
kept out of the type, out of the adapters, and out of the dependency tree: the SnapTrade adapter
speaks HTTP to specific endpoints via `httpx` rather than importing a vendor SDK that ships
trading services alongside the read ones.

`BROKERAGE_ACCESS_MODE` states the same thing as a value tests can assert on. A test that fails
when a trading symbol appears anywhere under `app/connectors/` is what keeps this true after the
person who wrote it has moved on.

The other reason this protocol is narrow: it must be satisfiable by a mock with no credentials.
CI never logs into a brokerage, and demo mode never touches a real account, so both run the same
`MockPortfolioConnector` through the same code path as production. A fake that bypasses the
real pipeline tests nothing.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from app.domain.connection import (
    ConnectedAccount,
    ConnectedPosition,
    ConnectedTransaction,
    ConnectionStatus,
)

# AdvisorOS reads brokerage data and never acts on it. Asserted by tests/security.
BROKERAGE_ACCESS_MODE = "READ_ONLY"

# Passed to a provider's hosted connection portal. Named here rather than at the call site so
# there is exactly one place the read-only intent is expressed, and so a test can assert it.
READ_ONLY_CONNECTION_TYPE = "read"

# Portal modes that grant order-placing rights. Never requested; listed so the prohibition is
# explicit and testable rather than implied by their absence.
FORBIDDEN_CONNECTION_TYPES = frozenset({"trade", "trade-if-available"})


class ConnectionSession(BaseModel):
    """A short-lived handoff to a provider's hosted portal.

    The redirect URI is the only thing the browser receives. Credentials — the application's
    provider key and the per-user secret — stay server-side, which is the whole reason the
    portal exists rather than the frontend talking to the provider directly.
    """

    model_config = ConfigDict(extra="forbid")

    redirect_uri: str = Field(min_length=1)
    session_id: str = ""
    expires_at: str | None = None
    connection_type: str = READ_ONLY_CONNECTION_TYPE


class BrokerageConnection(BaseModel):
    """One link between a user and one institution, possibly covering several accounts."""

    model_config = ConfigDict(extra="forbid")

    connection_id: str = Field(min_length=1)
    provider: str = Field(min_length=1)
    institution: str = Field(min_length=1)
    status: ConnectionStatus = ConnectionStatus.active
    created_at: str | None = None
    last_successful_sync: str | None = None
    # True when the provider says re-authentication is needed. Distinct from `status` because a
    # connection can be broken for reasons the user cannot fix by logging in again.
    needs_reconnect: bool = False


class ConnectorError(RuntimeError):
    """A provider call failed. Never carries a credential in its message."""


@runtime_checkable
class PortfolioConnector(Protocol):
    """Read-only access to a user's brokerage data.

    `user_id` is always the AdvisorOS `app_users.id` UUID — never an email. Adapters map it to
    whatever the provider wants internally, and that mapping is the adapter's problem, not the
    caller's.
    """

    provider_name: str

    async def create_connection_session(self, user_id: str, redirect_url: str) -> ConnectionSession:
        """Begin linking an institution. Must request read-only access."""
        ...

    async def list_connections(self, user_id: str) -> list[BrokerageConnection]: ...

    async def list_accounts(self, user_id: str) -> list[ConnectedAccount]: ...

    async def get_positions(self, user_id: str, account_id: str) -> list[ConnectedPosition]: ...

    async def get_balances(self, user_id: str, account_id: str) -> ConnectedAccount:
        """Cash and total value for one account, with its own freshness stamp.

        Returns the account rather than a separate balance type: a balance with no account
        identity and no freshness is a number nobody can safely render.
        """
        ...

    async def get_transactions(
        self, user_id: str, account_id: str
    ) -> list[ConnectedTransaction]: ...

    async def disconnect(self, user_id: str, connection_id: str) -> None:
        """Remove one institution link. Does not delete the AdvisorOS account or its history."""
        ...
