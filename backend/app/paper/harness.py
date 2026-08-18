"""The forward loop: one portfolio, one provider, one checked plan, optionally one paper fill.

    snapshot -> provider -> InvestorView -> compute_scenario -> merge -> feasibility
             -> counterfactual -> orders -> MockPaperBroker -> snapshot

Two things about the shape are worth stating, because both were choices.

**The stances do not become orders directly.** They become `ProposedAction`s and go through the
same feasibility and counterfactual checks as anything the policy engine produced. A provider
saying "increase AAPL" against a portfolio with no cash is a plan that fails arithmetic, and the
whole point of the decision engine is that arithmetic gets the last word rather than the
opinion. This is where a language model, when one is eventually plugged in, stops being able to
recommend something impossible.

**Concentration trims and directional stances are merged, not concatenated.** Both can name the
same symbol — the engine wants to trim NVDA to the cap while the investor wants to exit it — and
emitting both would try to sell the same shares twice. `ActionSet.check_feasible` would catch
it, but as an infeasibility rather than as what it is: two views of one position that need
resolving before anyone sees them. The engine's trim wins on a shared symbol, because it is the
one carrying a threshold with provenance and a sensitivity sweep behind it.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from app.analytics.counterfactual import Counterfactual
from app.analytics.counterfactual import apply as counterfactual_apply
from app.analytics.counterfactual import evaluate as evaluate_counterfactual
from app.analytics.guardrails import evaluate_guardrails
from app.analytics.portfolio_analytics import analyze_portfolio
from app.analytics.profile_analytics import analyze_profile
from app.distillation.finance_nuwa.prediction import BehavioralAction
from app.domain.action import ActionKind, ActionSet, Infeasibility, ProposedAction
from app.domain.portfolio import AssetClass, Holding, Portfolio
from app.domain.profile import AccountType, FinancialProfile
from app.paper.broker import (
    HarnessMode,
    OrderSide,
    PaperAccount,
    PaperFill,
    PaperOrder,
    PaperPosition,
    RejectedOrder,
)
from app.paper.mock_broker import MockPaperBroker
from app.paper.provider import InvestorDecisionProvider, InvestorStance, InvestorView
from app.policy.engine import PortfolioScenario, compute_scenario


class PaperRunResult(BaseModel):
    """Everything one round produced, including the parts that did not survive."""

    model_config = ConfigDict(extra="forbid")

    mode: HarnessMode
    provider_id: str
    provider_display_name: str

    view: InvestorView | None = None
    scenario: PortfolioScenario | None = None

    action_set: ActionSet = Field(default_factory=ActionSet)
    infeasibilities: list[Infeasibility] = Field(default_factory=list)
    counterfactual: Counterfactual | None = None

    submitted: list[PaperOrder] = Field(default_factory=list)
    fills: list[PaperFill] = Field(default_factory=list)
    rejections: list[RejectedOrder] = Field(default_factory=list)

    account_before: PaperAccount | None = None
    account_after: PaperAccount | None = None

    notes: list[str] = Field(default_factory=list)

    @property
    def executed(self) -> bool:
        return bool(self.fills)

    @property
    def is_feasible(self) -> bool:
        return not self.infeasibilities

    def render(self) -> str:
        lines = [
            f"mode           {self.mode.value}",
            f"provider       {self.provider_display_name} ({self.provider_id})",
        ]
        if self.view is not None:
            lines.append(
                f"coverage       {self.view.coverage:.0%} "
                f"({len(self.view.abstentions)} abstained of {len(self.view.stances)})"
            )
        if self.scenario is not None:
            lines.append(f"headline       {self.scenario.headline}")
        lines.append(f"actions        {len(self.action_set.actions)}")
        if self.infeasibilities:
            lines.append(f"INFEASIBLE     {len(self.infeasibilities)}")
            lines.extend(f"               - {p.message}" for p in self.infeasibilities)
        if self.counterfactual is not None:
            lines.append(f"holds up       {self.counterfactual.holds_up}")
        if self.submitted:
            lines.append(f"submitted      {len(self.submitted)}")
            lines.append(f"filled         {len(self.fills)}")
            if self.rejections:
                lines.append(f"REJECTED       {len(self.rejections)}")
                lines.extend(f"               - {r.reason}" for r in self.rejections)
        if self.account_before and self.account_after:
            lines.append(
                f"equity         ${self.account_before.equity:,.2f} -> "
                f"${self.account_after.equity:,.2f}"
            )
        lines.extend(f"note           {n}" for n in self.notes)
        return "\n".join(lines)


def run_once(
    profile: FinancialProfile,
    portfolio: Portfolio,
    provider: InvestorDecisionProvider,
    broker: MockPaperBroker | None = None,
    *,
    mode: HarnessMode = HarnessMode.recommend_only,
) -> PaperRunResult:
    """One round of the loop. Pure except for the broker, which it mutates only in execute mode."""
    result = PaperRunResult(
        mode=mode,
        provider_id=provider.provider_id,
        provider_display_name=provider.display_name,
        account_before=broker.snapshot() if broker else None,
    )

    if not mode.may_decide:
        result.notes.append("observe-only: no decision was requested and nothing was proposed")
        result.account_after = result.account_before
        return result

    analytics = analyze_profile(profile)
    portfolio_analytics = analyze_portfolio(portfolio) if portfolio.holdings else None
    guardrails = evaluate_guardrails(profile, analytics)

    view = provider.decide(profile, portfolio)
    result.view = view

    scenario = compute_scenario(
        profile,
        analytics,
        portfolio,
        portfolio_analytics,
        guardrails,
        policy_profile=view.policy,
        advisor_id=view.provider_id,
        display_name=view.display_name,
    )
    result.scenario = scenario

    merged, merge_notes = _merge(scenario.action_set, view, profile, portfolio)
    result.action_set = merged
    result.notes.extend(merge_notes)
    result.infeasibilities = merged.check_feasible(portfolio, analytics.liquid_assets)
    result.counterfactual = evaluate_counterfactual(profile, portfolio, merged)

    if not mode.may_execute:
        return result

    if broker is None:
        result.notes.append("paper_execute requested but no broker was supplied; nothing submitted")
        return result

    if result.infeasibilities:
        result.notes.append(
            "not submitted: the plan failed its own feasibility check, and a simulator that "
            "accepted it would be hiding the bug rather than finding it"
        )
        result.account_after = broker.snapshot()
        return result

    orders = to_orders(merged, portfolio)
    result.submitted = orders
    fills, rejections = broker.submit(orders)
    result.fills = fills
    result.rejections = rejections
    result.account_after = broker.snapshot()
    return result


def _merge(
    engine_actions: ActionSet,
    view: InvestorView,
    profile: FinancialProfile,
    portfolio: Portfolio,
) -> tuple[ActionSet, list[str]]:
    """Engine trims first; stances are then sized against the book the trims would leave behind.

    The ordering matters and the naive version is wrong. Sizing a stance against the *pre-trim*
    portfolio lets a buy quietly undo the deconcentration the trims just achieved: in a
    three-position book the engine trims everything to the 1/n floor of 33%, and a stance-driven
    purchase sized on the old total then takes one name back to 43%. Every individual action
    still "worked" — the trims reduced their weights, the buy increased its position — so the
    counterfactual passes each one and the plan as a whole is incoherent.

    Two rules fall out, and both are about the plan rather than about any single action:

    1. Stances are evaluated against the post-trim portfolio, which is the state the investor
       would actually be looking at.
    2. A buy may not take its symbol above the largest weight the trims left standing. That
       ceiling is the engine's own answer to "how concentrated may anything be here", including
       when the 1/n floor rather than the stated cap is what set it.

    Refusals are returned rather than dropped silently, because "your policy wanted to add to
    VTI and the concentration limit would not allow it" is a finding, not noise.
    """
    actions = list(engine_actions.actions)
    claimed = {a.symbol.upper() for a in actions if a.symbol}
    notes: list[str] = []

    # The book as the trims would leave it. Falls back to the original when nothing was trimmed.
    _, trimmed, _ = counterfactual_apply(profile, portfolio, engine_actions)
    working = trimmed or portfolio

    ceiling = _largest_weight(working)

    for stance in view.stances:
        symbol = stance.symbol.upper()
        if symbol in claimed or not stance.is_actionable:
            continue

        action = _stance_to_action(stance, view, working)
        if action is None:
            continue

        if action.kind is ActionKind.add_position and action.target_weight is not None:
            if ceiling is not None and action.target_weight > ceiling + 1e-9:
                notes.append(
                    f"{symbol}: {view.display_name} would add to this position, but taking it to "
                    f"{action.target_weight:.1%} would exceed the {ceiling:.1%} ceiling the "
                    "concentration trims left in place. Not proposed."
                )
                continue

        actions.append(action)
        claimed.add(symbol)

    return ActionSet(actions=actions), notes


def _largest_weight(portfolio: Portfolio) -> float | None:
    total = portfolio.total_value
    if total <= 0:
        return None
    values: dict[str, float] = {}
    for holding in portfolio.holdings:
        symbol = holding.symbol.strip().upper()
        if symbol:
            values[symbol] = values.get(symbol, 0.0) + holding.market_value
    return max(values.values()) / total if values else None


def _stance_to_action(
    stance: InvestorStance, view: InvestorView, portfolio: Portfolio
) -> ProposedAction | None:
    """One directional stance, sized against what is actually held.

    Sized by `target_weight` rather than by shares or dollars: the stance is a statement about
    how much of the book this position should be, and converting it to a share count here would
    fix a quantity against a price that the broker will re-read anyway.
    """
    symbol = stance.symbol.upper()
    total = portfolio.total_value
    if total <= 0:
        return None

    held = sum(h.market_value for h in portfolio.holdings if h.symbol.strip().upper() == symbol)
    weight = held / total

    rationale = f"{view.display_name}: {stance.note}" if stance.note else view.display_name

    if stance.action is BehavioralAction.exit:
        if held <= 0:
            return None
        return ProposedAction(
            action_id=f"stance-exit-{symbol}",
            kind=ActionKind.trim_position,
            symbol=symbol,
            target_weight=0.0,
            sequence=20,
            proposed_by=view.provider_id,
            rationale=rationale,
        )

    if stance.action is BehavioralAction.reduce:
        if held <= 0:
            return None
        # Halve the position. A stance says direction, not size; the engine's concentration
        # policy is the thing that computes a size from a threshold, and inventing a precise
        # target here would dress a directional opinion up as a calculation.
        return ProposedAction(
            action_id=f"stance-reduce-{symbol}",
            kind=ActionKind.trim_position,
            symbol=symbol,
            target_weight=round(weight / 2, 6),
            sequence=21,
            proposed_by=view.provider_id,
            rationale=rationale,
        )

    if stance.action is BehavioralAction.increase:
        if held <= 0:
            # Opening a new position is a different decision from scaling one, and this harness
            # covers incumbent positions only — the same boundary FinanceNuwa's task draws.
            return None
        return ProposedAction(
            action_id=f"stance-increase-{symbol}",
            kind=ActionKind.add_position,
            symbol=symbol,
            target_weight=round(min(weight * 1.5, 1.0), 6),
            sequence=30,
            proposed_by=view.provider_id,
            rationale=rationale,
        )

    return None


def to_orders(action_set: ActionSet, portfolio: Portfolio) -> list[PaperOrder]:
    """Turn a checked plan into share-count orders, in sequence.

    Anything whose size cannot be resolved to shares is dropped rather than guessed at, and the
    drop is visible because the order count will not match the action count.
    """
    orders: list[PaperOrder] = []
    for action in action_set.ordered:
        if action.symbol is None or action.kind is ActionKind.hold:
            continue

        price = _price_of(portfolio, action.symbol)
        if price is None or price <= 0:
            continue

        magnitude = abs(action.cash_effect(portfolio))
        if magnitude <= 0:
            continue

        shares = round(magnitude / price, 6)
        if shares <= 0:
            continue

        if action.kind is ActionKind.trim_position:
            side = OrderSide.sell
        elif action.kind is ActionKind.add_position:
            side = OrderSide.buy
        else:
            # Debt paydown and cash-flow actions are real, and they are not brokerage orders.
            continue

        orders.append(
            PaperOrder(
                client_order_id=f"{action.action_id}",
                symbol=action.symbol.upper(),
                side=side,
                quantity=shares,
                action_id=action.action_id,
            )
        )
    return orders


def _price_of(portfolio: Portfolio, symbol: str) -> float | None:
    upper = symbol.strip().upper()
    for holding in portfolio.holdings:
        if holding.symbol.strip().upper() != upper:
            continue
        if holding.quantity and holding.quantity > 0:
            return holding.market_value / holding.quantity
    return None


def portfolio_from_account(account: PaperAccount, template: Portfolio) -> Portfolio:
    """Rebuild the decision's input from what the simulator actually holds.

    This is what closes the loop. Without it a second round decides against the book from before
    the first round executed, re-proposes trims that have already happened, and the broker
    rejects every one of them — the rejections are correct and the run is meaningless.

    Reference data that execution does not change — the display name, the asset class, the price
    series — is carried across from `template`. A symbol the template never knew about keeps the
    simulator's own average price and lands in `other`, because guessing an asset class from a
    ticker is exactly the kind of invention this codebase avoids elsewhere.
    """
    by_symbol = {h.symbol.strip().upper(): h for h in template.holdings}

    holdings: list[Holding] = []
    for position in account.positions:
        source = by_symbol.get(position.symbol.upper())
        holdings.append(
            Holding(
                symbol=position.symbol,
                name=source.name if source else position.symbol,
                asset_class=source.asset_class if source else AssetClass.other,
                quantity=position.quantity,
                market_value=position.market_value,
                # Cost basis follows the simulator's blended average, which is the only basis
                # that survives a partial sale and a repurchase.
                cost_basis=round(position.quantity * position.average_price, 2),
                account_type=source.account_type if source else AccountType.taxable,
            )
        )

    return Portfolio(
        holdings=holdings,
        price_series=[
            s
            for s in template.price_series
            if s.symbol.upper() in {p.symbol.upper() for p in account.positions}
        ],
        currency=template.currency,
    )


def broker_from_portfolio(portfolio: Portfolio, cash: float) -> MockPaperBroker:
    """Seed a simulator so its opening state matches the portfolio the decision was made on.

    Without this the harness would propose against one book and execute against another, and
    every rejection would be an artefact of the mismatch rather than a finding.
    """
    positions: dict[str, PaperPosition] = {}
    prices: dict[str, float] = {}
    for holding in portfolio.holdings:
        symbol = holding.symbol.strip().upper()
        if not symbol or not holding.quantity or holding.quantity <= 0:
            continue
        price = holding.market_value / holding.quantity
        prices[symbol] = price
        existing = positions.get(symbol)
        if existing is None:
            positions[symbol] = PaperPosition(
                symbol=symbol, quantity=holding.quantity, average_price=price
            )
        else:
            total = existing.quantity + holding.quantity
            positions[symbol] = PaperPosition(
                symbol=symbol,
                quantity=total,
                average_price=(existing.market_value + holding.market_value) / total,
            )

    return MockPaperBroker(cash=cash, positions=positions, prices=prices)
