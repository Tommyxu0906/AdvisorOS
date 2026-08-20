"""What a persona's view of concentration implies for an oversized position.

The narrowest genuinely quantitative decision in the product, and the one the flagship question
asks: *"Should I sell some NVDA right now and pay off my credit card, or keep riding it?"* Six
personas used to answer that in prose and nobody checked the arithmetic. Here the answer is a
share count, sequenced behind the house's blocking constraints, with the tax of getting there
stated rather than omitted.

Three things this module is careful about.

**Whose threshold.** Every number resolves through `PolicyProfile.resolve`, which reports
whether the value came from the subject or from AdvisorOS. Distillation can establish that
Buffett tolerates concentration; it cannot establish that his cap for a retail investor is 25%.
When a persona has no evidence-backed number the policy still runs, on a house threshold that
the rationale names as a house threshold. The alternative — a hand-authored cap presented as the
subject's rule — manufactures precision exactly where the evidence thins out.

**Whether to speak at all.** A persona whose scopes exclude concentration produces nothing.
Bogle declining to size an individual security is a real position, not a gap to be filled by
computing one anyway.

**What it costs to act.** Selling an appreciated position realizes gain. The estimate is
position-level, because that is the only basis the data model carries — see `estimate_tax_impact`.
It is reported as an estimate and never silently treated as zero.

House constraints — clearing high-APR debt, rebuilding a thin reserve — are not here. They are
in `policy/house.py`, because they are this product's rules rather than any advisor's view.
"""

from __future__ import annotations

from app.analytics.portfolio_analytics import PortfolioAnalytics
from app.analytics.profile_analytics import ProfileAnalytics
from app.domain.action import ActionKind, ProposedAction, TaxRange
from app.domain.policy import (
    Direction,
    PolicyParameterName,
    PolicyProfile,
    PolicyScope,
    ResolvedParameter,
)
from app.domain.portfolio import Holding, Portfolio
from app.domain.profile import FinancialProfile
from app.domain.report import Guardrail
from app.policy import house

# The two ends of the tax range. Neither is a computed rate, and the distance between them is
# the point: `Holding` carries no acquisition date, so a sale is either a long-term gain or
# ordinary income and nothing in the data says which. Filing status and state are not collected
# either, so the upper end is a mid-to-upper federal marginal bracket rather than this person's
# bracket. Deriving that properly means a bracket table with a stated year and filing status —
# worth doing, and worth doing as configuration with provenance rather than a constant here.
ASSUMED_LONG_TERM_RATE = 0.15
ASSUMED_ORDINARY_RATE = 0.32

RATE_ASSUMPTION = (
    "The low end treats the whole sale as a long-term gain and the high end as ordinary income. "
    "Nothing recorded says which applies, because holding dates are not collected. Choosing which "
    "lots to sell can move the real figure outside this range in either direction."
)
NO_TAX_ASSUMPTION = "Held in a tax-advantaged account, so a sale realizes nothing to tax."

# Used when a persona carries no evidence-backed concentration threshold. An AdvisorOS number,
# and described as one everywhere it appears.
HOUSE_SINGLE_NAME_CAP = 0.20


def propose(
    profile: FinancialProfile,
    analytics: ProfileAnalytics,
    portfolio: Portfolio | None,
    portfolio_analytics: PortfolioAnalytics | None,
    guardrails: list[Guardrail],
    policy_profile: PolicyProfile,
    *,
    advisor_id: str = "house",
    display_name: str = "AdvisorOS",
) -> list[ProposedAction]:
    """Trim over-weight positions, then hand the proceeds to the house rules.

    Returns an empty list when this persona does not opine on concentration, or when nothing
    exceeds the applicable threshold. An explicit `hold` is the caller's decision to make:
    "this policy produced nothing" and "this policy recommends inaction" are different claims.
    """
    if not policy_profile.covers(PolicyScope.concentration):
        return []
    if portfolio is None or portfolio_analytics is None:
        return []
    total = portfolio_analytics.total_value
    if total <= 0:
        return []

    cap = policy_profile.resolve(
        PolicyParameterName.single_name_concentration, HOUSE_SINGLE_NAME_CAP
    )

    value_by_symbol = {s: w * total for s, w in portfolio_analytics.weights.items()}
    targets, effective_cap = solve_trim_targets(value_by_symbol, total, cap.value)
    if not targets:
        return []

    # Selling raises cash that leaves the portfolio, so the book the surviving positions are
    # weighed against is smaller than today's. A position inside the cap now can be over it
    # afterwards, purely because the denominator moved — and the rationale has to say so, or it
    # reads as "this is 12% of the portfolio, so reduce it under a 20% threshold".
    post_trim_total = sum(targets.get(s, v) for s, v in value_by_symbol.items())

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
                sequence=house.SEQ_RAISE_CASH,
                proposed_by=advisor_id,
                estimated_tax=tax,
                rationale=_trim_rationale(
                    symbol=symbol,
                    weight=portfolio_analytics.weights[symbol],
                    share_of_remaining=(
                        value_by_symbol[symbol] / post_trim_total if post_trim_total > 0 else None
                    ),
                    cap=cap,
                    effective_cap=effective_cap,
                    holding_count=len(value_by_symbol),
                    tax=tax,
                    policy_profile=policy_profile,
                    display_name=display_name,
                    is_house_run=advisor_id == "house",
                ),
            )
        )
        proceeds += amount

    claimed, _ = house.claim_proceeds(profile, analytics, guardrails, proceeds)
    return actions + claimed


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


