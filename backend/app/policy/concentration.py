"""What to do about a position that has grown too large.

The narrowest genuinely quantitative decision in the product, and the one the flagship question
asks: *"Should I sell some NVDA right now and pay off my credit card, or keep riding it?"* Until
now six personas answered that in prose and nobody checked the arithmetic. Here the answer is a
share count, sequenced against the blocking guardrails, with the tax cost of getting there
stated rather than omitted.

Three things this module is careful about:

**Whose cap.** The threshold is `PolicyParameters.max_single_name_weight`, which comes from the
advisor. Bogle trims at 5%, Buffett tolerates 25% — the disagreement that used to live in tone
now lives in a number, and both answers can be costed.

**Where the money goes.** Proceeds are not left as an exercise. A blocking guardrail claims them
first, in the order the guardrails themselves are ranked: high-APR debt before a thin emergency
reserve before anything discretionary. That sequencing is the actual answer to the flagship
question, and it is arithmetic rather than judgment.

**What it costs to act.** Selling an appreciated position realizes gain. The estimate here is
position-level, because that is the only basis the data model carries — see
`estimate_tax_impact`. It is reported as an estimate and never silently treated as zero.
"""

from __future__ import annotations

from app.analytics.portfolio_analytics import PortfolioAnalytics
from app.analytics.profile_analytics import ProfileAnalytics
from app.domain.action import ActionKind, ProposedAction
from app.domain.advisor import PolicyParameters
from app.domain.portfolio import Holding, Portfolio
from app.domain.profile import FinancialProfile
from app.domain.report import Guardrail, GuardrailSeverity

# Long-term capital gains rate assumed when estimating the cost of trimming. A single blended
# rate is a simplification and is labelled as one wherever the number surfaces; the alternative
# is holding period, filing status, and state tax, none of which the profile collects.
ASSUMED_CAPITAL_GAINS_RATE = 0.15

# Sequence bands. Blocking guardrails are resolved before anything optional, and the trim that
# funds them has to happen first of all.
SEQ_RAISE_CASH = 0
SEQ_RESOLVE_BLOCKING = 1
SEQ_DISCRETIONARY = 2


def propose(
    profile: FinancialProfile,
    analytics: ProfileAnalytics,
    portfolio: Portfolio | None,
    portfolio_analytics: PortfolioAnalytics | None,
    guardrails: list[Guardrail],
    params: PolicyParameters,
    *,
    advisor_id: str = "policy",
) -> list[ProposedAction]:
    """Trim over-weight positions, and put the proceeds where the guardrails demand.

    Returns an empty list when there is nothing to do — an explicit `hold` is the caller's
    decision to make, since "no action from this policy" and "this policy recommends inaction"
    are different claims.
    """
    if portfolio is None or portfolio_analytics is None:
        return []
    total = portfolio_analytics.total_value
    if total <= 0:
        return []

    value_by_symbol = {s: w * total for s, w in portfolio_analytics.weights.items()}
    targets, effective_cap = solve_trim_targets(
        value_by_symbol, total, params.max_single_name_weight
    )
    if not targets:
        return []

    actions: list[ProposedAction] = []
    proceeds = 0.0

    # Largest first: the biggest position is both the largest risk and the cheapest place to
    # raise a given amount of cash.
    for symbol in sorted(targets, key=lambda s: value_by_symbol[s], reverse=True):
        amount = value_by_symbol[symbol] - targets[symbol]
        if amount <= 0:
            continue

        lots = [h for h in portfolio.holdings if h.symbol == symbol]
        shares = _shares_for(lots, amount)
        tax = estimate_tax_impact(lots, amount)

        actions.append(
            ProposedAction(
                action_id=f"trim_{symbol.lower()}",
                kind=ActionKind.trim_position,
                symbol=symbol,
                # Prefer a share count where the data supports one: it is what the user actually
                # enters at a broker. Fall back to dollars when any lot lacks a quantity.
                shares=shares,
                amount_usd=None if shares is not None else round(amount, 2),
                sequence=SEQ_RAISE_CASH,
                proposed_by=advisor_id,
                estimated_tax_impact_usd=tax,
                rationale=_trim_rationale(
                    symbol,
                    portfolio_analytics.weights[symbol],
                    params,
                    effective_cap,
                    len(value_by_symbol),
                    tax,
                ),
            )
        )
        proceeds += amount

    actions.extend(_deploy_proceeds(profile, analytics, guardrails, proceeds, advisor_id))
    return actions


