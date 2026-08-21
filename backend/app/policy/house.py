"""AdvisorOS's own portfolio rules, which belong to no persona.

Money needed within three years does not belong in growth assets. That is not Buffett's opinion
or Bogle's opinion — it is this product's policy, and it would be the same if every persona were
deleted tomorrow. A drawdown does not care about the date the money is needed.

**Redomained when the product narrowed to the portfolio.** This module used to settle credit-card
balances and rebuild emergency reserves, which is household finance and no longer this product's
business. What is left is the one constraint that binds an investment decision regardless of who
is advising.

Keeping it here rather than inside a persona policy matters for two reasons.

**Attribution.** If the de-risking action were emitted by "the Bogle policy", the output would
imply Bogle prescribed it, which is both false and the exact class of error `domain/policy.py`
exists to prevent. House actions carry `proposed_by="house"` and say so.

**Non-negotiability.** It corresponds to a blocking guardrail already computed in
`analytics/guardrails.py`, and the committee charter tells every advisor they may not recommend
anything contradicting one. A rule that cannot be argued with should not be expressed as one
advisor's argument — and this is the rule a lens can find itself overruled by.

Where the two layers meet: the house claims first. A persona may hold whatever view it likes
about which growth assets are worth owning; if the money is needed in eighteen months, the
growth share comes down before that question is reached.
"""

from __future__ import annotations

from app.analytics.profile_analytics import NEAR_TERM_EQUITY_CEILING, ProfileAnalytics
from app.domain.action import ActionKind, ProposedAction
from app.domain.portfolio import Portfolio
from app.domain.profile import FinancialProfile
from app.domain.report import Guardrail, GuardrailSeverity

HOUSE = "house"

# Sequence bands. The house de-risks first; everything a persona wants comes after, because a
# near-term need is settled before the question of which growth assets to own is even reached.
SEQ_HOUSE_DERISK = 0
SEQ_RAISE_CASH = 10


def claim_first(
    profile: FinancialProfile,
    analytics: ProfileAnalytics,
    portfolio: Portfolio | None,
    guardrails: list[Guardrail],
) -> list[ProposedAction]:
    """Actions the house requires before any persona's view is reached.

    Returns an empty list in the ordinary case, which is the point: most books do not trip a
    blocking rule, and a house layer that always had something to say would be a house layer
    nobody read.
    """
    blocking = {g.code for g in guardrails if g.severity is GuardrailSeverity.blocking}
    if "HORIZON_RISK_MISMATCH" not in blocking or portfolio is None:
        return []

    total = sum(h.market_value for h in portfolio.holdings)
    if total <= 0:
        return []

    # Bring the growth share down to the ceiling, and no further. The house enforces the
    # constraint; it does not express a preference about what the book should look like beyond
    # that, because that is exactly where the personas differ.
    excess_share = analytics.growth_asset_share - NEAR_TERM_EQUITY_CEILING
    amount = round(total * excess_share, 2)
    if amount <= 0:
        return []

    return [
        ProposedAction(
            action_id="derisk_near_term",
            kind=ActionKind.rebalance_to_target,
            asset_class=None,
            target_weight=NEAR_TERM_EQUITY_CEILING,
            sequence=SEQ_HOUSE_DERISK,
            proposed_by=HOUSE,
            rationale=(
                f"This money is needed in {profile.horizon_years:.0f} year"
                f"{'s' if profile.horizon_years != 1 else ''} while "
                f"{analytics.growth_asset_share:.0%} of the book sits in growth assets. Under "
                f"AdvisorOS policy a near-term need caps that share at "
                f"{NEAR_TERM_EQUITY_CEILING:.0%}, which implies moving about "
                f"{amount:,.0f} out of growth. This is a house rule, not an advisor's view, and "
                "no advisor may recommend against it."
            ),
        )
    ]