def estimate_tax_impact(lots: list[Holding], amount: float) -> TaxRange | None:
    """Estimated tax on selling `amount` worth of these lots, as a range.

    Position-level, not lot-level: the domain model carries one `cost_basis` per holding, so
    this assumes the sale realizes gain in the same proportion the whole position carries.

    The range spans the two tax treatments the data cannot distinguish between — see `TaxRange`.
    `Holding` records no acquisition date, so whether a sale is a long-term gain or ordinary
    income is genuinely unknown, and that gap is far wider than any rounding.

    Returns None when no lot declares a basis — unknown, which is not the same as zero. Sales
    from tax-advantaged accounts contribute nothing, and that is the one case where the two ends
    of the range legitimately coincide, because there is no uncertainty left to express.
    """
    taxable = [h for h in lots if not h.account_type.is_tax_advantaged]
    with_basis = [h for h in taxable if h.cost_basis is not None]
    if not with_basis:
        if any(h.cost_basis is None for h in taxable):
            return None
        return TaxRange(low_usd=0.0, high_usd=0.0, assumption=NO_TAX_ASSUMPTION)

    taxable_value = sum(h.market_value for h in with_basis)
    if taxable_value <= 0:
        return TaxRange(low_usd=0.0, high_usd=0.0, assumption=NO_TAX_ASSUMPTION)

    gain_fraction = (
        sum((h.market_value - (h.cost_basis or 0.0)) for h in with_basis) / taxable_value
    )
    if gain_fraction <= 0:
        return TaxRange(
            low_usd=0.0,
            high_usd=0.0,
            assumption="This position is not showing a gain, so a sale realizes nothing to tax.",
        )

    # Only the share of the sale coming from taxable lots is exposed.
    taxable_share = taxable_value / sum(h.market_value for h in lots)
    realized_gain = amount * taxable_share * gain_fraction
    return TaxRange(
        low_usd=round(realized_gain * ASSUMED_LONG_TERM_RATE, 2),
        high_usd=round(realized_gain * ASSUMED_ORDINARY_RATE, 2),
        assumption=RATE_ASSUMPTION,
    )


def _shares_for(lots: list[Holding], amount: float) -> float | None:
    """Share count equivalent to `amount`, or None when any lot lacks a quantity."""
    if not lots or any(h.quantity is None for h in lots):
        return None
    shares = sum(h.quantity or 0.0 for h in lots)
    value = sum(h.market_value for h in lots)
    if shares <= 0 or value <= 0:
        return None
    return round(amount / (value / shares), 4)


def _trim_rationale(
    *,
    symbol: str,
    weight: float,
    share_of_remaining: float | None,
    cap: ResolvedParameter,
    effective_cap: float,
    holding_count: int,
    tax: TaxRange | None,
    policy_profile: PolicyProfile,
    display_name: str,
    is_house_run: bool = False,
) -> str:
    """Phrased as what a threshold implies, not as an instruction to trade.

    "Sell 40 shares" is an instruction. "Under a 5% concentration policy this would imply
    reducing about 40 shares" is a scenario, which is what this product produces and what its
    disclaimer says it produces. The distinction costs nothing and is the difference between
    educational analysis and a personalized recommendation.

    `share_of_remaining` handles the case that otherwise reads as a contradiction. Trimming is
    solved against the *post-trim* book, because proceeds leave the portfolio — so a position
    comfortably inside the threshold today can still need trimming once the larger positions are
    sold and the denominator shrinks. Stating only today's weight produces the sentence "VXUS is
    12% of the portfolio, so under a 20% threshold this implies reducing it", which is nonsense
    on its face and makes the whole panel look broken.
    """
    attribution = cap.attribution(display_name, is_house_run=is_house_run)

    second_order = (
        share_of_remaining is not None
        and weight <= cap.value + 1e-9
        and share_of_remaining > cap.value + 1e-9
    )

    if second_order:
        parts = [
            f"{symbol} is {weight:.0%} of the portfolio today, which is inside the "
            f"{cap.value:.0%} single-name threshold — {attribution}. It appears here because "
            f"selling the larger positions takes that cash out of the portfolio: against the "
            f"smaller book that remains, {symbol} would be about {share_of_remaining:.0%}, and "
            f"the same threshold then applies to it."
        ]
    else:
        parts = [
            f"{symbol} is {weight:.0%} of the portfolio. Under a {cap.value:.0%} single-name "
            f"threshold — {attribution} — this scenario implies reducing it."
        ]

    if cap.is_house_number and cap.direction is not Direction.neutral:
        # The persona has a documented lean but no number. Say both, separately.
        leaning = (
            "is willing to hold concentrated positions"
            if cap.direction is Direction.tolerates
            else "avoids concentrated positions"
        )
        scope = f" ({'; '.join(cap.applicable_scope)})" if cap.applicable_scope else ""
        parts.append(
            f"{display_name} {leaning}{scope}, though no threshold of theirs is on record."
        )

    if effective_cap > cap.value + 1e-9:
        parts.append(
            f"With {holding_count} positions the most any one can be trimmed to is "
            f"{effective_cap:.0%}; reaching {cap.value:.0%} means holding more names, which is "
            "an allocation decision rather than a sale."
        )

    if policy_profile.allows_concentration_on_conviction:
        parts.append(
            "Concentration is acceptable on this view where the holder genuinely understands "
            "the business, so read this as the size the threshold implies rather than a verdict "
            "on the position."
        )

    if tax is None:
        parts.append("Tax cost is unknown — no cost basis is recorded for this position.")
    elif tax.high_usd > 0:
        parts.append(f"Acting would realize roughly {tax.render()} in tax. {tax.assumption}")

    return " ".join(parts)
