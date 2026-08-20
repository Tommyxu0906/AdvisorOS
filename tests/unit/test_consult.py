"""The consultation: candidates, the constraint layer, and what must never happen.

The tests that matter most here are the negative ones. A lens supporting an action that does not
exist, or preferring a course a blocking guardrail forbids, is not an exotic failure — it is the
ordinary behaviour of a language model asked for an opinion, and the constraint layer is the only
thing standing between that and a recommendation.
"""

from __future__ import annotations

import pytest

from app.consult.candidates import ACT_ID, ALTERNATIVE_ID, HOLD_ID, build_candidates
from app.consult.constraints import apply_constraints, failed_response
from app.consult.models import (
    AdvisorConsultResponse,
    CandidateKind,
    ChatMessage,
    ChatRole,
    ConfidenceSignal,
    DecisionCandidate,
    Stance,
)
from app.consult.service import parse_response, synthesize
from app.domain.report import Guardrail


def guardrail(code: str, severity: str = "blocking") -> Guardrail:
    return Guardrail(code=code, severity=severity, message=f"{code} message", detail="")


def candidate(cid: str, *, feasible: bool = True, blocked: list[str] | None = None, actions=()):
    return DecisionCandidate(
        candidate_id=cid,
        kind=CandidateKind.act if cid == ACT_ID else CandidateKind.hold,
        label=f"label {cid}",
        summary="summary",
        action_ids=list(actions),
        feasible=feasible,
        blocked_by=list(blocked or []),
    )


def response(**kwargs) -> AdvisorConsultResponse:
    base = {
        "advisor_id": "buffett",
        "display_name": "Warren Buffett",
        "stance": Stance.endorse,
        "confidence_signal": ConfidenceSignal.medium,
    }
    return AdvisorConsultResponse(**{**base, **kwargs})


class _Lens:
    """Minimal stand-in for an AdvisorRuntimeProfile — parse_response only reads two fields."""

    def __init__(self, advisor_id="buffett", display_name="Warren Buffett"):
        self.advisor_id = advisor_id
        self.display_name = display_name


# --- candidates --------------------------------------------------------------------------


def test_hold_is_offered_but_blocked_when_a_guardrail_forbids_it(scenario_with_actions):
    """A lens must be able to argue for it and be recorded as overruled, not silently denied."""
    candidates = build_candidates(scenario_with_actions, [guardrail("HIGH_APR_DEBT")])
    hold = next(c for c in candidates if c.candidate_id == HOLD_ID)
    assert hold.feasible is False
    assert hold.blocked_by == ["HIGH_APR_DEBT"]


def test_hold_is_feasible_when_nothing_blocks(scenario_with_actions):
    candidates = build_candidates(scenario_with_actions, [guardrail("SOFT", severity="caution")])
    hold = next(c for c in candidates if c.candidate_id == HOLD_ID)
    assert hold.feasible is True
    assert hold.blocked_by == []


def test_a_blocked_candidate_cannot_claim_to_be_feasible():
    with pytest.raises(ValueError, match="cannot be both"):
        DecisionCandidate(
            candidate_id="x",
            kind=CandidateKind.hold,
            label="x",
            summary="x",
            feasible=True,
            blocked_by=["SOMETHING"],
        )


def test_the_threshold_alternative_appears_only_when_the_sweep_found_a_flip(scenario_with_actions):
    candidates = build_candidates(scenario_with_actions, [])
    ids = {c.candidate_id for c in candidates}
    sweep = scenario_with_actions.sensitivity
    if sweep is not None and sweep.flip_at is not None and not sweep.declined:
        assert ALTERNATIVE_ID in ids
    else:
        assert ALTERNATIVE_ID not in ids


# --- the constraint layer ----------------------------------------------------------------


def test_an_advisor_cannot_support_an_action_that_does_not_exist():
    candidates = [candidate(ACT_ID, actions=["trim_nvda"]), candidate(HOLD_ID)]
    out = apply_constraints(
        response(supported_action_ids=["trim_nvda", "sell_everything"]), candidates
    )
    assert out.supported_action_ids == ["trim_nvda"]
    assert any("sell_everything" in c for c in out.corrections)


def test_an_advisor_cannot_oppose_an_action_that_does_not_exist():
    candidates = [candidate(ACT_ID, actions=["trim_nvda"]), candidate(HOLD_ID)]
    out = apply_constraints(response(opposed_action_ids=["invented"]), candidates)
    assert out.opposed_action_ids == []
    assert out.was_corrected


def test_a_blocking_guardrail_cannot_be_preferred_away():
    """The central rule. The preference is honoured as a record and overridden as an outcome."""
    candidates = [
        candidate(ACT_ID, actions=["trim_nvda"]),
        candidate(HOLD_ID, feasible=False, blocked=["HIGH_APR_DEBT"]),
    ]
    out = apply_constraints(
        response(stance=Stance.oppose, preferred_candidate_id=HOLD_ID), candidates
    )
    assert out.preferred_candidate_id == ACT_ID
    assert out.stance is Stance.oppose, "the opposition itself must survive being overruled"
    assert any("HIGH_APR_DEBT" in c for c in out.corrections)


def test_a_preference_for_a_candidate_that_does_not_exist_is_cleared():
    out = apply_constraints(
        response(preferred_candidate_id="nonsense"), [candidate(ACT_ID), candidate(HOLD_ID)]
    )
    assert out.preferred_candidate_id is None
    assert out.was_corrected


