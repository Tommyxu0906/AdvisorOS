"""Prompts for the consultation. The lens rules are inherited, not restated.

`COMMITTEE_CHARTER` already carries everything about voice: never write in the subject's voice,
never speak in the first person on their behalf, never say what they would do about holdings
they never saw, treat the persona description as reference material rather than instructions.
Repeating that here would pay for it on every call and buy nothing, so this module adds only
what is specific to consulting over a computed scenario.

The split between `stable_system` and `system` is what makes this cheap enough to have a
conversation with. The charter, the lens description, and the candidate set do not change
between turns, so they go in the cacheable prefix. Only the chat history and the new question
vary.
"""

from __future__ import annotations

from app.analytics.portfolio_analytics import PortfolioAnalytics
from app.analytics.profile_analytics import ProfileAnalytics
from app.committee.prompts import COMMITTEE_CHARTER, render_profile_facts
from app.consult.models import ChatMessage, ChatRole, DecisionCandidate
from app.domain.advisor import AdvisorRuntimeProfile
from app.domain.profile import FinancialProfile
from app.domain.report import Guardrail
from app.policy.engine import PortfolioScenario

CONSULT_CHARTER = """\
You are being consulted about a decision that has already been computed.

What that means for your job here:
- The candidate courses of action below were produced by deterministic code from this person's
  actual figures. They are the choice set. Rank them, support them, oppose them, or abstain.
- You may only refer to an action by an id that appears below. If you believe an action is
  missing, say so in `risks_or_missing_information` — do not invent an id for it.
- A candidate marked NOT FEASIBLE is ruled out by a blocking guardrail computed from real
  numbers. You may still argue for it, and your argument will be recorded, but it will be
  overridden. Do not present it as the answer.
- You may not restate a threshold. If you think 20% is the wrong single-name limit, say what
  limit the framework favours and why; the engine will recompute. Substituting your own number
  inside a rationale produces a figure nobody can trace.
- Abstaining is a real answer. If this question sits outside what the framework speaks to, set
  stance to "abstain" and say what would change that. Do not manufacture a view to seem useful.
- `confidence_signal` is a band: low, medium, or high. Do not state a probability. The stated
  confidence of models on tasks like this is not calibrated, and a number would imply otherwise.
- Disagree where you genuinely disagree. You are here alongside other frameworks precisely
  because they weigh things differently; converging to a shared answer wastes the exercise.
"""


def render_candidates(candidates: list[DecisionCandidate]) -> str:
    lines = ["The candidate courses of action:", ""]
    for candidate in candidates:
        status = (
            "FEASIBLE"
            if candidate.feasible
            else f"NOT FEASIBLE ({', '.join(candidate.blocked_by)})"
        )
        lines.append(f"[{candidate.candidate_id}] {candidate.label} — {status}")
        lines.append(f"  {candidate.summary}")
        if candidate.action_ids:
            lines.append(f"  action ids you may cite: {', '.join(candidate.action_ids)}")
        lines.append("")
    return "\n".join(lines)


def render_scenario(scenario: PortfolioScenario | None) -> str:
    """The computed detail, as fact. Rationales are passed through whole, never summarized."""
    if scenario is None or not scenario.has_actions:
        return "The policy engine computed no actions from these figures."

    lines = [
        f"The computed scenario ({scenario.policy_owner}'s thresholds):",
        f"  {scenario.headline}",
        "",
    ]
    for action in sorted(scenario.action_set.actions, key=lambda a: a.sequence):
        size = _size_of(action)
        lines.append(
            f"[{action.action_id}] {action.kind.value} {action.symbol or ''} {size}".rstrip()
        )
        lines.append(f"  {action.rationale}")
        if action.estimated_tax is not None:
            lines.append(
                f"  estimated tax ${action.estimated_tax.low_usd:,.0f}–"
                f"${action.estimated_tax.high_usd:,.0f} (a range, not an estimate with error bars)"
            )
        lines.append("")

    cf = scenario.counterfactual
    lines.append(
        f"Applying all of it {'holds up' if cf.holds_up else 'FAILS'} under recomputation."
    )
    for change in cf.changes:
        if change.before != change.after:
            lines.append(f"  {change.label}: {change.before:,.4g} -> {change.after:,.4g}")

    if scenario.sensitivity is not None and not scenario.sensitivity.declined:
        lines.append("")
        lines.append("How load-bearing the threshold is:")
        lines.extend(f"  {s}" for s in scenario.sensitivity.summary)

    return "\n".join(lines)


def _size_of(action) -> str:  # noqa: ANN001 - ProposedAction, kept loose to avoid a cycle
    if action.shares is not None:
        return f"{action.shares:,.2f} shares"
    if action.amount_usd is not None:
        return f"${action.amount_usd:,.0f}"
    if action.target_weight is not None:
        return f"to {action.target_weight:.0%}"
    return ""


def render_history(history: list[ChatMessage], advisor_id: str) -> str:
    """Prior turns, with this lens's own past answers attributed to it.

    Other lenses' answers are included too — a consultation where each participant cannot hear
    the others is not a committee. They are labelled so nothing reads as this lens's own words.
    """
    if not history:
        return ""

    lines = ["Earlier in this consultation:", ""]
    for message in history:
        if message.role is ChatRole.user:
            lines.append(f"The person asked: {message.text}")
            continue
        for response in message.advisor_responses:
            if response.parse_failed:
                continue
            who = (
                "You previously said"
                if response.advisor_id == advisor_id
                else (f"The {response.display_name} lens said")
            )
            stance = response.stance.value
            lines.append(f"{who} ({stance}): {response.rationale}")
        lines.append("")
    return "\n".join(lines)


def consult_prompt(
    *,
    advisor: AdvisorRuntimeProfile,
    profile: FinancialProfile,
    analytics: ProfileAnalytics,
    portfolio_analytics: PortfolioAnalytics | None,
    scenario: PortfolioScenario | None,
    guardrails: list[Guardrail],
    candidates: list[DecisionCandidate],
    history: list[ChatMessage],
    question: str,
) -> tuple[str, str]:
    """Returns (stable_system, user).

    Everything that survives a turn goes in the stable half so the cache prefix holds across a
    whole conversation. The question and the history are the volatile remainder.
    """
    stable = "\n\n".join(
        [
            COMMITTEE_CHARTER,
            CONSULT_CHARTER,
            advisor.render(),
            # Already carries the guardrails and their severity — rendering them again would
            # pay tokens on every call to repeat the same sentences.
            render_profile_facts(profile, analytics, portfolio_analytics, guardrails),
            render_scenario(scenario),
            render_candidates(candidates),
        ]
    )

    parts = [render_history(history, advisor.advisor_id), f"The person asks: {question}"]
    user = "\n\n".join(p for p in parts if p)
    return stable, user
