"""AdvisorOS's own financial rules, which belong to no persona.

A 22.9% credit card balance outranks a marginal investment decision. An emergency reserve below
three months of essential expenses has to be rebuilt before new market risk is added. Neither of
those is Buffett's opinion or Bogle's opinion — they are this product's policy, and they would
be the same if every persona were deleted tomorrow.

Keeping them here rather than inside a persona policy matters for two reasons.

**Attribution.** If the reserve top-up were emitted by "the Bogle policy", the report would
imply Bogle prescribed it, which is both false and the exact class of error `domain/policy.py`
exists to prevent. House actions carry `proposed_by="house"` and say so.

**Non-negotiability.** These correspond to blocking guardrails already computed in
`analytics/guardrails.py`, and the committee charter tells every advisor they may not recommend
anything contradicting one. Rules that cannot be argued with should not be expressed as one
advisor's argument.

Where the two layers meet: house rules claim resources first. A persona policy proposes raising
cash; the house decides that the first $9,000 of it clears a 22.9% card. What is left over is
where personas legitimately differ.
"""

from __future__ import annotations

from app.analytics.profile_analytics import ProfileAnalytics
from app.domain.action import ActionKind, ProposedAction
from app.domain.profile import FinancialProfile
from app.domain.report import Guardrail, GuardrailSeverity

# Matches HIGH_APR_THRESHOLD in analytics/profile_analytics.py. Debt above this is treated as a
# guaranteed return no position can promise, rather than as one option among several.
HIGH_APR_THRESHOLD = 0.08

# Matches the EMERGENCY_FUND_THIN blocking guardrail in analytics/guardrails.py.
EMERGENCY_FUND_FLOOR_MONTHS = 3.0

HOUSE = "house"

# Sequence bands, shared with the persona policies so a merged plan orders correctly.
SEQ_RAISE_CASH = 0
SEQ_RESOLVE_BLOCKING = 1
SEQ_DISCRETIONARY = 2


def claim_proceeds(
    profile: FinancialProfile,
    analytics: ProfileAnalytics,
    guardrails: list[Guardrail],
    available: float,
) -> tuple[list[ProposedAction], float]:
    """Spend `available` against blocking guardrails, and return what remains.

    Order is not arbitrary. High-APR debt is settled before the emergency reserve because it
    compounds against the holder every day it stands, while a thin reserve is a risk that may
    never be realized. Both come before anything discretionary.

    Returns the actions and the unclaimed remainder — deliberately not allocating the surplus,
    because where surplus cash should go is a portfolio question and this module only enforces
    constraints.
    """
    blocking = {g.code for g in guardrails if g.severity is GuardrailSeverity.blocking}
    actions: list[ProposedAction] = []
    remaining = available

    if "HIGH_APR_DEBT" in blocking:
        for debt in sorted(profile.debts, key=lambda d: d.apr, reverse=True):
            if remaining <= 0:
                break
            if debt.apr <= HIGH_APR_THRESHOLD:
                continue
            pay = min(debt.balance, remaining)
            actions.append(
                ProposedAction(
                    action_id=f"pay_{_slug(debt.name)}",
                    kind=ActionKind.pay_down_debt,
                    symbol=debt.name,
                    amount_usd=round(pay, 2),
                    sequence=SEQ_RESOLVE_BLOCKING,
                    proposed_by=HOUSE,
                    rationale=(
                        f"{debt.name} costs {debt.apr:.1%} a year — "
                        f"${debt.balance * debt.apr:,.0f} on the current balance. Clearing it "
                        "returns more than any position in the portfolio can promise. This is "
                        "an AdvisorOS rule, not an advisor's view."
                    ),
                )
            )
            remaining -= pay

    if "EMERGENCY_FUND_THIN" in blocking and remaining > 0:
        monthly = profile.expenses.monthly_essential
        shortfall = max(0.0, monthly * EMERGENCY_FUND_FLOOR_MONTHS - analytics.liquid_assets)
        if shortfall > 0:
            top_up = min(shortfall, remaining)
            actions.append(
                ProposedAction(
                    action_id="build_reserve",
                    kind=ActionKind.build_emergency_fund,
                    amount_usd=round(top_up, 2),
                    sequence=SEQ_RESOLVE_BLOCKING,
                    proposed_by=HOUSE,
                    rationale=(
                        f"Holding ${top_up:,.0f} takes the reserve to "
                        f"{(analytics.liquid_assets + top_up) / monthly:.1f} months of essential "
                        f"expenses, above the {EMERGENCY_FUND_FLOOR_MONTHS:.0f}-month floor "
                        "AdvisorOS applies before adding market risk."
                    ),
                )
            )
            remaining -= top_up

    return actions, remaining


def _slug(name: str) -> str:
    return "".join(c if c.isalnum() else "_" for c in name.lower()).strip("_") or "debt"