def solve_trim_targets(
    value_by_symbol: dict[str, float], total: float, cap: float
) -> tuple[dict[str, float], float]:
    """Target values for the positions that must be trimmed, and the cap actually applied.

    Two things make this more than `cap * total`.

    **The proceeds leave.** Money raised by trimming pays debt or sits in cash; it is no longer
    in the portfolio. So selling each oversized position down to `cap * total` overshoots: the
    portfolio shrinks by exactly what was sold, and the survivors' weights rise to fill the gap.
    Trimming a 68/32 portfolio to a "20% cap" that way leaves 50/50. The targets have to be
    solved against the *post-trim* total, which is the classic water-filling problem:

        T_final = untrimmed_value / (1 - k * cap)

    for the k largest positions, choosing the k where the k-th position is above the resulting
    target and the (k+1)-th is not.

    **Some caps are unreachable.** A portfolio of two holdings cannot put any position under 20%
    — the arithmetic floor is 1/n. Rather than proposing a sale that cannot achieve its own
    stated goal, the cap is raised to 1/n and the rationale says so: getting below it is an
    allocation decision (buy more positions), not a trimming one.
    """
    n = len(value_by_symbol)
    if n == 0 or total <= 0:
        return {}, cap

    effective_cap = max(cap, 1.0 / n)
    items = sorted(value_by_symbol.items(), key=lambda kv: kv[1], reverse=True)
    values = [v for _, v in items]

    for k in range(1, n + 1):
        denominator = 1.0 - k * effective_cap
        if denominator <= 1e-12:
            # These k positions at the cap would already account for the whole portfolio.
            continue
        untrimmed = total - sum(values[:k])
        final_total = untrimmed / denominator
        target = effective_cap * final_total

        kth_is_over = values[k - 1] > target + 1e-9
        next_is_under = k == n or values[k] <= target + 1e-9
        if kth_is_over and next_is_under:
            return {items[i][0]: target for i in range(k)}, effective_cap

    return {}, effective_cap


def estimate_tax_impact(lots: list[Holding], amount: float) -> float | None:
    """Estimated tax on selling `amount` worth of these lots.

    Position-level, not lot-level: the domain model carries one `cost_basis` per holding, so
    this assumes the sale realizes gain in the same proportion the whole position carries. Real
    tax depends on which lots are sold, how long they were held, and where the seller lives.

    Returns None when no lot declares a basis — unknown, which is not the same as zero. Sales
    from tax-advantaged accounts contribute nothing, which is the one part of this that is
    exactly right rather than approximate.
    """
    taxable = [h for h in lots if not h.account_type.is_tax_advantaged]
    with_basis = [h for h in taxable if h.cost_basis is not None]
    if not with_basis:
        return None if any(h.cost_basis is None for h in taxable) else 0.0

    taxable_value = sum(h.market_value for h in with_basis)
    if taxable_value <= 0:
        return 0.0

    gain_fraction = (
        sum((h.market_value - (h.cost_basis or 0.0)) for h in with_basis) / taxable_value
    )
    if gain_fraction <= 0:
        return 0.0

    # Only the share of the sale coming from taxable lots is exposed.
    taxable_share = taxable_value / sum(h.market_value for h in lots)
    realized_gain = amount * taxable_share * gain_fraction
    return round(realized_gain * ASSUMED_CAPITAL_GAINS_RATE, 2)


