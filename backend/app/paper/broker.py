"""The simulated-execution contract, and the guarantee that it can only ever be simulated.

`app/connectors/` gets its read-only guarantee from *absence*: no order-placing identifier
exists anywhere in the package, so no call site can reach one. That technique is unavailable
here, because submitting orders is the entire job. So the guarantee is made on the other axis —
not "cannot place an order" but **"cannot reach a venue where an order costs money"**.

Three things enforce it, and all three are asserted by `tests/security`:

1. `PAPER_ONLY` is a module constant, not a setting. There is no environment variable that
   turns it off, because a switch that can be flipped is a switch that gets flipped.
2. `LIVE_TRADING_HOSTS` names the hosts that must never appear in this package. Naming them is
   what makes their absence testable — a prohibition nobody wrote down is a preference.
3. `HarnessMode` makes execution opt-in at the call site. Observing and recommending are the
   default; actually submitting requires having said so.

The mode ladder exists because the failure it prevents is not exotic. A harness that submits by
default is one misconfigured run away from placing orders nobody reviewed, and the fact that
they were paper orders this time is luck rather than design.
"""

from __future__ import annotations

from enum import Enum
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

# This package simulates execution and never reaches a real venue. Asserted by tests/security.
PAPER_ONLY = True

# Hosts that would execute against real money. Listed so their absence is testable rather than
# implied. A paper adapter added later must use its provider's paper host and nothing else.
LIVE_TRADING_HOSTS = frozenset(
    {
        "api.alpaca.markets",
        "api.tradier.com",
        "api.schwabapi.com",
        "trade.interactivebrokers",
    }
)


class HarnessMode(str, Enum):
    """How far a run is permitted to go. Ascending; each mode allows everything below it."""

    observe_only = "observe_only"
    """Read the portfolio and report it. No decision is requested, nothing is proposed."""

    recommend_only = "recommend_only"
    """Produce a checked action set and stop. The default, and the useful one for testing."""

    paper_execute = "paper_execute"
    """Additionally submit the surviving actions to a paper broker. Never chosen implicitly."""

    @property
    def may_execute(self) -> bool:
        return self is HarnessMode.paper_execute

    @property
    def may_decide(self) -> bool:
        return self is not HarnessMode.observe_only


class OrderSide(str, Enum):
    buy = "buy"
    sell = "sell"


class PaperOrder(BaseModel):
    """An instruction to a simulator. Quantity is in shares and always positive.

    Direction lives in `side` rather than in the sign of the quantity, because a negative
    quantity that loses its minus sign somewhere in a serialization round trip becomes a buy.
    """

    model_config = ConfigDict(extra="forbid")

    client_order_id: str = Field(min_length=1)
    symbol: str = Field(min_length=1)
    side: OrderSide
    quantity: float = Field(gt=0)

    # Which computed action this came from, so a fill can be traced back to the threshold that
    # implied it. A fill nobody can attribute is a fill nobody can review.
    action_id: str = Field(min_length=1)


class PaperFill(BaseModel):
    """What the simulator says happened.

    `is_simulated` is on the record itself rather than only in the type name, so a fill that is
    serialized into a log or a report cannot be mistaken for a real one by whatever reads it next.
    """

    model_config = ConfigDict(extra="forbid")

    client_order_id: str
    symbol: str
    side: OrderSide
    quantity: float
    price: float = Field(ge=0)
    is_simulated: bool = True

    @property
    def notional(self) -> float:
        return round(self.quantity * self.price, 2)


class RejectedOrder(BaseModel):
    """An order the broker declined, and why. Never raised — rejections are data.

    A run that rejects half its orders is a finding about the policy, and a harness that raised
    on the first one would hide the other half.
    """

    model_config = ConfigDict(extra="forbid")

    client_order_id: str
    symbol: str
    reason: str


class PaperPosition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    symbol: str
    quantity: float
    average_price: float = Field(ge=0)

    @property
    def market_value(self) -> float:
        return round(self.quantity * self.average_price, 2)


class PaperAccount(BaseModel):
    """Simulated balances. No account number, because there is no account."""

    model_config = ConfigDict(extra="forbid")

    cash: float
    positions: list[PaperPosition] = Field(default_factory=list)
    is_simulated: bool = True

    @property
    def positions_value(self) -> float:
        return round(sum(p.market_value for p in self.positions), 2)

    @property
    def equity(self) -> float:
        return round(self.cash + self.positions_value, 2)


@runtime_checkable
class PaperBroker(Protocol):
    """A simulated venue. Implementations must never contact a live trading host."""

    broker_id: str

    def get_account(self) -> PaperAccount: ...

    def submit(self, orders: list[PaperOrder]) -> tuple[list[PaperFill], list[RejectedOrder]]:
        """Attempt every order, returning what filled and what did not.

        Attempts all of them rather than stopping at the first rejection, for the same reason
        `ActionSet.check_feasible` returns every problem at once.
        """
        ...
