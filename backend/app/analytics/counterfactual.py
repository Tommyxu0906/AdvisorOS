"""Apply a plan to a copy of someone's finances and measure what actually changed.

This is what makes a recommendation falsifiable. "Trimming NVDA reduces your concentration" is
an assertion until the trim is applied to a copy of the portfolio, the analytics are recomputed,
and the number is compared. Anything that fails that comparison is a bug in a policy, not an
opinion to weigh.

It replaces the previous check, which was five regular expressions looking for phrases like
"invest all your cash" in the report text (`analytics/guardrails.py`). That check could only
find the wordings someone had thought of in advance, and any paraphrase walked straight past it.
Arithmetic does not care how the recommendation was phrased.

Nothing here decides whether a plan is *good*. It reports what the plan does — which guardrails
it resolves, which it triggers, and whether each action moved the number it was aimed at. The
judgment of whether that trade is worth making stays with the committee, and with the user.

The applier is deliberately literal and refuses to guess. An action it cannot model is listed in
`unapplied` rather than approximated, because a counterfactual that quietly invents the effect
of an instruction is worse than one that admits it does not know.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from app.analytics.guardrails import evaluate_guardrails
from app.analytics.portfolio_analytics import PortfolioAnalytics, analyze_portfolio
from app.analytics.profile_analytics import ProfileAnalytics, analyze_profile
from app.domain.action import ActionKind, ActionSet, Infeasibility, ProposedAction
from app.domain.portfolio import AssetClass, Holding, Portfolio
from app.domain.profile import AccountType, Asset, FinancialProfile
from app.domain.report import GuardrailSeverity

# Where proceeds land and purchases are funded from when the profile has no cash asset yet.
SYNTHETIC_CASH_NAME = "cash (from proposed actions)"


class MetricChange(BaseModel):
    """One number, before and after. `higher_is_better` is None where neither direction is
    inherently good — net worth after a tax-generating sale, for instance."""

    model_config = ConfigDict(extra="forbid")

    label: str
    before: float
    after: float
    higher_is_better: bool | None = None

    @property
    def delta(self) -> float:
        return self.after - self.before

    @property
    def improved(self) -> bool | None:
        if self.higher_is_better is None or abs(self.delta) < 1e-9:
            return None
        return self.delta > 0 if self.higher_is_better else self.delta < 0


class Counterfactual(BaseModel):
    """What a plan does to the numbers, and whether it holds up."""

    model_config = ConfigDict(extra="forbid")

    feasible: bool
    infeasibilities: list[Infeasibility] = Field(default_factory=list)
    unapplied: list[str] = Field(
        default_factory=list,
        description="action_ids the applier could not model, rather than approximate",
    )

    changes: list[MetricChange] = Field(default_factory=list)
    estimated_tax_usd: float | None = Field(
        default=None,
        description=(
            "Total estimated tax the plan realizes, summed from the actions. None when no "
            "action could estimate one — unknown, not zero. Deliberately reported rather than "
            "subtracted: the estimate rests on an assumed blended rate, and folding it into "
            "net worth would give a guess the authority of a computed balance."
        ),
    )
    resolved_guardrails: list[str] = Field(default_factory=list)
    introduced_guardrails: list[str] = Field(default_factory=list)
    ineffective_actions: list[str] = Field(
        default_factory=list,
        description="action_ids that did not move the metric they were aimed at",
    )

    @property
    def introduces_blocking_guardrail(self) -> bool:
        return bool(self.introduced_guardrails)

    @property
    def holds_up(self) -> bool:
        """The bar a plan must clear before it is worth showing as a recommendation."""
        return (
            self.feasible
            and not self.introduced_guardrails
            and not self.ineffective_actions
            and not self.unapplied
        )

    def summary_lines(self) -> list[str]:
        lines = [
            f"{c.label}: {c.before:,.4g} -> {c.after:,.4g}"
            for c in self.changes
            if abs(c.delta) > 1e-9
        ]
        if self.estimated_tax_usd:
            lines.append(f"Estimated tax to act: ${self.estimated_tax_usd:,.0f}")
        if self.resolved_guardrails:
            lines.append(f"Resolves: {', '.join(self.resolved_guardrails)}")
        if self.introduced_guardrails:
            lines.append(f"TRIGGERS: {', '.join(self.introduced_guardrails)}")
        return lines


def evaluate(
    profile: FinancialProfile,
    portfolio: Portfolio | None,
    action_set: ActionSet,
) -> Counterfactual:
    """Score a plan against the situation it was written for."""
    before_profile_analytics = analyze_profile(profile, portfolio)
    before_portfolio_analytics = analyze_portfolio(portfolio) if portfolio else None
    before_rails = evaluate_guardrails(
        profile, before_profile_analytics, portfolio, before_portfolio_analytics
    )

    infeasibilities = action_set.check_feasible(portfolio, before_profile_analytics.liquid_assets)

    after_profile, after_portfolio, unapplied = apply(profile, portfolio, action_set)
    after_profile_analytics = analyze_profile(after_profile, after_portfolio)
    after_portfolio_analytics = analyze_portfolio(after_portfolio) if after_portfolio else None
    after_rails = evaluate_guardrails(
        after_profile, after_profile_analytics, after_portfolio, after_portfolio_analytics
    )

    before_codes = {g.code for g in before_rails}
    after_codes = {g.code for g in after_rails}
    # Only blocking rails count as "introduced". A plan that trades a blocking problem for a
    # caution is usually the point of the plan, not a failure of it.
    newly_blocking = {
        g.code for g in after_rails if g.severity is GuardrailSeverity.blocking
    } - before_codes

    return Counterfactual(
        feasible=not infeasibilities,
        infeasibilities=infeasibilities,
        unapplied=unapplied,
        changes=_changes(
            before_profile_analytics,
            after_profile_analytics,
            before_portfolio_analytics,
            after_portfolio_analytics,
        ),
        estimated_tax_usd=_total_tax(action_set),
        resolved_guardrails=sorted(before_codes - after_codes),
        introduced_guardrails=sorted(newly_blocking),
        ineffective_actions=_ineffective(
            action_set,
            before_profile_analytics,
            after_profile_analytics,
            before_portfolio_analytics,
            after_portfolio_analytics,
            unapplied,
        ),
    )


def apply(
    profile: FinancialProfile,
    portfolio: Portfolio | None,
    action_set: ActionSet,
) -> tuple[FinancialProfile, Portfolio | None, list[str]]:
    """Carry out a plan against deep copies. The originals are never touched.

    Returns the resulting profile and portfolio plus the ids of actions that could not be
    modelled — see the module docstring on why those are reported rather than approximated.
    """
    new_profile = profile.model_copy(deep=True)
    new_portfolio = portfolio.model_copy(deep=True) if portfolio else None
    unapplied: list[str] = []

    for action in action_set.ordered:
        if not _apply_one(action, new_profile, new_portfolio):
            unapplied.append(action.action_id)

    return new_profile, new_portfolio, unapplied


def _apply_one(
    action: ProposedAction, profile: FinancialProfile, portfolio: Portfolio | None
) -> bool:
    """True when the action was modelled; False when it was not."""
    if action.kind is ActionKind.hold:
        return True

    if action.kind is ActionKind.trim_position:
        if portfolio is None or action.symbol is None:
            return False
        sold = _reduce_holding(portfolio, action)
        if sold is None:
            return False
        _add_cash(profile, sold)
        return True

    if action.kind is ActionKind.add_position:
        amount = action._dollar_magnitude(portfolio or Portfolio())
        if amount is None or portfolio is None or action.symbol is None:
            return False
        _increase_holding(portfolio, action.symbol, action.asset_class, amount)
        _add_cash(profile, -amount)
        return True

    if action.kind is ActionKind.pay_down_debt:
        if action.amount_usd is None:
            return False
        paid = _reduce_debt(profile, action.amount_usd, action.symbol)
        _add_cash(profile, -paid)
        return True

    if action.kind is ActionKind.build_emergency_fund:
        if action.amount_usd is None:
            return False
        # Money already counted as liquid by a preceding sale; this names where it settles.
        _add_cash(profile, 0.0)
        return True

    if action.kind is ActionKind.redirect_cashflow:
        if action.amount_usd is None:
            return False
        # Interpreted as a monthly figure moved out of discretionary spending.
        monthly = min(action.amount_usd, profile.expenses.monthly_discretionary)
        profile.expenses.monthly_discretionary -= monthly
        return True

    if action.kind is ActionKind.rebalance_to_target:
        return _rebalance(portfolio, action)

    return False


def _reduce_holding(portfolio: Portfolio, action: ProposedAction) -> float | None:
    """Sell down a symbol across its lots, largest first. Returns the proceeds."""
    lots = [h for h in portfolio.holdings if h.symbol == action.symbol]
    if not lots:
        return None
    amount = action._dollar_magnitude(portfolio)
    if amount is None:
        return None

    held = sum(h.market_value for h in lots)
    amount = min(amount, held)
    remaining = amount

    for lot in sorted(lots, key=lambda h: h.market_value, reverse=True):
        if remaining <= 0:
            break
        take = min(lot.market_value, remaining)
        fraction = take / lot.market_value if lot.market_value > 0 else 0.0
        if lot.quantity is not None:
            lot.quantity = max(0.0, lot.quantity * (1 - fraction))
        if lot.cost_basis is not None:
            lot.cost_basis = max(0.0, lot.cost_basis * (1 - fraction))
        lot.market_value -= take
        remaining -= take

    portfolio.holdings = [h for h in portfolio.holdings if h.market_value > 0]
    return amount


def _increase_holding(
    portfolio: Portfolio, symbol: str, asset_class: AssetClass | None, amount: float
) -> None:
    for lot in portfolio.holdings:
        if lot.symbol == symbol:
            # Share count would need a price; leaving it alone keeps market_value authoritative
            # and avoids inventing a quantity the counterfactual cannot verify.
            lot.market_value += amount
            return
    portfolio.holdings.append(
        Holding(
            symbol=symbol,
            asset_class=asset_class or AssetClass.other,
            market_value=amount,
        )
    )


def _reduce_debt(profile: FinancialProfile, amount: float, name: str | None) -> float:
    """Pay against the named debt, or the highest-APR one. Returns what was actually paid."""
    candidates = [d for d in profile.debts if name is None or d.name == name]
    if not candidates:
        return 0.0
    remaining = amount
    for debt in sorted(candidates, key=lambda d: d.apr, reverse=True):
        if remaining <= 0:
            break
        paid = min(debt.balance, remaining)
        debt.balance -= paid
        remaining -= paid
        if debt.balance <= 0:
            debt.minimum_monthly_payment = 0.0
    profile.debts = [d for d in profile.debts if d.balance > 0]
    return amount - remaining


def _add_cash(profile: FinancialProfile, amount: float) -> None:
    """Move cash in or out of the first liquid account, creating one if there is none."""
    if abs(amount) < 1e-9:
        return
    for asset in profile.assets:
        if asset.is_liquid:
            asset.value = max(0.0, asset.value + amount)
            return
    if amount > 0:
        profile.assets.append(
            Asset(
                name=SYNTHETIC_CASH_NAME,
                value=amount,
                account_type=AccountType.cash,
                is_liquid=True,
            )
        )


def _rebalance(portfolio: Portfolio | None, action: ProposedAction) -> bool:
    """Scale an asset class to its target weight, proportionally across its holdings.

    Returns False when the target class holds nothing: reaching it would mean choosing an
    instrument, and picking a ticker on the user's behalf is not this module's job.
    """
    if portfolio is None or action.asset_class is None or action.target_weight is None:
        return False
    total = portfolio.total_value
    if total <= 0:
        return False

    in_class = [h for h in portfolio.holdings if h.asset_class is action.asset_class]
    if not in_class:
        return False

    current = sum(h.market_value for h in in_class)
    target = action.target_weight * total
    if current <= 0:
        return False

    scale = target / current
    for lot in in_class:
        if lot.quantity is not None:
            lot.quantity *= scale
        if lot.cost_basis is not None:
            lot.cost_basis *= scale
        lot.market_value *= scale
    return True


def _changes(
    before: ProfileAnalytics,
    after: ProfileAnalytics,
    before_pa: PortfolioAnalytics | None,
    after_pa: PortfolioAnalytics | None,
) -> list[MetricChange]:
    changes = [
        MetricChange(
            label="emergency fund (months)",
            before=before.emergency_fund_months,
            after=after.emergency_fund_months,
            higher_is_better=True,
        ),
        MetricChange(
            label="savings rate",
            before=before.savings_rate,
            after=after.savings_rate,
            higher_is_better=True,
        ),
        MetricChange(
            label="high-APR debt",
            before=before.high_apr_debt_balance,
            after=after.high_apr_debt_balance,
            higher_is_better=False,
        ),
        MetricChange(
            label="annual interest cost",
            before=before.annual_interest_cost,
            after=after.annual_interest_cost,
            higher_is_better=False,
        ),
        # Both ledgers together. `ProfileAnalytics.net_worth` counts only `profile.assets`, and
        # the portfolio is a separate list — so a sale moves value from an uncounted ledger into
        # a counted one and reads as though selling made the holder richer. Summing the two is
        # the number a person would recognize as theirs, and it correctly stays flat when a
        # position is merely converted to cash.
        #
        # No preferred direction: a sale that realizes tax lowers it on purpose, and scoring
        # that as a regression would penalize the right decision.
        MetricChange(
            label="net worth (incl. portfolio)",
            before=before.net_worth + (before_pa.total_value if before_pa else 0.0),
            after=after.net_worth + (after_pa.total_value if after_pa else 0.0),
        ),
    ]
    if before_pa and after_pa:
        changes.extend(
            [
                MetricChange(
                    label="largest position weight",
                    before=before_pa.largest_weight,
                    after=after_pa.largest_weight,
                    higher_is_better=False,
                ),
                MetricChange(
                    label="HHI",
                    before=before_pa.hhi,
                    after=after_pa.hhi,
                    higher_is_better=False,
                ),
                MetricChange(
                    label="effective positions",
                    before=before_pa.effective_holdings,
                    after=after_pa.effective_holdings,
                    higher_is_better=True,
                ),
            ]
        )
    return changes


def _total_tax(action_set: ActionSet) -> float | None:
    """Summed estimates, or None when nothing could produce one."""
    estimates = [
        a.estimated_tax_impact_usd
        for a in action_set.actions
        if a.estimated_tax_impact_usd is not None
    ]
    return round(sum(estimates), 2) if estimates else None


def _ineffective(
    action_set: ActionSet,
    before: ProfileAnalytics,
    after: ProfileAnalytics,
    before_pa: PortfolioAnalytics | None,
    after_pa: PortfolioAnalytics | None,
    unapplied: list[str],
) -> list[str]:
    """Actions that ran but did not move the number they were aimed at.

    A policy producing one of these has a bug: it recommended something whose stated purpose it
    did not achieve. Unapplied actions are excluded — they are already reported separately, and
    blaming them twice would obscure which problem is which.
    """
    skip = set(unapplied)
    failures: list[str] = []

    for action in action_set.actions:
        if action.action_id in skip or action.kind is ActionKind.hold:
            continue

        moved: bool | None = None
        if action.kind is ActionKind.trim_position and before_pa and after_pa and action.symbol:
            moved = (
                after_pa.weights.get(action.symbol, 0.0)
                < before_pa.weights.get(action.symbol, 0.0) - 1e-9
            )
        elif action.kind is ActionKind.add_position and before_pa and after_pa and action.symbol:
            moved = (
                after_pa.weights.get(action.symbol, 0.0)
                > before_pa.weights.get(action.symbol, 0.0) + 1e-9
            )
        elif action.kind is ActionKind.pay_down_debt:
            moved = after.total_debt < before.total_debt - 1e-9
        elif action.kind is ActionKind.build_emergency_fund:
            moved = after.emergency_fund_months > before.emergency_fund_months - 1e-9
        elif action.kind is ActionKind.redirect_cashflow:
            moved = after.savings_rate > before.savings_rate - 1e-9

        if moved is False:
            failures.append(action.action_id)

    return failures
