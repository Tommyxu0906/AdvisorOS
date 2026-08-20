"""Turn one computed scenario into the smallest honest set of things to choose between.

The engine computes *one* scenario, not a menu. Rather than build an optimizer to manufacture
alternatives — which would mean inventing portfolios nobody costed — this derives only what the
existing outputs already support:

    act                    the computed action set, exactly as the engine produced it
    hold                   change nothing, offered only when no blocking guardrail forbids it
    alternative_threshold  what the same holdings imply on the other side of the sweep's flip
                           point, which the sensitivity module already computed

Three candidates at most, every one of them backed by arithmetic that already ran.

**`hold` is where the constraint layer earns its place.** Doing nothing is a legitimate option
right up until a blocking guardrail says otherwise — 22.9% card debt against a thin reserve is
not a matter of taste. When that is the situation, `hold` is still *offered to the lenses* but
carries `blocked_by`, so a lens that prefers it is making a recorded, overridable choice rather
than being silently denied the option. Hiding it would make the transcript dishonest: the lens
never got to say what it thought.
"""

from __future__ import annotations

from app.consult.models import CandidateKind, DecisionCandidate
from app.domain.report import Guardrail
from app.policy.engine import PortfolioScenario

ACT_ID = "act"
HOLD_ID = "hold"
ALTERNATIVE_ID = "alternative_threshold"


def build_candidates(
    scenario: PortfolioScenario | None,
    guardrails: list[Guardrail],
) -> list[DecisionCandidate]:
    """The choice set the lenses rank. Ordered: act, hold, then any threshold alternative."""
    blocking = [g.code for g in guardrails if g.severity == "blocking"]
    candidates: list[DecisionCandidate] = []

    if scenario is not None and scenario.has_actions:
        action_ids = [a.action_id for a in scenario.action_set.actions]
        candidates.append(
            DecisionCandidate(
                candidate_id=ACT_ID,
                kind=CandidateKind.act,
                label="Carry out the computed scenario",
                summary=_act_summary(scenario),
                action_ids=action_ids,
                # An action set that fails its own counterfactual is a policy bug, and offering
                # it as a choice would ask the lenses to rank something arithmetic rejected.
                feasible=scenario.counterfactual.holds_up,
                blocked_by=[] if scenario.counterfactual.holds_up else ["scenario_infeasible"],
            )
        )

    candidates.append(
        DecisionCandidate(
            candidate_id=HOLD_ID,
            kind=CandidateKind.hold,
            label="Change nothing for now",
            summary=_hold_summary(blocking),
            action_ids=[],
            feasible=not blocking,
            blocked_by=list(blocking),
        )
    )

    alternative = _threshold_alternative(scenario)
    if alternative is not None:
        candidates.append(alternative)

    return candidates


def _act_summary(scenario: PortfolioScenario) -> str:
    actions = scenario.action_set.actions
    trims = [a for a in actions if a.kind.value == "trim_position"]
    others = len(actions) - len(trims)

    parts = []
    if trims:
        names = ", ".join(a.symbol or "?" for a in trims[:3])
        more = f" and {len(trims) - 3} more" if len(trims) > 3 else ""
        parts.append(f"reduce {names}{more}")
    if others:
        parts.append(f"{others} further step{'s' if others != 1 else ''} from the house rules")

    body = "; ".join(parts) if parts else "the computed steps"
    return f"{body}. {scenario.headline}"


def _hold_summary(blocking: list[str]) -> str:
    if not blocking:
        return (
            "Leave the portfolio as it is. Nothing computed forbids this — the thresholds imply "
            "a change, and implying is not requiring."
        )
    return (
        "Not available here. A blocking guardrail was computed from these figures "
        f"({', '.join(blocking)}), and it is not a consideration to be weighed against others. "
        "A lens may still argue for it, and the record will show the argument was overruled."
    )


def _threshold_alternative(scenario: PortfolioScenario | None) -> DecisionCandidate | None:
    """The other side of the sweep's flip point — already computed, never re-derived here.

    Only offered when the sweep found a reversal. Absent a flip point there is no alternative
    threshold to speak of, and inventing one would mean claiming a conclusion nobody computed.
    """
    if scenario is None or scenario.sensitivity is None:
        return None
    sweep = scenario.sensitivity
    if sweep.declined or sweep.flip_at is None:
        return None

    return DecisionCandidate(
        candidate_id=ALTERNATIVE_ID,
        kind=CandidateKind.alternative_threshold,
        label=f"Apply a {sweep.flip_at:.0%} threshold instead",
        summary=(
            f"The threshold in use is {sweep.baseline:.0%}. At {sweep.flip_at:.0%} and above, "
            "the same holdings imply holding rather than trimming. This candidate is that "
            "reading — a different tolerance for concentration, not a different portfolio."
            + (
                " The sweep flags this conclusion as fragile: a threshold a reasonable person "
                "might pick instead reverses it."
                if sweep.fragile
                else ""
            )
        ),
        action_ids=[],
        feasible=True,
    )


def candidate_index(candidates: list[DecisionCandidate]) -> dict[str, DecisionCandidate]:
    return {c.candidate_id: c for c in candidates}


def known_action_ids(candidates: list[DecisionCandidate]) -> set[str]:
    """Every action id a lens is permitted to cite. Anything else is an invention."""
    return {action_id for c in candidates for action_id in c.action_ids}
