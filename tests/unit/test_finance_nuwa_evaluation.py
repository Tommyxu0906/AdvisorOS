"""The rules a behavioural result has to be reported under.

`test_a_constant_model_scores_the_prevalence_and_macro_f1_exposes_it` is the reason this module
exists. At 69% hold prevalence, "69% accurate" and "learned nothing" are the same sentence, and a
report that leads with accuracy has said the second while sounding like the first.
"""

from __future__ import annotations

import pytest

from app.distillation.finance_nuwa.evaluation import (
    CLASSES,
    Calibration,
    Evaluation,
    PairedComparison,
    balanced_accuracy,
    brier_score,
    calibration,
    class_metrics,
    compare_information_sets,
    confusion_matrix,
    evaluate,
    log_loss,
    macro_f1,
)


def constant(labels: list[str], answer: str = "hold") -> list[str]:
    return [answer] * len(labels)


# --- the reason accuracy is not reportable alone -----------------------------------------------


def test_a_constant_model_scores_the_prevalence_and_macro_f1_exposes_it():
    actual = ["hold"] * 69 + ["reduce"] * 20 + ["increase"] * 11
    result = evaluate(
        actual,
        constant(actual),
        model_name="always_hold",
        view="natural",
        split="held_out",
        with_intervals=False,
    )

    assert result.accuracy == 0.69
    assert result.majority_class_rate == 0.69
    # ...and the metric that is actually reportable says it plainly.
    assert result.macro_f1 == pytest.approx(0.272, abs=1e-3)
    assert result.balanced_accuracy == pytest.approx(1 / 3, abs=1e-3)


def test_macro_f1_averages_only_over_classes_the_truth_contains():
    """Scoring a model on a class the split never asked about penalises it for a question that
    was not put. With 28 held-out ENTER episodes the difference is not academic."""
    actual = ["hold"] * 3 + ["reduce"] * 2
    assert macro_f1(actual, actual) == 1.0
    assert balanced_accuracy(actual, actual) == 1.0


def test_perfect_and_useless_predictions_bracket_the_range():
    actual = ["hold", "reduce", "increase", "exit", "enter"]
    assert macro_f1(actual, actual) == 1.0
    assert macro_f1(actual, constant(actual)) == pytest.approx(0.0667, abs=1e-3)


# --- per class ----------------------------------------------------------------------------------


def test_precision_and_recall_are_computed_per_class_not_pooled():
    actual = ["hold", "hold", "reduce", "reduce"]
    predicted = ["hold", "reduce", "reduce", "hold"]

    hold = class_metrics(actual, predicted, "hold")
    assert (hold.support, hold.predicted) == (2, 2)
    assert hold.precision == 0.5
    assert hold.recall == 0.5


def test_a_class_that_is_never_predicted_scores_zero_rather_than_erroring():
    actual = ["hold", "enter"]
    metric = class_metrics(actual, ["hold", "hold"], "enter")
    assert (metric.precision, metric.recall, metric.f1) == (0.0, 0.0, 0.0)


def test_the_confusion_matrix_covers_every_class_in_a_fixed_order():
    matrix = confusion_matrix(["hold"], ["reduce"])
    assert list(matrix) == list(CLASSES)
    assert matrix["hold"]["reduce"] == 1
    assert set(matrix["hold"]) == set(CLASSES)


# --- thin classes need intervals ------------------------------------------------------------------


def test_a_thin_class_gets_a_wide_interval_and_a_dense_one_gets_a_narrow_one():
    """85 ENTER episodes across eleven years. Quoting an F1 on 28 held-out examples to three
    decimals implies a precision the sample cannot support."""
    actual = ["hold"] * 200 + ["enter"] * 8
    predicted = ["hold"] * 200 + ["enter"] * 4 + ["hold"] * 4

    result = evaluate(actual, predicted, model_name="m", view="natural", split="held_out")
    by_label = {m.label: m for m in result.per_class}

    assert by_label["enter"].interval_width > by_label["hold"].interval_width


