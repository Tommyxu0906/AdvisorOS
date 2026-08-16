"""The language-only baseline, and the boundaries it must not cross.

`test_the_prompt_cannot_carry_the_answer` and `test_abstaining_is_not_holding` are the two that
matter. The first is the information boundary at the prompt layer; the second is the difference
between a persona that declines and a persona that guesses the majority class.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.distillation.finance_nuwa.disagreement import analyse
from app.distillation.finance_nuwa.prediction import (
    BehavioralAction,
    BehavioralPrediction,
    PredictionSet,
    ReasonCode,
)
from app.distillation.finance_nuwa.task import (
    CHALLENGE_SET_RESERVED_FROM,
    INCUMBENT_CLASSES,
    is_incumbent,
    partition,
    refuse_reserved_quarters,
)
from app.distillation.finance_nuwa.vanilla_nuwa import (
    PositionState,
    PromptInputs,
    build_prompt,
    parse_prediction,
    prompt_hash,
)
from app.domain.advisor import AdvisorManifest

MANIFEST_PATH = Path(__file__).resolve().parents[2] / "config/nuwa/berkshire_public_equity.manifest.json"


def profile():
    payload = json.loads(MANIFEST_PATH.read_text())
    manifest = AdvisorManifest.model_validate(
        {k: v for k, v in payload.items() if not k.startswith("_")}
    )
    return manifest.to_runtime_profile()


def inputs(**kw) -> PromptInputs:
    base = dict(
        episode_id="e1",
        state=PositionState(security="191216100:COM", weight=0.11, rank=4, quarters_held=20),
        decision_window_start=date(2019, 4, 1),
        decision_window_end=date(2019, 6, 30),
        public_information_cutoff=date(2019, 4, 1),
    )
    base.update(kw)
    return PromptInputs(**base)


# --- the task, frozen before anyone was scored on it --------------------------------------------


def test_the_primary_task_is_the_four_incumbent_classes():
    """ENTER is not a fifth class here. Every ENTER row has all position features missing, so a
    model that answers ENTER whenever they are absent scores perfect recall on it while knowing
    nothing, and lifts the macro average for free."""
    assert INCUMBENT_CLASSES == ("exit", "reduce", "hold", "increase")
    assert "enter" not in INCUMBENT_CLASSES


def test_incumbency_is_decided_by_whether_the_position_was_visible():
    assert is_incumbent({"weight": 0.04})
    assert not is_incumbent({"weight": None})
    assert not is_incumbent({})


def test_new_to_book_rows_are_separated_rather_than_dropped():
    class Row:
        def __init__(self, features):
            self.features = features

    incumbent, new_to_book = partition([Row({"weight": 0.1}), Row({"weight": None})])
    assert len(incumbent) == 1
    assert len(new_to_book) == 1


def test_the_reserved_challenge_set_is_a_check_not_a_note():
    """The 2022-2024 held-out set has already shaped project thinking. The 2025+ reservation is
    only worth something while nobody has looked, so it fails loudly rather than documenting."""
    refuse_reserved_quarters([date(2024, 12, 31)])  # in range, fine

    with pytest.raises(ValueError, match="reserved as the prospective challenge set"):
        refuse_reserved_quarters([date(2024, 12, 31), CHALLENGE_SET_RESERVED_FROM])


# --- the information boundary at the prompt layer ------------------------------------------------


def test_the_prompt_cannot_carry_the_answer():
    """The same barrier the dataset uses, one layer up: there is no field an outcome could
    arrive in, so leaking one is a construction error rather than a review oversight."""
    assert "outcome" not in PromptInputs.model_fields
    assert "subsequent_action" not in PromptInputs.model_fields
    assert "oracle_holdings" not in PromptInputs.model_fields

    with pytest.raises(ValidationError):
        PromptInputs(
            episode_id="e1",
            state=PositionState(security="x"),
            decision_window_start=date(2019, 4, 1),
            decision_window_end=date(2019, 6, 30),
            public_information_cutoff=date(2019, 4, 1),
            outcome="it tripled",
        )


def test_the_rendered_state_states_absences_rather_than_filling_them():
    """A missing four-quarter return usually means the position is younger than the window.
    Substituting a zero would turn 'unknown' into 'flat', which is a claim the record never made."""
    rendered = inputs().state.render()

    assert "not knowable at this date" in rendered
    assert "0.0%" not in rendered


def test_the_prompt_hash_moves_when_anything_that_shapes_an_answer_moves():
    """Frozen before the held-out run, so a result carrying the old hash cannot be quoted against
    a changed prompt."""
    first = prompt_hash(profile())
    edited = profile().model_copy(update={"heuristics": ["something entirely different"]})

    assert prompt_hash(edited) != first
    assert prompt_hash(profile()) == first  # stable across identical inputs


def test_the_framework_reaches_the_prompt_and_the_episode_stays_out_of_the_cached_half():
    stable, user = build_prompt(profile(), inputs())

    assert "circle of competence" in stable.lower()
    assert "191216100" in user
    assert "191216100" not in stable  # or every episode would bust the cache


# --- abstention -----------------------------------------------------------------------------------


def test_abstaining_is_not_holding():
    """A hold claims doing nothing is right; an abstention claims the evidence cannot tell. At 68%
    hold prevalence, collapsing the two converts every 'I don't know' into a free correct answer."""
    abstained = BehavioralPrediction(episode_id="e1", abstain=True)

    assert abstained.label is None
    assert not abstained.answered


