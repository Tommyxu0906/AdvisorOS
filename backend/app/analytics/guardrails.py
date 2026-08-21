"""Hard portfolio guardrails.

These are rules code enforces. They are injected into every advisor prompt AND re-checked
against the synthesized output afterwards, so a persuasive model cannot talk its way past them.

**Redomained when the product narrowed to the portfolio.** The old set was household finance —
credit-card APR, emergency-fund months, debt service, cash flow. None of that is this product's
business any more, and a platform that advises on an equity book has no standing to tell someone
what to do about their mortgage.

What replaces it is the constraint that actually binds an investment decision: **money needed
soon cannot sit in growth assets.** That is not a persona's opinion — Buffett and Munger would
both agree, and it would still hold if every persona were deleted. It is the one blocking rule
the house keeps, and it is what a lens can find itself overruled by.

The rest are cautions: concentration, a book too thin to be diversified, and no cash to fund a
purchase with.
"""

from __future__ import annotations

import re

from app.analytics.portfolio_analytics import PortfolioAnalytics
from app.analytics.profile_analytics import NEAR_TERM_EQUITY_CEILING, ProfileAnalytics
from app.domain.portfolio import Portfolio
from app.domain.profile import FinancialProfile, HorizonBand
from app.domain.report import Guardrail, GuardrailSeverity

CONCENTRATION_LIMIT = 0.25
THIN_BOOK_POSITIONS = 4


def evaluate_guardrails(
    profile: FinancialProfile,
    analytics: ProfileAnalytics,
    portfolio: Portfolio | None = None,
    portfolio_analytics: PortfolioAnalytics | None = None,
) -> list[Guardrail]:
    """Return every triggered guardrail, most severe first."""
    rails: list[Guardrail] = []

    # --- blocking: the only one, and the one the committee can be overruled by -----------
    if (
        profile.horizon_band is HorizonBand.near
        and analytics.growth_asset_share > NEAR_TERM_EQUITY_CEILING
    ):
        rails.append(
            Guardrail(
                code="HORIZON_RISK_MISMATCH",
                severity=GuardrailSeverity.blocking,
                message=(
                    f"Money needed within three years is {analytics.growth_asset_share:.0%} "
                    "invested in growth assets."
                ),
                detail=(
                    "A drawdown does not care about the date the money is needed. Reducing the "
                    "growth share comes before any question of which growth assets to hold — "
                    "and no advisor may recommend otherwise."
                ),
            )
        )

    # --- cautions -----------------------------------------------------------------------
    if analytics.largest_position_weight > CONCENTRATION_LIMIT:
        rails.append(
            Guardrail(
                code="POSITION_CONCENTRATION",
                severity=GuardrailSeverity.caution,
                message=(
                    f"The largest position is {analytics.largest_position_weight:.0%} of the book."
                ),
                detail=(
                    "Whether that is too much is exactly where investors legitimately differ, "
                    "which is why this is a caution and the threshold is a persona's to set."
                ),
            )
        )

    if analytics.position_count and analytics.position_count < THIN_BOOK_POSITIONS:
        rails.append(
            Guardrail(
                code="THIN_BOOK",
                severity=GuardrailSeverity.caution,
                message=f"The book holds {analytics.position_count} positions.",
                detail=(
                    "Few enough that single-name risk dominates whatever the asset allocation "
                    "says. Deliberate for some investors and an accident for others."
                ),
            )
        )

    if analytics.investable_cash <= 0 and analytics.portfolio_value > 0:
        rails.append(
            Guardrail(
                code="NO_DEPLOYABLE_CASH",
                severity=GuardrailSeverity.info,
                message="There is no cash available to deploy.",
                detail="Anything bought has to be funded by selling something already held.",
            )
        )

    severity_order = {
        GuardrailSeverity.blocking: 0,
        GuardrailSeverity.caution: 1,
        GuardrailSeverity.info: 2,
    }
    rails.sort(key=lambda r: severity_order[r.severity])
    return rails


def render_guardrails(rails: list[Guardrail]) -> str:
    """Stable text block for prompts."""
    if not rails:
        return "No hard financial guardrails were triggered for this profile."
    lines = [
        "The following constraints were computed deterministically from the user's numbers.",
        "They are not suggestions. You must not recommend anything that contradicts a BLOCKING item.",
        "",
    ]
    lines += [f"{g.render()} ({g.detail})" if g.detail else g.render() for g in rails]
    return "\n".join(lines)


# --- Post-hoc verification -------------------------------------------------------------
#
# Cheap lexical check that the synthesized report did not sail past a blocking guardrail.
# Deliberately conservative: it flags for human attention, it does not rewrite the report.

# Lexical net over the prose, secondary to the counterfactual which is the real check. Narrow
# on purpose: this domain says "stay invested through a drawdown" constantly, and a filter that
# fired on ordinary investing language would train people to write around it.
_VIOLATION_PATTERNS: dict[str, tuple[re.Pattern[str], ...]] = {
    "HORIZON_RISK_MISMATCH": (
        re.compile(r"(ignore|don'?t worry about|set aside) (the |your )?(time )?horizon", re.I),
        re.compile(
            r"(put|move|invest) (the whole|all of the|your entire) (balance|portfolio|book) "
            r"(in|into) (equit|stock|growth)",
            re.I,
        ),
        re.compile(
            r"stay fully invested (despite|regardless of) (the |your )?(need|horizon)", re.I
        ),
    ),
}


def verify_report_against_guardrails(report_text: str, rails: list[Guardrail]) -> list[str]:
    """Return human-readable descriptions of apparent guardrail contradictions."""
    violations: list[str] = []
    for rail in rails:
        if rail.severity is not GuardrailSeverity.blocking:
            continue
        for pattern in _VIOLATION_PATTERNS.get(rail.code, ()):
            if pattern.search(report_text):
                violations.append(
                    f"{rail.code}: report text matched '{pattern.pattern}', which appears to "
                    f"contradict the blocking guardrail."
                )
                break
    return violations
