"""The layer where arithmetic overrules preference — and says that it did.

A lens can want things the numbers forbid. It can cite an action that does not exist, prefer a
candidate a blocking guardrail rules out, or oppose the one step that resolves the guardrail.
None of those are hallucinations exactly; they are opinions the constraints do not permit.

Three rules, in the order they are applied:

1. **A lens may only cite actions the engine computed.** An invented `action_id` is dropped and
   the drop is recorded. Silently keeping it would let a persona appear to endorse a trade that
   exists nowhere in the plan.

2. **A blocking guardrail cannot be preferred away.** If a lens prefers an infeasible candidate,
   the preference is moved to the best feasible one and the original is recorded verbatim. The
   preference is not erased — a reader is entitled to know the Buffett lens wanted to hold and
   was overruled by the card balance.

3. **Abstention survives everything.** A lens that declined to opine is not assigned a
   preference by default, because a default preference is a manufactured opinion.

The recorded corrections are the point. A constraint layer that silently fixed things would
produce a transcript in which every lens happens to agree with the engine, which is precisely
the impression this product should not give.
"""

from __future__ import annotations

from app.consult.models import AdvisorConsultResponse, DecisionCandidate, Stance


def apply_constraints(
    response: AdvisorConsultResponse,
    candidates: list[DecisionCandidate],
) -> AdvisorConsultResponse:
    """Return a corrected copy. Never raises — a bad response is data, not an exception."""
    index = {c.candidate_id: c for c in candidates}
    permitted = {aid for c in candidates for aid in c.action_ids}

    corrections: list[str] = []

    supported, dropped = _filter_ids(response.supported_action_ids, permitted)
    if dropped:
        corrections.append(
            f"cited {_quote(dropped)} as supported, which the engine did not compute — dropped"
        )

    opposed, dropped = _filter_ids(response.opposed_action_ids, permitted)
    if dropped:
        corrections.append(
            f"cited {_quote(dropped)} as opposed, which the engine did not compute — dropped"
        )

    preferred = response.preferred_candidate_id
    stance = response.stance

    if preferred is not None and preferred not in index:
        corrections.append(
            f"preferred {preferred!r}, which is not one of the candidates — preference cleared"
        )
        preferred = None

    if preferred is not None:
        candidate = index[preferred]
        if not candidate.feasible:
            fallback = _best_feasible(candidates)
            reason = ", ".join(candidate.blocked_by) or "it is not feasible"
            if fallback is None:
                corrections.append(
                    f"preferred {candidate.label!r}, which is blocked by {reason}, and no "
                    "feasible candidate exists — preference cleared"
                )
                preferred = None
            else:
                corrections.append(
                    f"preferred {candidate.label!r}, which is blocked by {reason}. That is not a "
                    f"consideration to weigh against others, so the preference is recorded and "
                    f"overridden to {fallback.label!r}"
                )
                preferred = fallback.candidate_id

    # An abstention that survives the above must not carry a preference — see the module note.
    if stance is Stance.abstain:
        preferred = None
        supported, opposed = [], []

    return response.model_copy(
        update={
            "supported_action_ids": supported,
            "opposed_action_ids": opposed,
            "preferred_candidate_id": preferred,
            "stance": stance,
            "corrections": [*response.corrections, *corrections],
        }
    )


def _filter_ids(ids: list[str], permitted: set[str]) -> tuple[list[str], list[str]]:
    kept = [i for i in ids if i in permitted]
    dropped = [i for i in ids if i not in permitted]
    return kept, dropped


def _best_feasible(candidates: list[DecisionCandidate]) -> DecisionCandidate | None:
    """The fallback when a preference is overruled: the first feasible candidate in order.

    Order is act, hold, alternative — so the fallback is the computed scenario when it stands,
    which is the only candidate that resolves a blocking guardrail.
    """
    for candidate in candidates:
        if candidate.feasible:
            return candidate
    return None


def _quote(ids: list[str]) -> str:
    return ", ".join(repr(i) for i in ids)


def failed_response(advisor_id: str, display_name: str, detail: str) -> AdvisorConsultResponse:
    """What a lens contributes when its output could not be read.

    A parse failure is its own state — not an abstention, and certainly not an endorsement. The
    distinction matters because a run where two lenses failed to parse and the engine proceeded
    unopposed must not read as a run where two lenses agreed.
    """
    return AdvisorConsultResponse(
        advisor_id=advisor_id,
        display_name=display_name,
        stance=Stance.abstain,
        rationale="",
        confidence_signal="low",
        declined=False,
        parse_failed=True,
        corrections=[f"response could not be read: {detail}"],
    )