def test_claiming_both_an_action_and_an_abstention_is_rejected():
    """Recording both would let a scorer pick whichever turned out right."""
    with pytest.raises(ValidationError, match="different answers"):
        BehavioralPrediction(
            episode_id="e1", abstain=True, action=BehavioralAction.hold
        )


def test_a_parse_failure_is_neither_an_abstention_nor_an_answer():
    """Retrying until valid output came back would quietly select for the episodes the model
    finds easy."""
    failed = parse_prediction("e1", None, raw="I think Berkshire would probably hold here.")

    assert failed.parse_failed
    assert not failed.answered
    assert not failed.abstain

    predictions = PredictionSet(predictions=[failed])
    assert predictions.parse_failure_rate == 1.0
    assert predictions.abstention_rate == 0.0


def test_coverage_and_accuracy_are_reported_as_separate_facts():
    answers = PredictionSet(
        predictions=[
            BehavioralPrediction(episode_id="a", action=BehavioralAction.hold, confidence=0.8),
            BehavioralPrediction(episode_id="b", abstain=True),
            BehavioralPrediction(episode_id="c", action=BehavioralAction.reduce, confidence=0.6),
            BehavioralPrediction(episode_id="d", parse_failed=True),
        ]
    )

    assert answers.coverage == 0.5
    assert answers.abstention_rate == 0.25
    assert answers.parse_failure_rate == 0.25

    actual, predicted = answers.aligned({"a": "hold", "b": "exit", "c": "hold", "d": "hold"})
    assert actual == ["hold", "hold"]  # b and d are absent, not counted wrong
    assert predicted == ["hold", "reduce"]


def test_an_unrecognised_action_is_a_failure_rather_than_a_guess():
    assert parse_prediction("e1", {"abstain": False, "action": "trim_a_bit"}).parse_failed


def test_a_silent_answer_is_a_failure_rather_than_an_abstention():
    assert parse_prediction("e1", {"abstain": False, "action": None}).parse_failed


def test_an_unknown_reason_code_degrades_to_other_rather_than_failing_the_row():
    parsed = parse_prediction(
        "e1", {"abstain": False, "action": "hold", "reason_codes": ["vibes"]}
    )
    assert parsed.reason_codes == [ReasonCode.other]


# --- does it know something different? ------------------------------------------------------------


def test_abstentions_are_excluded_from_the_comparison_rather_than_scored_as_losses():
    """Folding coverage into accuracy is precisely what making abstention first-class avoided."""
    truth = {"a": "hold", "b": "reduce"}
    quant = {"a": "hold", "b": "hold"}
    predictions = PredictionSet(
        predictions=[
            BehavioralPrediction(episode_id="a", action=BehavioralAction.hold),
            BehavioralPrediction(episode_id="b", abstain=True),
        ]
    )

    report = analyse(truth, quant, predictions)
    assert report.compared == 1
    assert report.persona_abstained == 1
    assert report.both_correct == 1


