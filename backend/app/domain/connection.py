"""What a brokerage told us, and how much of it we actually know.

This is the boundary type between an outside data provider and everything AdvisorOS computes.
No provider vocabulary crosses it — nothing downstream should be able to tell whether a position
arrived from SnapTrade, from Plaid, from a mock, or from a CSV, because the moment policy code
can tell, swapping providers becomes a rewrite instead of an adapter.

Three rules shape every model here, and all three exist because a brokerage feed is a much
lower-quality input than it looks like.

**Absent is `None`, never `0.0`.** A hand-typed form produces a value for every field it shows.
A brokerage produces whatever that particular institution happens to report through whatever
integration the aggregator built for it, which means cost basis, tax lots, and even prices go
missing routinely and without warning. `TaxRange` already established that unknown and zero are
different claims; here that distinction has to hold across far more fields, so optionality is
the default rather than the exception.

**Every fact carries where it came from.** `DataSource` extends to portfolio numbers the same
provenance discipline `PolicyParameter` applies to persona thresholds. "NVDA quantity: 400" is
not one kind of fact — it is very different depending on whether Fidelity reported it, the
aggregator derived it from transactions, or the user typed it. The report should be able to say
which, for any number it shows.

**Freshness is not decoration.** A connection can be broken while the provider keeps serving its
last successful snapshot, so data can be simultaneously present, plausible, and weeks old. The
two failure modes are opposite and both are bad: treating stale data as current, and treating a
broken connection as an empty portfolio. `Freshness` is therefore required rather than optional,
so no code path can render a value without having been handed the means to qualify it.

What is deliberately *not* here: anything about placing orders. See `connectors/base.py`.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.domain.portfolio import AssetClass
from app.domain.profile import AccountType


class ConnectionStatus(str, Enum):
    """The state of the link to an institution — never the state of the money behind it."""

    active = "active"
    """Syncing normally."""

    broken = "broken"
    """Credentials expired or the institution refused. Cached data may still be served."""

    disabled = "disabled"
    """Switched off by the provider or the user. Cached data may still be served."""

    pending = "pending"
    """Registered, first sync not yet complete. Absence of holdings means nothing yet."""

    @property
    def is_live(self) -> bool:
        return self is ConnectionStatus.active

    @property
    def may_serve_cached_data(self) -> bool:
        """A broken link does not empty an account. Whatever was last seen is still the best
        available picture — it just must never be presented as current."""
        return self in (ConnectionStatus.broken, ConnectionStatus.disabled)


class DataSource(str, Enum):
    """Where a specific number came from. The provenance principle, applied to money."""

    provider_reported = "provider_reported"
    """The institution stated this value."""

    provider_computed = "provider_computed"
    """The aggregator derived it — e.g. basis reconstructed from transaction history."""

    user_supplied = "user_supplied"
    """Typed by the user, or corrected by them after import."""

    derived = "derived"
    """Computed by AdvisorOS from other fields."""

    @property
    def is_from_institution(self) -> bool:
        return self is DataSource.provider_reported


class Freshness(BaseModel):
    """When this was true, and whether the pipe that carried it is still open.

    Required on every connected object. Making it optional would let a caller render a balance
    with no way to say how old it is, which is the specific failure this type exists to prevent.
    """

    model_config = ConfigDict(extra="forbid")

    provider: str = Field(min_length=1, description="Adapter that produced this, e.g. 'snaptrade'")
    status: ConnectionStatus = ConnectionStatus.active
    as_of: datetime | None = Field(
        default=None, description="When the provider says this snapshot was current"
    )
    last_successful_sync: datetime | None = Field(
        default=None, description="Last completed sync, which may be older than `as_of`"
    )

    @property
    def is_stale(self) -> bool:
        """True when the data is real but the link behind it is not currently working."""
        return self.status.may_serve_cached_data

    def describe(self) -> str:
        """One phrase for the UI. Never returns something that reads as 'live' when it is not."""
        when = self.as_of or self.last_successful_sync
        stamp = when.strftime("%b %d, %Y at %H:%M") if when else "an unknown time"
        if self.status is ConnectionStatus.pending:
            return f"{self.provider}: first sync has not completed yet"
        if self.is_stale:
            return (
                f"{self.provider}: last synced {stamp}, and the connection is "
                f"{self.status.value} — this is the last known state, not current data"
            )
        return f"{self.provider}: synced {stamp}"


class TaxLot(BaseModel):
    """One acquisition of a security, when the provider actually reports lots.

    Every field is optional because partial lots are common: an institution may report a
    quantity and an acquisition date with no basis, or basis with no date. A lot with no
    quantity still carries information and is worth keeping rather than dropping.
    """

    model_config = ConfigDict(extra="forbid")

    quantity: float | None = Field(default=None, ge=0)
    cost_basis: float | None = Field(default=None, ge=0)
    acquired_at: datetime | None = None
    source: DataSource = DataSource.provider_reported

    def is_long_term(self, as_of: datetime) -> bool | None:
        """Whether this lot has been held over a year — None when the date is unknown.

        This is the field whose absence forces `TaxRange` to span long-term and ordinary rates.
        Where a provider does report it, the range can legitimately narrow to one treatment; the
        `None` case is why it usually cannot.
        """
        if self.acquired_at is None:
            return None
        return (as_of - self.acquired_at).days > 365


class ConnectedAccount(BaseModel):
    """One account at one institution."""

    model_config = ConfigDict(extra="forbid")

    account_id: str = Field(min_length=1, description="Provider's id, stable across syncs")
    connection_id: str = Field(min_length=1)
    institution: str = Field(min_length=1)
    account_name: str = ""

    # The mapping into AdvisorOS's own tax treatment vocabulary. `other` is the honest landing
    # place for anything the adapter cannot confidently classify — guessing between a Roth and a
    # traditional IRA would silently change every tax number downstream.
    account_type: AccountType = AccountType.other
    account_subtype: str = Field(
        default="", description="Provider's own label, kept verbatim for display and debugging"
    )

    currency: str = Field(default="USD", min_length=3, max_length=3)
    total_value: float | None = Field(default=None, ge=0)
    cash_value: float | None = Field(default=None, ge=0)

    freshness: Freshness
    excluded: bool = Field(
        default=False,
        description="User chose to leave this account out of analysis. Kept, not deleted.",
    )


class ConnectedPosition(BaseModel):
    """One holding, in one account, as the provider described it."""

    model_config = ConfigDict(extra="forbid")

    account_id: str = Field(min_length=1)
    symbol: str = Field(min_length=1, max_length=32)
    security_name: str = ""
    instrument_type: str = Field(
        default="", description="Provider's instrument label, before any AdvisorOS mapping"
    )
    asset_class: AssetClass = AssetClass.other

    quantity: float | None = Field(default=None, ge=0)
    price: float | None = Field(default=None, ge=0)
    market_value: float | None = Field(default=None, ge=0)
    currency: str = Field(default="USD", min_length=3, max_length=3)

    cost_basis: float | None = Field(default=None, ge=0)
    tax_lots: list[TaxLot] = Field(default_factory=list)

    quantity_source: DataSource = DataSource.provider_reported
    value_source: DataSource = DataSource.provider_reported
    cost_basis_source: DataSource | None = Field(
        default=None, description="None when no basis is known at all"
    )

    freshness: Freshness
    excluded: bool = False
    user_note: str = Field(
        default="", description="e.g. 'employer RSU, restricted until 2027' — user's own words"
    )

    @model_validator(mode="after")
    def _basis_source_tracks_basis(self) -> ConnectedPosition:
        if self.cost_basis is None and self.cost_basis_source is not None:
            raise ValueError(f"{self.symbol}: cost_basis_source set with no cost_basis")
        if self.cost_basis is not None and self.cost_basis_source is None:
            raise ValueError(f"{self.symbol}: cost_basis given with no source")
        return self

    @property
    def unrealized_gain(self) -> float | None:
        if self.cost_basis is None or self.market_value is None:
            return None
        return self.market_value - self.cost_basis

    @property
    def has_usable_tax_lots(self) -> bool:
        """Lots are only better than aggregate basis when they actually carry basis."""
        return any(lot.cost_basis is not None for lot in self.tax_lots)

    def effective_value(self) -> float | None:
        """Market value, falling back to quantity x price, or None.

        Deliberately does not invent a value from a quantity with no price: a share count alone
        says nothing about what a position is worth, and a zero would be counted as real money
        by every downstream weight calculation.
        """
        if self.market_value is not None:
            return self.market_value
        if self.quantity is not None and self.price is not None:
            return self.quantity * self.price
        return None


class TransactionType(str, Enum):
    buy = "buy"
    sell = "sell"
    dividend = "dividend"
    interest = "interest"
    contribution = "contribution"
    withdrawal = "withdrawal"
    fee = "fee"
    transfer = "transfer"
    other = "other"


class ConnectedTransaction(BaseModel):
    """One historical activity.

    Imported as *context* and kept away from the recommendation path on purpose. Observed
    behaviour is evidence about what someone did; it is not a statement of what they want.
    Frequent trading may be conviction, may be panic, may be a spouse, may be a vesting schedule
    someone else controls — and turning it into "this person prefers aggressive investing" would
    be exactly the inference this product refuses to make about a stated risk tolerance.
    """

    model_config = ConfigDict(extra="forbid")

    transaction_id: str = Field(min_length=1)
    account_id: str = Field(min_length=1)
    symbol: str | None = None
    transaction_type: TransactionType = TransactionType.other

    quantity: float | None = None
    price: float | None = Field(default=None, ge=0)
    amount: float | None = Field(default=None, description="Signed: negative leaves the account")
    currency: str = Field(default="USD", min_length=3, max_length=3)

    trade_date: datetime | None = None
    settlement_date: datetime | None = None
    description: str = ""


class ConnectedPortfolio(BaseModel):
    """Everything one user has connected — the source of record for account-level truth.

    The flattened `Portfolio` that analytics consume is a *projection* of this, not a
    replacement for it. Aggregating AAPL across a taxable account and a Roth is correct for
    concentration and wrong for tax, so both views have to remain available and the account
    attribution must survive the trip. `to_portfolio()` in `connectors/normalize.py` builds the
    projection; nothing here throws information away to produce it.
    """

    model_config = ConfigDict(extra="forbid")

    accounts: list[ConnectedAccount] = Field(default_factory=list)
    positions: list[ConnectedPosition] = Field(default_factory=list)
    transactions: list[ConnectedTransaction] = Field(default_factory=list)

    @property
    def included_accounts(self) -> list[ConnectedAccount]:
        return [a for a in self.accounts if not a.excluded]

    @property
    def included_positions(self) -> list[ConnectedPosition]:
        """Positions the user kept, from accounts the user kept."""
        live = {a.account_id for a in self.included_accounts}
        return [p for p in self.positions if not p.excluded and p.account_id in live]

    @property
    def is_any_data_stale(self) -> bool:
        return any(a.freshness.is_stale for a in self.included_accounts)

    @property
    def total_value(self) -> float | None:
        """Summed position values plus account cash, or None when nothing is priced.

        Returns None rather than 0.0 for an unpriced portfolio, because "we could not value
        this" and "this is worth nothing" must not render identically.
        """
        values = [v for p in self.included_positions if (v := p.effective_value()) is not None]
        cash = [a.cash_value for a in self.included_accounts if a.cash_value is not None]
        if not values and not cash:
            return None
        return sum(values) + sum(cash)

    def priced_coverage(self) -> float:
        """Fraction of included positions carrying a usable value.

        Reported rather than used as a gate: an 80%-priced portfolio still supports useful
        analysis, but a concentration figure computed from it deserves to be labelled.
        """
        included = self.included_positions
        if not included:
            return 1.0
        priced = sum(1 for p in included if p.effective_value() is not None)
        return priced / len(included)

    def positions_for(self, account_id: str) -> list[ConnectedPosition]:
        return [p for p in self.positions if p.account_id == account_id]

    def accounts_holding(self, symbol: str) -> list[str]:
        """Which accounts hold a symbol — the provenance that household aggregation would lose."""
        upper = symbol.upper()
        return sorted({p.account_id for p in self.included_positions if p.symbol.upper() == upper})