def _shares_for(lots: list[Holding], amount: float) -> float | None:
    """Share count equivalent to `amount`, or None when any lot lacks a quantity."""
    if not lots or any(h.quantity is None for h in lots):
        return None
    shares = sum(h.quantity or 0.0 for h in lots)
    value = sum(h.market_value for h in lots)
    if shares <= 0 or value <= 0:
        return None
    return round(amount / (value / shares), 4)


def _deploy_proceeds(
    profile: FinancialProfile,
    analytics: ProfileAnalytics,
    guardrails: list[Guardrail],
    proceeds: float,
    advisor_id: str,
) -> list[ProposedAction]:
    """Claim the proceeds against blocking guardrails, most expensive problem first.

    High-APR debt outranks the emergency reserve because it compounds against the holder every
    day it stands, while a thin reserve is a risk that may never be realized. Anything left over
    is deliberately left unallocated: this policy's remit is concentration, and inventing a
    destination for surplus cash is the allocation policy's job.
    """
    blocking = {g.code for g in guardrails if g.severity is GuardrailSeverity.blocking}
    actions: list[ProposedAction] = []
    remaining = proceeds

    if "HIGH_APR_DEBT" in blocking and remaining > 0:
        for debt in sorted(profile.debts, key=lambda d: d.apr, reverse=True):
            if remaining <= 0:
                break
            if debt.apr <= 0.08:
                continue
            pay = min(debt.balance, remaining)
            actions.append(
                ProposedAction(
                    action_id=f"pay_{_slug(debt.name)}",
                    kind=ActionKind.pay_down_debt,
                    symbol=debt.name,
                    amount_usd=round(pay, 2),
                    sequence=SEQ_RESOLVE_BLOCKING,
                    proposed_by=advisor_id,
                    rationale=(
                        f"{debt.name} costs {debt.apr:.1%} a year — "
                        f"${debt.balance * debt.apr:,.0f} on the current balance. Clearing it is "
                        "a guaranteed return no position in the portfolio can promise."
                    ),
                )
            )
            remaining -= pay

    if "EMERGENCY_FUND_THIN" in blocking and remaining > 0:
        monthly = profile.expenses.monthly_essential
        shortfall = max(0.0, monthly * 3 - analytics.liquid_assets)
        if shortfall > 0:
            top_up = min(shortfall, remaining)
            actions.append(
                ProposedAction(
                    action_id="build_reserve",
                    kind=ActionKind.build_emergency_fund,
                    amount_usd=round(top_up, 2),
                    sequence=SEQ_RESOLVE_BLOCKING,
                    proposed_by=advisor_id,
                    rationale=(
                        f"Holding ${top_up:,.0f} of the proceeds takes the reserve to "
                        f"{(analytics.liquid_assets + top_up) / monthly:.1f} months of essential "
                        "expenses, above the three-month floor."
                    ),
                )
            )
            remaining -= top_up

    return actions


def _trim_rationale(
    symbol: str,
    weight: float,
    params: PolicyParameters,
    effective_cap: float,
    holding_count: int,
    tax: float | None,
) -> str:
    cap = params.max_single_name_weight
    lead = f"{symbol} is {weight:.0%} of the portfolio against a {cap:.0%} single-name cap."
    if effective_cap > cap + 1e-9:
        lead += (
            f" With {holding_count} positions the most any one can be trimmed to is "
            f"{effective_cap:.0%} — reaching {cap:.0%} means holding more names, which is an "
            "allocation decision rather than a sale."
        )
    if params.allows_concentration_on_conviction:
        lead += (
            " Concentration is acceptable where the holder genuinely understands the business, "
            "so treat this as the size the cap implies rather than an instruction to sell."
        )
    if tax is None:
        lead += " Tax cost is unknown — no cost basis is recorded for this position."
    elif tax > 0:
        lead += (
            f" Trimming realizes an estimated ${tax:,.0f} in tax at a blended {ASSUMED_CAPITAL_GAINS_RATE:.0%} "
            "long-term rate; the real figure depends on holding period and where you file."
        )
    return lead


def _slug(name: str) -> str:
    return "".join(c if c.isalnum() else "_" for c in name.lower()).strip("_") or "debt"