def test_intervals_are_seeded_so_a_reported_number_is_reproducible():
    actual = ["hold"] * 50 + ["reduce"] * 20
    predicted = ["hold"] * 60 + ["reduce"] * 10
    kw = dict(model_name="m", view="natural", split="held_out")

    first = evaluate(actual, predicted, **kw)
    second = evaluate(actual, predicted, **kw)

    assert first.macro_f1_low == second.macro_f1_low
    assert first.macro_f1_high == second.macro_f1_high


# --- probabilities ---------------------------------------------------------------------------------


def test_log_loss_punishes_confident_and_wrong_more_than_uncertain_and_wrong():
    actual = ["hold"]
    confident_wrong = [{"hold": 0.01, "reduce": 0.99}]
    uncertain = [{"hold": 0.4, "reduce": 0.6}]

    assert log_loss(actual, confident_wrong) > log_loss(actual, uncertain)


def test_brier_is_zero_for_a_perfect_forecast_and_two_for_a_perfectly_wrong_one():
    perfect = [{c: (1.0 if c == "hold" else 0.0) for c in CLASSES}]
    inverted = [{c: (1.0 if c == "reduce" else 0.0) for c in CLASSES}]

    assert brier_score(["hold"], perfect) == 0.0
    assert brier_score(["hold"], inverted) == 2.0


def test_calibration_records_which_view_produced_the_probability():
    """A probability fitted on the matched view was fitted under a class prior that was changed
    on purpose, so it is not a real-world action frequency and the field says so."""
    probs = [{"hold": 0.8, "reduce": 0.2}] * 10
    natural = calibration(["hold"] * 10, probs, view="berkshire-v2.0-natural")
    matched = calibration(["hold"] * 10, probs, view="berkshire-v2.0")

    assert natural.is_deployable_prior
    assert not matched.is_deployable_prior


def test_an_overconfident_model_shows_a_negative_gap():
    probs = [{"hold": 0.9, "reduce": 0.1}] * 10
    actual = ["hold"] * 5 + ["reduce"] * 5

    result = calibration(actual, probs, view="natural")

    assert result.bins[0].mean_confidence == pytest.approx(0.9)
    assert result.bins[0].accuracy == pytest.approx(0.5)
    assert result.bins[0].gap < 0
    assert result.expected_calibration_error == pytest.approx(0.4, abs=1e-3)


# --- two information sets, never averaged ------------------------------------------------------------


def test_the_paired_comparison_separates_lag_from_policy_error():
    actual = {"a": "hold", "b": "reduce", "c": "increase", "d": "exit"}
    public = {"a": "hold", "b": "hold", "c": "hold", "d": "hold"}
    oracle = {"a": "hold", "b": "reduce", "c": "hold", "d": "increase"}

    paired = compare_information_sets(actual, public, oracle, model_name="m", split="held_out")

    assert paired.both_correct == 1  # a
    assert paired.public_wrong_oracle_correct == 1  # b: the lag was the whole problem
    assert paired.both_wrong == 2  # c, d: information does not fix these
    assert paired.information_lag_share == pytest.approx(1 / 3, abs=1e-3)


def test_the_comparison_only_uses_episodes_present_in_both():
    actual = {"a": "hold", "b": "reduce"}
    paired = compare_information_sets(
        actual, {"a": "hold"}, {"a": "hold", "b": "reduce"}, model_name="m", split="held_out"
    )
    assert paired.n == 1


def test_nothing_in_the_module_blends_the_two_scores_into_one():
    """Structural. A weak deployable result averaged with a strong research upper bound would
    report a number that describes neither."""
    fields = set(PairedComparison.model_fields) | set(Evaluation.model_fields)
    assert not {f for f in fields if "combined" in f or "overall" in f or "blended" in f}
    assert "public_macro_f1" in PairedComparison.model_fields
    assert "oracle_macro_f1" in PairedComparison.model_fields


def test_an_empty_evaluation_does_not_divide_by_zero():
    result = evaluate([], [], model_name="m", view="v", split="s")
    assert result.n == 0
    assert result.macro_f1 == 0.0
    assert result.majority_class_rate == 0.0
    assert Calibration(view="v", log_loss=0.0, brier=0.0, expected_calibration_error=0.0).bins == []
