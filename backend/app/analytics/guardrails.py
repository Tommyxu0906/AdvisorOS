"""Hard financial guardrails.

These are rules code enforces. They are injected into every advisor prompt AND re-checked
against the synthesized report afterwards, so a persuasive model cannot talk its way past them.
"""

from __future__ import annotations

import re

from app.analytics.portfolio_analytics import PortfolioAnalytics
from app.analytics.profile_analytics import HIGH_APR_THRESHOLD, ProfileAnalytics
from app.domain.portfolio import Portfolio
from app.domain.profile import FinancialProfile
from app.domain.report import Guardrail, GuardrailSeverity

CONCENTRATION_LIMIT = 0.25
SHORT_HORIZON_YEARS = 3.0
DEBT_SERVICE_LIMIT = 0.36


def evaluate_guardrails(
    profile: FinancialProfile,
    analytics: ProfileAnalytics,
    portfolio: Portfolio | None = None,
    portfolio_analytics: PortfolioAnalytics | None = None,
) -> list[Guardrail]:
    """Return every triggered guardrail, most severe first."""
    rails: list[Guardrail] = []

    if analytics.emergency_fund_months < 3:
        rails.append(
            Guardrail(
                code="EMERGENCY_FUND_THIN",
                severity=GuardrailSeverity.blocking,
                message=(
                    "Emergency reserves are below three months of essential expenses. Do not "
                    "recommend deploying cash into volatile assets before the buffer is rebuilt."
                ),
                detail=f"{analytics.emergency_fund_months:.1f} months of essential expenses covered.",
            )
        )
    elif analytics.emergency_fund_months < 6:
        rails.append(
            Guardrail(
                code="EMERGENCY_FUND_BELOW_TARGET",
                severity=GuardrailSeverity.caution,
                message="Emergency reserves are below the six-month target.",
                detail=f"{analytics.emergency_fund_months:.1f} months covered.",
            )
        )

    if analytics.high_apr_debt_balance > 0:
        rails.append(
            Guardrail(
                code="HIGH_APR_DEBT",
                severity=GuardrailSeverity.blocking,
                message=(
                    f"Debt above {HIGH_APR_THRESHOLD:.0%} APR must be prioritized over taking on "
                    "additional market risk; its return is certain and its rate is high."
                ),
                detail=(
                    f"{analytics.high_apr_debt_balance:,.0f} outstanding at a weighted average of "
                    f"{analytics.weighted_avg_apr:.1%}, costing {analytics.annual_interest_cost:,.0f}/yr."
                ),
            )
        )

    if analytics.debt_service_ratio > DEBT_SERVICE_LIMIT:
        rails.append(
            Guardrail(
                code="DEBT_SERVICE_HIGH",
                severity=GuardrailSeverity.caution,
                message="Required debt payments consume an unusually large share of net income.",
                detail=f"Debt service ratio {analytics.debt_service_ratio:.0%} of net income.",
            )
        )

    if analytics.savings_rate < 0:
        rails.append(
            Guardrail(
                code="CASH_FLOW_NEGATIVE",
                severity=GuardrailSeverity.blocking,
                message=(
                    "Spending exceeds after-tax income. Any investment recommendation must address "
                    "the cash-flow gap first."
                ),
                detail=f"Savings rate {analytics.savings_rate:.1%}.",
            )
        )

    if portfolio_analytics is not None and portfolio_analytics.largest_weight > CONCENTRATION_LIMIT:
        rails.append(
            Guardrail(
                code="POSITION_CONCENTRATION",
                severity=GuardrailSeverity.caution,
                message=(
                    f"A single position exceeds {CONCENTRATION_LIMIT:.0%} of the portfolio. "
                    "Concentration risk must be addressed explicitly in the final report."
                ),
                detail=(
                    f"{portfolio_analytics.largest_holding_symbol} is "
                    f"{portfolio_analytics.largest_weight:.0%} of the portfolio."
                ),
            )
        )

    short_goals = [g for g in profile.goals if g.years_until_needed < SHORT_HORIZON_YEARS]
    if short_goals:
        names = ", ".join(g.name for g in short_goals)
        rails.append(
            Guardrail(
                code="SHORT_HORIZON_GOAL",
                severity=GuardrailSeverity.caution,
                message=(
                    f"Money needed within {SHORT_HORIZON_YEARS:.0f} years should not sit in "
                    "volatile assets."
                ),
                detail=f"Short-horizon goals: {names}.",
            )
        )

    if profile.income.employer_match_pct > 0 and analytics.savings_rate >= 0:
        rails.append(
            Guardrail(
                code="UNMATCHED_EMPLOYER_MATCH",
                severity=GuardrailSeverity.info,
                message=(
                    "An employer match is available. Capturing it is an immediate, risk-free "
                    "return and should precede other discretionary investing."
                ),
                detail=f"Match: {profile.income.employer_match_pct:.0%} of salary.",
            )
        )

    order = {
        GuardrailSeverity.blocking: 0,
        GuardrailSeverity.caution: 1,
        GuardrailSeverity.info: 2,
    }
    rails.sort(key=lambda g: (order[g.severity], g.code))
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

_VIOLATION_PATTERNS: dict[str, tuple[re.Pattern[str], ...]] = {
    "EMERGENCY_FUND_THIN": (
        re.compile(
            r"invest (all|the rest of|your entire|most of) (your |the )?(cash|savings)", re.I
        ),
        re.compile(r"deploy (all|the entire|your full) (cash|balance|reserve)", re.I),
    ),
    "HIGH_APR_DEBT": (
        re.compile(
            r"(ignore|deprioriti[sz]e|set aside|don'?t worry about) the (credit card|debt|loan)",
            re.I,
        ),
        re.compile(
            r"invest (instead of|rather than|before) paying (off|down) (the |your )?(debt|card)",
            re.I,
        ),
    ),
    "CASH_FLOW_NEGATIVE": (re.compile(r"increase (your )?(monthly )?contributions", re.I),),
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
