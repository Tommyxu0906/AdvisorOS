"""The floor a persona has to beat, and the properties that keep it an honest floor.

Two failures would make every later comparison meaningless in opposite directions.
`test_missing_features_are_flagged_rather_than_imputed` guards against telling the model that a
brand-new position was averagely sized. `test_balanced_weighting_stops_the_fit_collapsing_to_the
_majority_class` guards against shipping a strawman: an unweighted fit at 69% hold prevalence
predicts hold for everything, and a persona that beats *that* has beaten nothing.
"""

from __future__ import annotations

from datetime import date

from app.distillation.finance_nuwa.artifact import EpisodeRow
from app.distillation.finance_nuwa.baselines import (
    FEATURE_SETS,
    AlwaysHold,
    ClassPrior,
    GradientBoostedTrees,
    MultinomialLogistic,
    Scaler,
    class_weights,
    encode,
)
from app.distillation.finance_nuwa.evaluation import macro_f1


def row(action: str, *, weight: float | None = 0.05, rank: int | None = 10, held=4) -> EpisodeRow:
    return EpisodeRow(
        episode_id=f"e-{action}-{weight}-{rank}-{held}-{id(action) % 97}",
        target_id="berkshire_public_equity",
        security="037833100:COM",
        security_cusip="037833100",
        security_title_of_class="COM",
        observed_action=action,
        magnitude="none",
        action_basis="share_count",
        attribution_basis="entity_filing",
        attribution_confidence=0.5,
        training_weight=0.5,
        decision_window_start=date(2016, 1, 1),
        decision_window_end=date(2016, 3, 31),
        public_information_cutoff=date(2016, 1, 1),
        replay_view="public_observer",
        features={"weight": weight, "rank": rank, "quarters_held": held},
        split="train",
    )


def separable(n: int = 60) -> list[EpisodeRow]:
    """Large long-held positions are held; small new ones are traded. Learnable on purpose."""
    rows = []
    for i in range(n):
        rows.append(row("hold", weight=0.10 + i * 0.001, rank=3, held=20))
        rows.append(row("reduce", weight=0.001 + i * 0.0001, rank=40, held=1))
    return rows


# --- encoding ------------------------------------------------------------------------------------


def test_missing_features_are_flagged_rather_than_imputed():
    """Imputing the median would tell the model a brand-new position was averagely sized, which
    is false for all 85 ENTER episodes at once."""
    design = encode([row("enter", weight=None, rank=None, held=None)], "position")

    assert design.columns == [
        "weight",
        "weight_missing",
        "rank",
        "rank_missing",
        "quarters_held",
        "quarters_held_missing",
    ]
    assert design.rows[0] == [0.0, 1.0, 0.0, 1.0, 0.0, 1.0]


def test_a_present_value_carries_a_zero_indicator():
    design = encode([row("hold", weight=0.25, rank=2, held=8)], "position")
    assert design.rows[0] == [0.25, 0.0, 2.0, 0.0, 8.0, 0.0]


def test_the_feature_sets_are_nested_so_an_ablation_row_is_attributable():
    sets = list(FEATURE_SETS.values())
    for smaller, larger in zip(sets, sets[1:], strict=False):
        assert set(smaller) < set(larger)


# --- no leakage through the scaler -----------------------------------------------------------------


def test_the_scaler_is_fitted_on_training_rows_only():
    """Fitting on all rows is a real leak — small, almost invisible, and exactly the kind that
    survives review."""
    train = [[0.0], [2.0]]
    scaler = Scaler.fit(train)

    assert scaler.means == [1.0]
    # Applying to unseen rows uses the training statistics, never the new rows'.
    assert scaler.apply([[4.0]])[0][0] == 3.0


def test_a_constant_column_does_not_divide_by_zero():
    scaler = Scaler.fit([[5.0], [5.0]])
    assert scaler.apply([[5.0]])[0][0] == 0.0


# --- the floor -------------------------------------------------------------------------------------


def test_always_hold_answers_hold_whatever_it_is_shown():
    design = encode([row("reduce"), row("enter", weight=None)], "position")
    prediction = AlwaysHold().fit(design).predict(design)

    assert prediction.predicted == ["hold", "hold"]
    assert prediction.probabilities[0]["hold"] == 1.0


