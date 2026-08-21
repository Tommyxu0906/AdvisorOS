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

from pydantic import BaseModel, ConfigDict, Field, computed_field

from app.analytics.guardrails import evaluate_guardrails
from app.analytics.portfolio_analytics import PortfolioAnalytics, analyze_portfolio
from app.analytics.profile_analytics import ProfileAnalytics, analyze_profile
from app.domain.action import ActionKind, ActionSet, Infeasibility, ProposedAction, TaxRange
from app.domain.portfolio import AssetClass, Holding, Portfolio
from app.domain.profile import FinancialProfile
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

    @computed_field(
        description="Did this move the right way? None where no direction is inherently good."
    )
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
    estimated_tax: TaxRange | None = Field(
        default=None,
        description=(
            "Total estimated tax the plan realizes, summed from the actions. None when no "
            "action could estimate one — unknown, not zero. Deliberately reported rather than "
            "subtracted: the estimate rests on assumed rates, and folding it into net worth "
            "would give a guess the authority of a computed balance."
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

    @computed_field(
        description="The bar a plan must clear before it is worth showing as a recommendation"
    )
    @property
    def holds_up(self) -> bool:
        """The bar a plan must clear before it is worth showing as a recommendation.

        Serialized rather than left as a bare property: the client must not re-derive this from
        the component fields, or the definition of "good enough to show" ends up living in two
        languages and drifting.
        """
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
        if self.estimated_tax and self.estimated_tax.high_usd > 0:
            lines.append(f"Estimated tax to act: {self.estimated_tax.render()}")
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

    infeasibilities = action_set.check_feasible(portfolio, before_profile_analytics.investable_cash)

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
        estimated_tax=_total_tax(action_set),
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

    if action.kind is ActionKind.rebalance_to_target:
        sold = _rebalance(portfolio, action)
        if sold is None:
            return False
        # The proceeds have to land somewhere. Without this the value simply vanished, and the
        # before/after read as though de-risking destroyed a third of the capital.
        _add_cash(profile, sold)
        return True

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


def _add_cash(profile: FinancialProfile, amount: float) -> None:
    """Move deployable cash. One number now, where it used to be a list of household assets."""
    profile.investable_cash = max(0.0, round(profile.investable_cash + amount, 2))


# Everything the house counts as a growth asset. Mirrors profile_analytics, which is the module
# that decides whether the growth share breaches the ceiling in the first place.
_GROWTH_CLASSES = {"us_equity", "intl_developed_equity", "emerging_equity", "crypto", "reit"}


def _rebalance(portfolio: Portfolio | None, action: ProposedAction) -> float | None:
    """Scale a group of holdings to a target weight, proportionally within the group.

    With `asset_class` set, the group is that class. With it left unset, the group is every
    growth asset — which is how the house expresses "money needed soon should not be this
    exposed" without naming an instrument to sell.

    Returns False when the group holds nothing: reaching the target would mean choosing what to
    buy, and picking a ticker on the user's behalf is not this module's job.
    """
    if portfolio is None or action.target_weight is None:
        return None
    total = portfolio.total_value
    if total <= 0:
        return None

    in_class = (
        [h for h in portfolio.holdings if h.asset_class is action.asset_class]
        if action.asset_class is not None
        else [h for h in portfolio.holdings if h.asset_class.value in _GROWTH_CLASSES]
    )
    if not in_class:
        return None

    current = sum(h.market_value for h in in_class)
    if current <= 0:
        return None

    # Solved against the *post-sale* total, not the current one. The proceeds leave the
    # portfolio, so scaling the group to `weight * total` overshoots: the book shrinks by exactly
    # what was sold and the survivors' weights rise to fill the gap. Same water-filling identity
    # `policy/concentration.solve_trim_targets` uses, for the same reason.
    #
    #   (current - sold) / (total - sold) = w   =>   sold = (current - w*total) / (1 - w)
    weight = action.target_weight
    if current <= weight * total:
        return 0.0
    sold_total = (current - weight * total) / (1 - weight) if weight < 1 else current
    target = current - sold_total

    scale = target / current
    for lot in in_class:
        if lot.quantity is not None:
            lot.quantity *= scale
        if lot.cost_basis is not None:
            lot.cost_basis *= scale
        lot.market_value *= scale
    return round(sold_total, 2)


def _changes(
    before: ProfileAnalytics,
    after: ProfileAnalytics,
    before_pa: PortfolioAnalytics | None,
    after_pa: PortfolioAnalytics | None,
) -> list[MetricChange]:
    changes = [
        # The house's one hard constraint, so it leads: money needed soon should not be sitting
        # in growth assets, and a plan that claims to fix that has to show the share coming down.
        MetricChange(
            label="growth asset share",
            before=before.growth_asset_share,
            after=after.growth_asset_share,
            higher_is_better=False,
        ),
        MetricChange(
            label="deployable cash",
            before=before.investable_cash,
            after=after.investable_cash,
        ),
        # Both ledgers together. Cash and the portfolio are separate numbers, so a sale moves
        # value from one into the other and would read as though selling made the holder richer.
        # Summing them is the figure a person would recognize as theirs, and it correctly stays
        # flat when a position is merely converted to cash.
        #
        # No preferred direction: a sale that realizes tax lowers it on purpose, and scoring that
        # as a regression would penalize the right decision.
        MetricChange(
            label="total capital",
            before=before.investable_cash + (before_pa.total_value if before_pa else 0.0),
            after=after.investable_cash + (after_pa.total_value if after_pa else 0.0),
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


def _total_tax(action_set: ActionSet) -> TaxRange | None:
    """Summed estimates, or None when nothing could produce one.

    Summing the ends independently is the right conservative move rather than a modelling
    shortcut: the actions in a plan are all settled in the same tax year by the same filer, so
    the treatments move together. Treating them as independent draws and narrowing the total
    would claim a diversification that does not exist.
    """
    estimates = [a.estimated_tax for a in action_set.actions if a.estimated_tax is not None]
    if not estimates:
        return None
    total = estimates[0]
    for estimate in estimates[1:]:
        total = total + estimate
    return total


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

    Trims and buys are judged on the position's **value**, not its weight. Weight was the
    obvious choice and it is wrong: a plan that trims several positions onto the same target
    leaves every weight unchanged, because the whole portfolio shrinks by exactly what was sold.
    A trim taking a holding from $20,000 to $19,000 plainly did something, and calling it
    ineffective because the percentages did not move blames one action for what the rest of the
    plan did. Whether the *portfolio* ended up where it should is a plan-level question, and
    `changes` and the guardrail diff already answer it.
    """
    skip = set(unapplied)
    failures: list[str] = []

    for action in action_set.actions:
        if action.action_id in skip or action.kind is ActionKind.hold:
            continue

        moved: bool | None = None
        if action.kind is ActionKind.trim_position and before_pa and after_pa and action.symbol:
            moved = (
                _position_value(after_pa, action.symbol)
                < _position_value(before_pa, action.symbol) - 1e-9
            )
        elif action.kind is ActionKind.add_position and before_pa and after_pa and action.symbol:
            moved = (
                _position_value(after_pa, action.symbol)
                > _position_value(before_pa, action.symbol) + 1e-9
            )
        elif action.kind is ActionKind.rebalance_to_target:
            # The house's de-risking action targets the growth share rather than one instrument,
            # so that is the number it has to move. Judged on the share and not on any single
            # position: selling the right amount across several holdings is a success even
            # though no individual weight lands on the target.
            moved = after.growth_asset_share < before.growth_asset_share - 1e-9

        if moved is False:
            failures.append(action.action_id)

    return failures


def _position_value(pa: PortfolioAnalytics, symbol: str) -> float:
    """Dollar value of one symbol, aggregated across accounts.

    Reconstructed from the symbol-aggregated weights rather than by re-walking holdings, so it
    inherits the duplicate-symbol handling `analyze_portfolio` already got right.
    """
    return pa.weights.get(symbol, 0.0) * pa.total_value