def test_the_persona_only_cell_is_what_the_architecture_rests_on():
    truth = {"a": "reduce", "b": "hold"}
    quant = {"a": "hold", "b": "hold"}
    predictions = PredictionSet(
        predictions=[
            BehavioralPrediction(
                episode_id="a",
                action=BehavioralAction.reduce,
                reason_codes=[ReasonCode.valuation_discipline],
            ),
            BehavioralPrediction(episode_id="b", action=BehavioralAction.hold),
        ]
    )

    report = analyse(truth, quant, predictions)
    assert report.persona_only == 1
    assert report.net_persona_gain == 1
    assert report.persona_win_reasons == {"valuation_discipline": 1}


def test_scattered_wins_read_as_noise_and_concentrated_ones_do_not():
    """A persona whose wins spread evenly across every reason code is probably adding noise that
    sometimes lands. One that keeps winning for the same stated reason is contributing a
    disposition, which is a claim that can be checked."""
    truth = {str(i): "reduce" for i in range(4)}
    quant = dict.fromkeys(truth, "hold")

    codes = [ReasonCode.hold_through_drawdown] * 4
    concentrated = analyse(
        truth,
        quant,
        PredictionSet(
            predictions=[
                BehavioralPrediction(
                    episode_id=k, action=BehavioralAction.reduce, reason_codes=[c]
                )
                for k, c in zip(truth, codes, strict=True)
            ]
        ),
    )
    scattered = analyse(
        truth,
        quant,
        PredictionSet(
            predictions=[
                BehavioralPrediction(
                    episode_id=k, action=BehavioralAction.reduce, reason_codes=[c]
                )
                for k, c in zip(
                    truth,
                    [
                        ReasonCode.hold_through_drawdown,
                        ReasonCode.valuation_discipline,
                        ReasonCode.exit_discipline,
                        ReasonCode.capital_allocation,
                    ],
                    strict=True,
                )
            ]
        ),
    )

    assert concentrated.reason_concentration == 1.0
    assert scattered.reason_concentration == 0.25
    assert "worth building on" in concentrated.render()
    assert "noise that sometimes lands" in scattered.render()


# --- the policy prior ------------------------------------------------------------------------------


def test_the_policy_prior_is_not_the_household_advice_persona():
    """The built-in `buffett` advisor says an index fund is usually the right answer and refuses
    leverage on a personal balance sheet. Correct for that job, wrong for predicting what a
    $300bn book does with an existing position."""
    # Checked against what actually reaches the prompt, not against the file: the file also
    # contains the commentary explaining why the household persona was rejected, and that
    # explanation naturally quotes the very phrase being excluded.
    stable, _ = build_prompt(profile(), inputs())
    text = stable.lower()

    assert "index fund" not in text
    assert "household" not in text
    assert "circle of competence" in text
    assert "diversification is protection against ignorance" in text


def test_the_policy_prior_cannot_be_selected_by_the_product_committee():
    """It lives outside app/advisors/builtin/, so the registry never loads it."""
    builtin = Path(__file__).resolve().parents[2] / "backend/app/advisors/builtin"
    assert not (builtin / "berkshire_public_equity").exists()
    assert "config/nuwa" in str(MANIFEST_PATH)


def test_the_contamination_caveat_travels_with_the_manifest():
    """Written by a model that already knows what Berkshire did. A good score here is an upper
    bound on what language distillation could contribute, not a measurement of it."""
    payload = json.loads(MANIFEST_PATH.read_text())

    assert "UPPER BOUND" in payload["_contamination"]
    assert "does not make it forget" in payload["_contamination"]
    assert all(e["year"] < 2014 for e in payload["evidence"])


def test_the_framework_authorises_abstention_rather_than_forcing_a_view():
    """A philosophy articulated over decades is genuinely silent on most quarters, and a persona
    forced to answer 505 times produces 505 opinions whose accuracy measures the forcing."""
    payload = json.loads(MANIFEST_PATH.read_text())
    boundaries = " ".join(payload["honest_boundaries"]).lower()

    assert "abstaining is the correct output" in boundaries
