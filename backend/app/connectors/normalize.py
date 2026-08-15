"""Turning what a brokerage reported into what the analytics already understand.

The whole point of this module is that nothing downstream changes. `analyze_portfolio`,
`evaluate_guardrails`, `concentration.propose`, `sensitivity.sweep_concentration`, and
`counterfactual.evaluate` were written against `Portfolio`/`Holding` and keep running exactly as
they are. Connected data earns its way in by becoming that type, not by the engine learning
about brokerages.

**The projection is one-way, and the original is kept.** `ConnectedPortfolio` stays the source
of record. The `Portfolio` this builds is a lossy view of it, suitable for household questions —
concentration, HHI, asset mix — and unsuitable for anything account-shaped. Selling AAPL is a
different act in a taxable account and in a Roth, so tax and location questions must read the
connected model, never this projection.

**Nothing is invented on the way through.** `Holding.market_value` is required and non-null,
which is precisely the field a brokerage most often cannot supply. A position that cannot be
valued is *dropped from the projection and reported*, not defaulted to zero: a zero-valued
holding is counted as real money at zero by every weight calculation, quietly diluting every
percentage in the report. `NormalizationResult.unpriced` carries what was left out so the UI can
say so, and `priced_coverage` says how much of the portfolio the numbers actually describe.

**Account identity survives.** Holdings keep their `account_type`, so tax-advantaged detection
still works per position, and `symbol_accounts` records which accounts each symbol came from.
The same symbol in three accounts becomes three holdings, and `analyze_portfolio` aggregates
them by symbol — the behaviour that was a duplicate-symbol corruption bug before it was fixed
and is the correct behaviour here.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from app.domain.connection import ConnectedPortfolio, ConnectedPosition, Freshness
from app.domain.portfolio import AssetClass, Holding, Portfolio
from app.domain.profile import AccountType

# Uninvested account cash is projected under one symbol so household analytics see a single
# cash line, while `symbol_accounts` keeps the per-account attribution the merge would lose.
CASH_SYMBOL = "CASH"


class UnpricedPosition(BaseModel):
    """A holding that could not be given a value, and why it was left out."""

    model_config = ConfigDict(extra="forbid")

    account_id: str
    symbol: str
    reason: str


class NormalizationResult(BaseModel):
    """The projection, plus an honest account of what did not make it in."""

    model_config = ConfigDict(extra="forbid")

    portfolio: Portfolio
    unpriced: list[UnpricedPosition] = Field(default_factory=list)
    excluded_count: int = 0
    priced_coverage: float = 1.0
    stale_accounts: list[str] = Field(default_factory=list)
    symbol_accounts: dict[str, list[str]] = Field(
        default_factory=dict,
        description="Symbol -> account ids it was held in, so aggregation stays attributable",
    )
    freshness: list[Freshness] = Field(default_factory=list)

    @property
    def is_complete(self) -> bool:
        return not self.unpriced and not self.stale_accounts

    def caveats(self) -> list[str]:
        """Sentences the UI must show beside any figure derived from this portfolio."""
        lines: list[str] = []
        if self.unpriced:
            names = ", ".join(sorted({u.symbol for u in self.unpriced}))
            lines.append(
                f"{len(self.unpriced)} position(s) could not be valued and are excluded from "
                f"these figures: {names}. They are not worth zero — they are unknown."
            )
        if self.stale_accounts:
            lines.append(
                f"{len(self.stale_accounts)} account(s) have a broken or disabled connection. "
                "The holdings shown are the last successful snapshot, not current data."
            )
        if self.priced_coverage < 1.0:
            lines.append(
                f"These percentages describe {self.priced_coverage:.0%} of the connected positions."
            )
        return lines


def to_portfolio(connected: ConnectedPortfolio) -> NormalizationResult:
    """Project connected data onto the `Portfolio` the analytics already consume."""
    holdings: list[Holding] = []
    unpriced: list[UnpricedPosition] = []
    symbol_accounts: dict[str, list[str]] = {}

    account_types = {a.account_id: a.account_type for a in connected.accounts}
    included = connected.included_positions

    for position in included:
        value = position.effective_value()
        if value is None:
            unpriced.append(
                UnpricedPosition(
                    account_id=position.account_id,
                    symbol=position.symbol,
                    reason=_why_unpriced(position),
                )
            )
            continue

        holdings.append(
            Holding(
                symbol=position.symbol,
                name=position.security_name,
                asset_class=position.asset_class,
                quantity=position.quantity,
                market_value=value,
                cost_basis=position.cost_basis,
                # The account's tax treatment, not the position's. `other` when the account is
                # unknown — never `taxable`, which would assert a tax status nobody reported and
                # make every downstream tax estimate confidently wrong.
                account_type=account_types.get(position.account_id, AccountType.other),
            )
        )
        symbol_accounts.setdefault(position.symbol.upper(), []).append(position.account_id)

    # Uninvested cash is part of the portfolio, and leaving it out is not a rounding error: it
    # shrinks the denominator every weight is computed against, so a position at 27% of holdings
    # is reported at 28% of a portfolio that also contains cash. Concentration is inflated for
    # exactly the people most likely to be holding cash deliberately, and since the sensitivity
    # engine measures distance to a threshold in single percentage points, an inflated weight can
    # turn a robust conclusion into a fragile one or push a position over a cap it never crossed.
    for account in connected.included_accounts:
        if not account.cash_value:
            continue
        holdings.append(
            Holding(
                symbol=CASH_SYMBOL,
                name=f"Cash — {account.institution} {account.account_name}".strip(),
                asset_class=AssetClass.cash,
                quantity=None,
                market_value=account.cash_value,
                # Cash carries no unrealized gain, so its basis is its value. This keeps a tax
                # estimate over a cash "sale" at zero rather than unknown.
                cost_basis=account.cash_value,
                account_type=account.account_type,
            )
        )
        symbol_accounts.setdefault(CASH_SYMBOL, []).append(account.account_id)

    return NormalizationResult(
        portfolio=Portfolio(holdings=holdings),
        unpriced=unpriced,
        excluded_count=len(connected.positions) - len(included),
        priced_coverage=connected.priced_coverage(),
        stale_accounts=[a.account_id for a in connected.included_accounts if a.freshness.is_stale],
        symbol_accounts={k: sorted(set(v)) for k, v in symbol_accounts.items()},
        freshness=[a.freshness for a in connected.included_accounts],
    )


def _why_unpriced(position: ConnectedPosition) -> str:
    if position.quantity is None and position.price is None:
        return "no market value, quantity, or price reported"
    if position.price is None:
        return "share count reported but no price, so no value can be computed"
    return "price reported but no share count, so no value can be computed"
