"""How much of the label set rests on a number somebody chose.

`test_offsetting_changes_are_not_reported_as_stability` is the one worth reading. Class totals
that come back identical at two tolerances look like robustness and can hide a hundred individual
labels moving in both directions at once — so the comparison is per episode, never per count.
"""

from __future__ import annotations

from app.distillation.finance_nuwa.tolerance import (
    MATERIAL_FLIP_SHARE,
    TOLERANCE_SWEEP,
    ToleranceReport,
    ToleranceRow,
    compare_to_zero,
)


def test_the_sweep_includes_zero_as_its_reference_point():
    """Zero is not the neutral choice — it is the choice that calls a one-share rounding
    difference an investment decision. It is the reference precisely because it is extreme."""
    assert TOLERANCE_SWEEP[0] == 0.0
    assert TOLERANCE_SWEEP == (0.0, 0.0005, 0.0010, 0.0025, 0.0050)


def test_flips_are_reported_by_episode_id_not_only_counted():
    baseline = {"e1": "increase", "e2": "reduce", "e3": "hold"}
    candidate = {"e1": "hold", "e2": "reduce", "e3": "hold"}

    count, flipped = compare_to_zero(baseline, candidate)

    assert count == 1
    assert flipped == ["e1"]


def test_offsetting_changes_are_not_reported_as_stability():
    """Two labels moving in opposite directions leave every class total unchanged."""
    baseline = {"e1": "increase", "e2": "hold"}
    candidate = {"e1": "hold", "e2": "increase"}

    count, flipped = compare_to_zero(baseline, candidate)

    assert count == 2
    assert flipped == ["e1", "e2"]


def test_an_episode_that_exists_at_one_tolerance_and_not_the_other_counts_as_a_flip():
    count, flipped = compare_to_zero({"e1": "hold"}, {})
    assert (count, flipped) == (1, ["e1"])


def test_a_material_dependence_is_flagged_rather_than_left_to_arithmetic():
    report = ToleranceReport(
        chosen=0.005,
        rows=[
            ToleranceRow(tolerance=0.0, total_episodes=1000),
            ToleranceRow(tolerance=0.005, total_episodes=1000, flips_vs_zero=80),
        ],
    )

    assert report.chosen_row.flip_share == 0.08
    assert report.is_material
    assert "FLAG" in report.render()
    assert "must be stated wherever a score is" in report.render()


def test_a_small_dependence_says_so_plainly():
    report = ToleranceReport(
        chosen=0.005,
        rows=[
            ToleranceRow(tolerance=0.0, total_episodes=2087),
            ToleranceRow(tolerance=0.005, total_episodes=2087, flips_vs_zero=19),
        ],
    )

    assert not report.is_material
    assert report.chosen_row.flip_share < MATERIAL_FLIP_SHARE
    assert "not carrying the dataset" in report.render()


def test_a_chosen_tolerance_that_was_never_swept_is_called_out():
    report = ToleranceReport(chosen=0.02, rows=[ToleranceRow(tolerance=0.0, total_episodes=10)])
    assert report.chosen_row is None
    assert "WARNING" in report.render()


def test_the_report_carries_no_performance_number_of_any_kind():
    """Structural. Selecting the tolerance by downstream accuracy would be fitting the definition
    of the target to the predictor, and every metric would improve while meaning less."""
    fields = set(ToleranceRow.model_fields) | set(ToleranceReport.model_fields)

    assert not {f for f in fields if any(w in f for w in ("accuracy", "score", "f1", "auc"))}


def test_the_split_distribution_is_reported_at_the_chosen_value():
    report = ToleranceReport(
        chosen=0.005,
        rows=[
            ToleranceRow(tolerance=0.0, total_episodes=100),
            ToleranceRow(
                tolerance=0.005,
                total_episodes=100,
                flips_vs_zero=1,
                train_counts={"hold": 40, "reduce": 10},
                validation_counts={"hold": 20},
                held_out_counts={"hold": 30},
            ),
        ],
    )
    rendered = report.render()

    assert "train       hold 40  reduce 10" in rendered
    assert "validation  hold 20" in rendered
    assert "held out    hold 30" in rendered