def test_the_class_prior_reports_frequencies_rather_than_a_constant_one():
    """Its argmax is the same constant, but its probabilities are the honest base rates — so it
    is the reference any calibration claim has to beat."""
    train = encode([row("hold")] * 7 + [row("reduce")] * 3, "position")
    model = ClassPrior().fit(train)
    prediction = model.predict(train)

    assert prediction.probabilities[0]["hold"] == 0.7
    assert prediction.probabilities[0]["reduce"] == 0.3
    assert set(prediction.predicted) == {"hold"}


# --- class weighting -------------------------------------------------------------------------------


def test_balanced_weights_are_inverse_frequency_and_unbalanced_are_flat():
    labels = ["hold"] * 90 + ["reduce"] * 10

    balanced = class_weights(labels, balanced=True)
    flat = class_weights(labels, balanced=False)

    assert balanced["reduce"] > balanced["hold"]
    assert balanced["hold"] * 90 == balanced["reduce"] * 10
    assert flat["hold"] == flat["reduce"] == 1.0
    assert balanced["enter"] == 0.0  # absent from the labels, so it contributes nothing


def test_balanced_weighting_stops_the_fit_collapsing_to_the_majority_class():
    """Unweighted at this prevalence the model answers hold for everything and scores the same
    macro F1 as the constant baseline. Reporting that as 'what a quant model can do' would be
    building a strawman for a persona to beat later."""
    # Overlapping, not separable: the reduce rows sit inside the hold rows' range, so the only
    # way to recover them is to stop letting the 90:10 prior decide everything. A cleanly
    # separable fixture would pass without weighting and prove nothing.
    rows = [
        row("hold", weight=0.02 + 0.02 * (i % 4), rank=3 + (i % 4), held=20) for i in range(90)
    ] + [row("reduce", weight=0.02 + 0.02 * (i % 2), rank=3 + (i % 2), held=20) for i in range(10)]
    design = encode(rows, "position")

    unweighted = MultinomialLogistic(balanced=False, iterations=200).fit(design)
    weighted = MultinomialLogistic(balanced=True, iterations=200).fit(design)

    assert set(unweighted.predict(design).predicted) == {"hold"}
    assert "reduce" in set(weighted.predict(design).predicted)


# --- the models actually fit ------------------------------------------------------------------------


def test_logistic_learns_a_separable_rule():
    design = encode(separable(), "position")
    model = MultinomialLogistic(iterations=300).fit(design)
    prediction = model.predict(design)

    assert macro_f1(design.labels, prediction.predicted) > 0.9


def test_boosting_learns_a_separable_rule():
    design = encode(separable(), "position")
    model = GradientBoostedTrees(rounds=30).fit(design)
    prediction = model.predict(design)

    assert macro_f1(design.labels, prediction.predicted) > 0.9


def test_probabilities_are_a_distribution_over_every_class():
    design = encode(separable(20), "position")
    for model in (MultinomialLogistic(iterations=50), GradientBoostedTrees(rounds=5)):
        prediction = model.fit(design).predict(design)
        for probs in prediction.probabilities:
            assert abs(sum(probs.values()) - 1.0) < 1e-6
            assert set(probs) >= {"hold", "reduce"}


def test_fitting_is_deterministic_so_a_config_hash_means_something():
    """No random initialisation, no subsampling, no shuffling. The same data gives the same
    coefficients, which is what lets a held-out result be checked later."""
    design = encode(separable(20), "position")

    first = MultinomialLogistic(iterations=50).fit(design).predict(design)
    second = MultinomialLogistic(iterations=50).fit(design).predict(design)
    assert first.probabilities == second.probabilities

    third = GradientBoostedTrees(rounds=5).fit(design).predict(design)
    fourth = GradientBoostedTrees(rounds=5).fit(design).predict(design)
    assert third.probabilities == fourth.probabilities


def test_an_empty_training_set_does_not_crash_the_fit():
    empty = encode([], "position")
    assert MultinomialLogistic().fit(empty).predict(empty).predicted == []
    assert GradientBoostedTrees().fit(empty).predict(empty).predicted == []
