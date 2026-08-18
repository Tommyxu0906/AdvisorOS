"""A simulator with no network, no clock, and no pretence about what it is.

Fills happen at the reference price it was handed, in full, immediately. That is not a modelling
choice so much as a refusal to make one: a fake that invented slippage, partial fills, or queue
position would produce numbers with the shape of a backtest and none of the content, and someone
would eventually quote them.

So the honest limits are stated in the type and repeated wherever a number leaves it:

  - no slippage, no market impact, no spread
  - no partial fills, no queue position, no latency
  - no borrowing, no shorting, no margin
  - no corporate actions, no dividends

What it *does* enforce is the arithmetic that would actually stop an order: you cannot sell
shares you do not hold, and you cannot spend cash you do not have. Those are the two rejections
the decision engine's feasibility check is supposed to have prevented upstream, which makes this
broker a second, independent opinion on the same question rather than a rubber stamp.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from app.paper.broker import (
    OrderSide,
    PaperAccount,
    PaperFill,
    PaperOrder,
    PaperPosition,
    RejectedOrder,
)

SIMULATION_LIMITS = (
    "fills are immediate, complete, and at the reference price",
    "no slippage, spread, market impact, or queue position",
    "no partial fills, latency, or rejections from a venue",
    "no margin, shorting, dividends, or corporate actions",
)


class MockPaperBroker(BaseModel):
    """Holds cash and positions in memory and settles orders against a fixed price map.

    Mutable on purpose — it is a running account, and successive rounds of the harness are meant
    to see the effect of the last one. `snapshot()` is how a caller takes an immutable copy.
    """

    model_config = ConfigDict(extra="forbid")

    broker_id: str = "mock_paper"

    cash: float = Field(default=0.0)
    positions: dict[str, PaperPosition] = Field(default_factory=dict)

    prices: dict[str, float] = Field(
        default_factory=dict,
        description="Reference price per symbol. A symbol absent here cannot be traded.",
    )

    fills: list[PaperFill] = Field(default_factory=list)
    rejections: list[RejectedOrder] = Field(default_factory=list)

    # ---- reads -------------------------------------------------------------------------

    def get_account(self) -> PaperAccount:
        return PaperAccount(
            cash=round(self.cash, 2),
            positions=[self.positions[s] for s in sorted(self.positions)],
        )

    def snapshot(self) -> PaperAccount:
        """Alias for `get_account`, named for the case where the point is the point-in-time copy."""
        return self.get_account()

    # ---- writes ------------------------------------------------------------------------

    def submit(self, orders: list[PaperOrder]) -> tuple[list[PaperFill], list[RejectedOrder]]:
        """Settle each order in the order given, against state the previous ones have updated.

        Sequential rather than simultaneous, because a plan that sells to fund a purchase only
        works if the sale settles first — and if the harness ordered them wrongly, this is where
        that shows up as a rejection instead of as a silently overdrawn account.
        """
        filled: list[PaperFill] = []
        rejected: list[RejectedOrder] = []

        for order in orders:
            price = self.prices.get(order.symbol.upper())
            if price is None:
                rejected.append(
                    RejectedOrder(
                        client_order_id=order.client_order_id,
                        symbol=order.symbol,
                        reason=(
                            f"no reference price for {order.symbol}; the simulator will not "
                            "invent one"
                        ),
                    )
                )
                continue

            outcome = (
                self._sell(order, price)
                if order.side is OrderSide.sell
                else self._buy(order, price)
            )
            if isinstance(outcome, RejectedOrder):
                rejected.append(outcome)
            else:
                filled.append(outcome)

        self.fills.extend(filled)
        self.rejections.extend(rejected)
        return filled, rejected

    def _sell(self, order: PaperOrder, price: float) -> PaperFill | RejectedOrder:
        held = self.positions.get(order.symbol.upper())
        if held is None:
            return RejectedOrder(
                client_order_id=order.client_order_id,
                symbol=order.symbol,
                reason=f"cannot sell {order.symbol}: not held",
            )
        # Tolerance rather than exact comparison: share counts arrive from a policy that divided
        # dollars by a price, so an intended full exit lands a rounding error above the position.
        if order.quantity > held.quantity + 1e-6:
            return RejectedOrder(
                client_order_id=order.client_order_id,
                symbol=order.symbol,
                reason=(
                    f"cannot sell {order.quantity:g} shares of {order.symbol}: "
                    f"only {held.quantity:g} held"
                ),
            )

        remaining = round(held.quantity - order.quantity, 8)
        if remaining <= 1e-6:
            del self.positions[order.symbol.upper()]
        else:
            # Average cost is unchanged by a sale; only the share count moves.
            self.positions[order.symbol.upper()] = PaperPosition(
                symbol=held.symbol, quantity=remaining, average_price=held.average_price
            )

        self.cash = round(self.cash + order.quantity * price, 2)
        return PaperFill(
            client_order_id=order.client_order_id,
            symbol=order.symbol,
            side=OrderSide.sell,
            quantity=order.quantity,
            price=price,
        )

    def _buy(self, order: PaperOrder, price: float) -> PaperFill | RejectedOrder:
        cost = order.quantity * price
        if cost > self.cash + 1e-6:
            return RejectedOrder(
                client_order_id=order.client_order_id,
                symbol=order.symbol,
                reason=(
                    f"cannot buy {order.quantity:g} shares of {order.symbol} for "
                    f"${cost:,.2f}: only ${self.cash:,.2f} in cash"
                ),
            )

        key = order.symbol.upper()
        held = self.positions.get(key)
        if held is None:
            self.positions[key] = PaperPosition(
                symbol=key, quantity=order.quantity, average_price=price
            )
        else:
            total_shares = held.quantity + order.quantity
            blended = (held.quantity * held.average_price + cost) / total_shares
            self.positions[key] = PaperPosition(
                symbol=key, quantity=round(total_shares, 8), average_price=round(blended, 6)
            )

        self.cash = round(self.cash - cost, 2)
        return PaperFill(
            client_order_id=order.client_order_id,
            symbol=order.symbol,
            side=OrderSide.buy,
            quantity=order.quantity,
            price=price,
        )
