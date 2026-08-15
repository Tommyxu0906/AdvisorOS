"""Recommendations as typed objects rather than sentences.

Until now a recommendation was a string. The committee charter asks each advisor to "name
amounts, sequences, and conditions", and those amounts then lived inside prose that nothing
parsed and nothing checked. A report telling someone to move $40,000 into bonds when they hold
$12,000 in liquid assets passed every layer of the system, because no layer could read it.

A `ProposedAction` is the same recommendation in a form arithmetic can reach. That buys three
things the string could not offer:

  Feasibility. `ActionSet.check_feasible()` is ordinary arithmetic over the person's real
  balances — you cannot sell shares that are not there, or spend money that is not there.

  Counterfactual scoring. An action can be applied to a copy of the portfolio and the analytics
  recomputed, so "this reduces your largest position to 20%" is verified rather than asserted.
  See analytics/counterfactual.py.

  Comparable disagreement. Two advisors proposing different trims are now two numbers over the
  same policy, not two paragraphs whose difference has to be read out of the tone.

The prose does not go away — `rationale` carries it, and the model still explains and objects.
What changes is that the *decision* is a value, and the sentence is a comment on it.

Nothing here computes what a good action is. Policies produce actions (app/policy/), the
committee argues about them (app/committee/), and this module only says what an action is and
whether a set of them is arithmetically possible.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.domain.portfolio import AssetClass, Portfolio
from app.domain.profile import AccountType

# Two dollar amounts are "the same" below this. Share counts are floats and weights are
# divisions, so exact equality is the wrong test for whether a plan spends what it has.
MONEY_EPSILON = 0.01
WEIGHT_EPSILON = 0.005


class ActionKind(str, Enum):
    trim_position = "trim_position"
    add_position = "add_position"
    rebalance_to_target = "rebalance_to_target"
    pay_down_debt = "pay_down_debt"
    build_emergency_fund = "build_emergency_fund"
    redirect_cashflow = "redirect_cashflow"
    hold = "hold"

    @property
    def raises_cash(self) -> bool:
        """True when carrying this out produces money the later steps can spend."""
        return self is ActionKind.trim_position

    @property
    def spends_cash(self) -> bool:
        return self in (
            ActionKind.add_position,
            ActionKind.pay_down_debt,
            ActionKind.build_emergency_fund,
        )


class InfeasibleReason(str, Enum):
    """Why a set cannot be carried out. Named rather than free text so the eval harness and the
    UI can count them by kind."""

    no_such_holding = "no_such_holding"
    oversells_holding = "oversells_holding"
    double_disposal = "double_disposal"
    insufficient_cash = "insufficient_cash"
    weights_do_not_sum = "weights_do_not_sum"
    duplicate_action_id = "duplicate_action_id"


class Infeasibility(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: InfeasibleReason
    action_id: str | None = None
    message: str

    def __str__(self) -> str:  # pragma: no cover - convenience for logs and test output
        return f"[{self.reason.value}] {self.message}"


class TaxRange(BaseModel):
    """What selling would cost in tax, expressed as the range the data can actually support.

    The previous version of this was a single float, and it rendered as "an estimated $3,200".
    That figure is wrong in a specific and misleading way: it is not a computed amount with some
    error around it, it is one arbitrary point inside a wide band, printed to the dollar.

    Two things are unknown and neither is a rounding error.

    **The rate.** `Holding` records no acquisition date, so nothing says whether a sale is a
    long-term gain or a short-term one taxed as ordinary income. That is not a missing field to
    be added later — brokerages report it per lot, and this product deliberately does not ask
    users to key in their lot history. The gap between those two treatments is most of the range.

    **Which lots.** Basis is recorded per position, so the estimate assumes a sale realizes gain
    in the same proportion the whole position carries. A seller who specifies their highest-basis
    lots could owe far less than `low`; one who sells the oldest shares could owe more than
    `high`. Lot selection can move the answer outside this range entirely, which is why the
    range is presented as a range of *assumptions* rather than a confidence interval.

    So the honest output is a span with its assumptions named, and a UI that shows "$3,200 –
    $6,800, depending on holding period" rather than a number that looks computed.
    """

    model_config = ConfigDict(extra="forbid")

    low_usd: float = Field(ge=0, description="Whole sale treated as a long-term gain")
    high_usd: float = Field(ge=0, description="Whole sale treated as ordinary income")
    assumption: str = Field(
        default="",
        description="Why the range is this wide, in the user's terms — rendered beside it",
    )

    @model_validator(mode="after")
    def _ordered(self) -> TaxRange:
        if self.low_usd > self.high_usd:
            raise ValueError(f"tax range low {self.low_usd} exceeds high {self.high_usd}")
        return self

    @property
    def is_certain(self) -> bool:
        """True only where the answer genuinely has no spread — a tax-advantaged account."""
        return self.low_usd == self.high_usd

    def render(self) -> str:
        if self.is_certain:
            return f"${self.low_usd:,.0f}"
        return f"${self.low_usd:,.0f}-${self.high_usd:,.0f}"

    def __add__(self, other: TaxRange) -> TaxRange:
        return TaxRange(
            low_usd=self.low_usd + other.low_usd,
            high_usd=self.high_usd + other.high_usd,
            assumption=self.assumption or other.assumption,
        )


class ProposedAction(BaseModel):
    """One concrete step, sized in exactly one way.

    The three sizing fields are mutually exclusive on purpose. "Sell 40 shares", "sell $9,000
    worth", and "take it to 20% of the portfolio" are different instructions that happen to
    coincide at one moment; accepting more than one at a time would mean silently choosing which
    to believe when the price moves between computing and applying.
    """

    model_config = ConfigDict(extra="forbid")

    action_id: str = Field(min_length=1)
    kind: ActionKind

    symbol: str | None = None
    asset_class: AssetClass | None = None
    account_type: AccountType | None = None

    shares: float | None = Field(default=None, ge=0)
    amount_usd: float | None = Field(default=None, ge=0)
    target_weight: float | None = Field(default=None, ge=0, le=1)

    # Lower runs first. Resolving a blocking guardrail always outranks anything optional, so
    # sequence is the field that carries "pay the 22.9% card before buying more equities".
    sequence: int = Field(default=0, ge=0)

    # advisor_id, or "policy" when the engine produced it with no persona attached.
    proposed_by: str = "policy"
    rationale: str = ""

    # A range, never a point. None means "not estimable", never "zero". See TaxRange.
    estimated_tax: TaxRange | None = None

    @model_validator(mode="after")
    def _one_sizing_form(self) -> ProposedAction:
        if self.kind is ActionKind.hold:
            # A hold is a decision, not a transaction. Sizing it is meaningless.
            if any(v is not None for v in (self.shares, self.amount_usd, self.target_weight)):
                raise ValueError("a hold action cannot carry a size")
            return self

        given = [v is not None for v in (self.shares, self.amount_usd, self.target_weight)]
        if sum(given) != 1:
            raise ValueError(
                "exactly one of shares, amount_usd, or target_weight must be set "
                f"(action {self.action_id} set {sum(given)})"
            )
        return self

    def cash_effect(self, portfolio: Portfolio) -> float:
        """Signed dollars this action moves: positive raises cash, negative spends it.

        Returns 0.0 when the size cannot be resolved to dollars — a `target_weight` needs the
        portfolio to interpret it, and a symbol that is not held has no price here.
        """
        magnitude = self._dollar_magnitude(portfolio)
        if magnitude is None:
            return 0.0
        if self.kind.raises_cash:
            return magnitude
        if self.kind.spends_cash:
            return -magnitude
        return 0.0

    def _dollar_magnitude(self, portfolio: Portfolio) -> float | None:
        if self.amount_usd is not None:
            return self.amount_usd

        if self.symbol is None:
            return None
        held = _value_of(portfolio, self.symbol)

        if self.shares is not None:
            price = _price_per_share(portfolio, self.symbol)
            return None if price is None else self.shares * price

        if self.target_weight is not None:
            total = portfolio.total_value
            if total <= 0:
                return None
            # The distance between where the position is and where it should be.
            return abs(held - self.target_weight * total)

        return None


class ActionSet(BaseModel):
    """An ordered plan, and the arithmetic that says whether it can actually be carried out."""

    model_config = ConfigDict(extra="forbid")

    actions: list[ProposedAction] = Field(default_factory=list)

    @property
    def ordered(self) -> list[ProposedAction]:
        """By sequence, then by declaration order — a stable sort, so equal sequences keep the
        order the policy produced rather than an arbitrary one."""
        return sorted(self.actions, key=lambda a: a.sequence)

    def check_feasible(
        self, portfolio: Portfolio | None, liquid_assets: float
    ) -> list[Infeasibility]:
        """Every reason this plan cannot be carried out, or an empty list.

        Deliberately returns all the problems rather than raising on the first: a user is better
        served by "these three steps do not work" than by being told about one, fixing it, and
        being told about the next.
        """
        problems: list[Infeasibility] = []

        seen_ids: set[str] = set()
        for action in self.actions:
            if action.action_id in seen_ids:
                problems.append(
                    Infeasibility(
                        reason=InfeasibleReason.duplicate_action_id,
                        action_id=action.action_id,
                        message=f"action_id {action.action_id!r} appears more than once",
                    )
                )
            seen_ids.add(action.action_id)

        problems.extend(self._check_disposals(portfolio))
        problems.extend(self._check_cash(portfolio, liquid_assets))
        problems.extend(self._check_rebalance_weights())
        return problems

    def _check_disposals(self, portfolio: Portfolio | None) -> list[Infeasibility]:
        """You cannot sell what you do not hold, and you cannot sell it twice."""
        problems: list[Infeasibility] = []
        sells = [a for a in self.ordered if a.kind is ActionKind.trim_position]
        if not sells:
            return problems

        remaining_value: dict[str, float] = {}
        remaining_shares: dict[str, float | None] = {}
        if portfolio:
            for h in portfolio.holdings:
                remaining_value[h.symbol] = remaining_value.get(h.symbol, 0.0) + h.market_value
                if h.quantity is not None:
                    prior = remaining_shares.get(h.symbol)
                    remaining_shares[h.symbol] = (prior or 0.0) + h.quantity
                else:
                    # One lot without a share count makes the symbol's total unknowable.
                    remaining_shares[h.symbol] = None

        for action in sells:
            symbol = action.symbol
            if symbol is None or portfolio is None or symbol not in remaining_value:
                problems.append(
                    Infeasibility(
                        reason=InfeasibleReason.no_such_holding,
                        action_id=action.action_id,
                        message=f"cannot trim {symbol or '(no symbol)'}: it is not held",
                    )
                )
                continue

            if action.shares is not None:
                available = remaining_shares.get(symbol)
                if available is None:
                    # No share count on the holding: fall through to the value check below.
                    pass
                elif action.shares > available + 1e-9:
                    problems.append(
                        Infeasibility(
                            reason=(
                                InfeasibleReason.double_disposal
                                if available <= 1e-9
                                else InfeasibleReason.oversells_holding
                            ),
                            action_id=action.action_id,
                            message=(
                                f"cannot sell {action.shares:g} shares of {symbol}: "
                                f"{available:g} remain at this point in the sequence"
                            ),
                        )
                    )
                    continue
                else:
                    remaining_shares[symbol] = available - action.shares

            value = action._dollar_magnitude(portfolio)
            if value is None:
                continue
            available_value = remaining_value.get(symbol, 0.0)
            if value > available_value + MONEY_EPSILON:
                problems.append(
                    Infeasibility(
                        reason=(
                            InfeasibleReason.double_disposal
                            if available_value <= MONEY_EPSILON
                            else InfeasibleReason.oversells_holding
                        ),
                        action_id=action.action_id,
                        message=(
                            f"cannot take ${value:,.2f} out of {symbol}: "
                            f"${available_value:,.2f} remains at this point in the sequence"
                        ),
                    )
                )
                continue
            remaining_value[symbol] = available_value - value

        return problems

    def _check_cash(self, portfolio: Portfolio | None, liquid_assets: float) -> list[Infeasibility]:
        """Spending is checked against cash on hand *at that point in the sequence*.

        Order is the whole point: trimming a position before buying with the proceeds is fine,
        and the same two steps in the opposite order are not.
        """
        problems: list[Infeasibility] = []
        available = liquid_assets
        empty = Portfolio()

        for action in self.ordered:
            effect = action.cash_effect(portfolio or empty)
            if effect < 0 and -effect > available + MONEY_EPSILON:
                problems.append(
                    Infeasibility(
                        reason=InfeasibleReason.insufficient_cash,
                        action_id=action.action_id,
                        message=(
                            f"{action.kind.value} needs ${-effect:,.2f} but only "
                            f"${available:,.2f} is available at step {action.sequence}"
                        ),
                    )
                )
                # Keep going with what there was, so one overspend does not cascade into a
                # complaint about every later step.
                available = 0.0
                continue
            available += effect

        return problems

    def _check_rebalance_weights(self) -> list[Infeasibility]:
        """Target weights in one rebalance must describe a whole portfolio, not part of one."""
        targets = [
            a
            for a in self.actions
            if a.kind is ActionKind.rebalance_to_target and a.target_weight is not None
        ]
        if not targets:
            return []

        total = sum(a.target_weight or 0.0 for a in targets)
        if abs(total - 1.0) <= WEIGHT_EPSILON:
            return []
        return [
            Infeasibility(
                reason=InfeasibleReason.weights_do_not_sum,
                message=(
                    f"rebalance target weights sum to {total:.3f}, not 1.0 — "
                    "the plan describes a portfolio that does not add up"
                ),
            )
        ]


def _value_of(portfolio: Portfolio, symbol: str) -> float:
    """Market value of a symbol across every account holding it.

    Aggregated by symbol, matching analytics/portfolio_analytics.py. A ticker held in both a
    taxable account and a Roth is one position from the point of view of an action.
    """
    return sum(h.market_value for h in portfolio.holdings if h.symbol == symbol)


def _price_per_share(portfolio: Portfolio, symbol: str) -> float | None:
    """Implied from value and quantity, since a Holding carries no price of its own.

    None when any lot of the symbol lacks a share count — a partial quantity would produce a
    confidently wrong price, which is worse than declining to answer.
    """
    lots = [h for h in portfolio.holdings if h.symbol == symbol]
    if not lots or any(h.quantity is None for h in lots):
        return None
    shares = sum(h.quantity or 0.0 for h in lots)
    if shares <= 0:
        return None
    return sum(h.market_value for h in lots) / shares