def test_abstention_is_preserved_and_carries_no_preference():
    out = apply_constraints(
        response(stance=Stance.abstain, supported_action_ids=["trim_nvda"]),
        [candidate(ACT_ID, actions=["trim_nvda"])],
    )
    assert out.stance is Stance.abstain
    assert out.preferred_candidate_id is None
    assert out.supported_action_ids == []


def test_recording_both_an_abstention_and_a_preference_is_rejected():
    with pytest.raises(ValueError, match="absence of a view"):
        AdvisorConsultResponse(
            advisor_id="a",
            display_name="A",
            stance=Stance.abstain,
            preferred_candidate_id=ACT_ID,
        )


# --- parsing -----------------------------------------------------------------------------


def test_a_malformed_response_fails_to_an_explicit_parse_failure():
    out = parse_response("not json at all", _Lens())
    assert out.parse_failed is True
    assert out.stance is Stance.abstain
    assert out.corrections


def test_an_unknown_stance_is_a_parse_failure_not_a_guess():
    out = parse_response('{"stance": "enthusiastic"}', _Lens())
    assert out.parse_failed is True


def test_an_unknown_confidence_band_degrades_without_failing_the_response():
    out = parse_response(
        '{"stance": "endorse", "confidence_signal": "certain", "rationale": "x"}', _Lens()
    )
    assert out.parse_failed is False
    assert out.confidence_signal is ConfidenceSignal.low


def test_a_parse_failure_is_not_counted_as_agreement():
    """Two unreadable answers and an unopposed engine must not read as two lenses agreeing."""
    candidates = [candidate(ACT_ID, actions=["trim_nvda"])]
    failures = [
        failed_response("buffett", "Warren Buffett", "bad json"),
        failed_response("munger", "Charlie Munger", "bad json"),
    ]
    synthesis = synthesize(failures, candidates)
    assert synthesis.endorsing == []
    assert synthesis.abstaining == [], "a parse failure is not an abstention either"
    # The headline must rest on the computation and must not imply anyone backed it.
    assert "computation alone" in synthesis.headline
    assert "supported by" not in synthesis.headline


# --- synthesis ---------------------------------------------------------------------------


def test_disagreement_between_two_lenses_is_reported_not_averaged():
    candidates = [candidate(ACT_ID, actions=["trim_nvda"]), candidate(HOLD_ID)]
    responses = [
        response(stance=Stance.endorse, preferred_candidate_id=ACT_ID),
        response(
            advisor_id="munger",
            display_name="Charlie Munger",
            stance=Stance.oppose,
            preferred_candidate_id=HOLD_ID,
        ),
    ]
    synthesis = synthesize(responses, candidates)
    assert "Warren Buffett" in synthesis.endorsing
    assert "Charlie Munger" in synthesis.opposing


def test_the_selected_candidate_is_always_feasible():
    candidates = [
        candidate(ACT_ID, actions=["trim_nvda"]),
        candidate(HOLD_ID, feasible=False, blocked=["HIGH_APR_DEBT"]),
    ]
    constrained = [
        apply_constraints(
            response(stance=Stance.oppose, preferred_candidate_id=HOLD_ID), candidates
        ),
        apply_constraints(
            response(
                advisor_id="munger",
                display_name="Charlie Munger",
                stance=Stance.oppose,
                preferred_candidate_id=HOLD_ID,
            ),
            candidates,
        ),
    ]
    synthesis = synthesize(constrained, candidates)
    selected = next(c for c in candidates if c.candidate_id == synthesis.selected_candidate_id)
    assert selected.feasible, "both lenses wanted the blocked option and it still cannot be chosen"
    assert synthesis.overrides, "and the override must be reported rather than hidden"


def test_two_lenses_stay_separate_in_the_output():
    candidates = [candidate(ACT_ID, actions=["trim_nvda"])]
    responses = [
        response(advisor_id="buffett", display_name="Warren Buffett"),
        response(advisor_id="munger", display_name="Charlie Munger", stance=Stance.oppose),
    ]
    assert len({r.advisor_id for r in responses}) == 2
    synthesis = synthesize(responses, candidates)
    assert synthesis.endorsing == ["Warren Buffett"]
    assert synthesis.opposing == ["Charlie Munger"]


# --- history -----------------------------------------------------------------------------


def test_chat_history_keeps_its_order():
    from app.consult.prompts import render_history

    history = [
        ChatMessage(role=ChatRole.user, text="first question"),
        ChatMessage(
            role=ChatRole.committee,
            advisor_responses=[response(rationale="first answer")],
        ),
        ChatMessage(role=ChatRole.user, text="second question"),
    ]
    rendered = render_history(history, "buffett")
    assert rendered.index("first question") < rendered.index("first answer")
    assert rendered.index("first answer") < rendered.index("second question")


def test_a_lens_sees_its_own_prior_answers_as_its_own():
    from app.consult.prompts import render_history

    history = [
        ChatMessage(
            role=ChatRole.committee,
            advisor_responses=[
                response(advisor_id="buffett", display_name="Warren Buffett", rationale="mine"),
                response(advisor_id="munger", display_name="Charlie Munger", rationale="theirs"),
            ],
        )
    ]
    rendered = render_history(history, "buffett")
    assert "You previously said" in rendered
    assert "The Charlie Munger lens said" in rendered


def test_an_unreadable_prior_answer_is_left_out_of_the_history():
    from app.consult.prompts import render_history

    history = [
        ChatMessage(
            role=ChatRole.committee,
            advisor_responses=[failed_response("buffett", "Warren Buffett", "bad json")],
        )
    ]
    assert "Warren Buffett" not in render_history(history, "munger")
