"""A dataset that can support a claim, or one that quietly cannot.

Three tests carry this file. `test_a_dataset_of_only_trades_would_teach_constant_trading` is the
reason hold episodes exist at all. `test_a_quiet_hold_in_a_tiny_position_is_dropped` is the
reason they are sampled rather than kept wholesale. And
`test_the_held_out_set_cannot_be_reached_while_refining` is the one that decides whether any
number this project reports is worth printing.
"""

from __future__ import annotations

from datetime import date

import pytest

from app.distillation.finance_nuwa.dataset import (
    MAX_HOLD_RATIO,
    EpisodeDataset,
    MagnitudeBucket,
    Split,
    bucket_magnitude,
    score_hold,
    select_holds,
)
from app.distillation.finance_nuwa.disclosure import DisclosureScope
from app.distillation.finance_nuwa.drift import (
    ActionBasis,
    ActionClassification,
    ObservedAction,
)
from app.distillation.finance_nuwa.episode import DecisionEpisode, EpisodeInputs


def episode(
    episode_id: str,
    *,
    when: date,
    action: ObservedAction = ObservedAction.increase,
    basis: ActionBasis = ActionBasis.share_count,
    confidence: float = 1.0,
) -> DecisionEpisode:
    window_start = date(when.year, ((when.month - 1) // 3) * 3 + 1, 1)
    return DecisionEpisode(
        advisor_id="buffett",
        episode_id=episode_id,
        inputs=EpisodeInputs(as_of=window_start, symbol="AAPL"),
        observed_action=action,
        action_basis=basis,
        attribution_confidence=confidence,
        decision_window_start=window_start,
        decision_window_end=when,
    )


def classification(symbol: str, **kw) -> ActionClassification:
    base = dict(symbol=symbol, action=ObservedAction.hold, basis=ActionBasis.share_count)
    base.update(kw)
    return ActionClassification(**base)


# --- magnitude is a bucket, not a number ------------------------------------------------------


@pytest.mark.parametrize(
    ("fraction", "expected"),
    [
        (0.03, MagnitudeBucket.small),
        (0.25, MagnitudeBucket.medium),
        (0.80, MagnitudeBucket.large),
        (-0.25, MagnitudeBucket.medium),  # direction lives on the action, not the size
    ],
)
def test_a_trade_is_bucketed_by_the_share_of_the_position_it_moved(fraction, expected):
    assert bucket_magnitude(ObservedAction.reduce, fraction) is expected


def test_opening_and_closing_are_always_full():
    assert bucket_magnitude(ObservedAction.enter, None) is MagnitudeBucket.full
    assert bucket_magnitude(ObservedAction.exit, 1.0) is MagnitudeBucket.full


def test_a_hold_has_no_magnitude():
    assert bucket_magnitude(ObservedAction.hold, None) is MagnitudeBucket.none


def test_an_unknown_fraction_is_unknown_rather_than_small():
    """`small` is a claim, and the wrong one: a trade with no measurable fraction is as likely to
    have been large. Encoding it as small teaches a bias that exists only in the encoding."""
    assert bucket_magnitude(ObservedAction.reduce, None) is MagnitudeBucket.unknown


# --- which holds are evidence ------------------------------------------------------------------


def test_a_quiet_hold_in_a_tiny_position_is_dropped():
    """Keeping these lets a classifier answer 'hold' unconditionally and look excellent."""
    quiet = score_hold(classification("XYZ", end_weight=0.003), period_return=0.01)

    assert not quiet.is_informative
    assert quiet.score == 0.0
    assert quiet.reasons == []


def test_a_hold_through_a_crash_in_a_big_position_is_kept():
    """Sitting still through a 40% drawdown in a top holding is a decision someone made."""
    tested = score_hold(classification("KO", end_weight=0.18), period_return=-0.40)

    assert tested.is_informative
    assert any("fell 40%" in r for r in tested.reasons)
    assert any("concentrated" in r for r in tested.reasons)


def test_holds_are_capped_relative_to_the_number_of_actions():
    """A subject who rarely trades would otherwise produce a dataset that is almost all holds."""
    holds = [
        score_hold(classification(f"S{i}", end_weight=0.20), period_return=-0.30) for i in range(50)
    ]
    kept = select_holds(holds, action_count=3)

    assert len(kept) == int(3 * MAX_HOLD_RATIO)
    # And the ones kept are the most informative, not the first ones seen.
    assert all(k.score >= min(h.score for h in kept) for k in kept)


def test_the_most_salient_holds_survive_the_cap():
    mild = score_hold(classification("A", end_weight=0.06), period_return=0.02)
    severe = score_hold(classification("B", end_weight=0.20), period_return=-0.45)

    kept = select_holds([mild, severe], action_count=1)
    assert [k.symbol for k in kept][:1] == ["B"]


# --- the split ------------------------------------------------------------------------------------


def _dataset() -> EpisodeDataset:
    return EpisodeDataset(
        advisor_id="buffett",
        train_end=date(2020, 12, 31),
        validation_end=date(2022, 12, 31),
        episodes=[
            episode("e-2018", when=date(2018, 6, 30)),
            episode("e-2020", when=date(2020, 9, 30)),
            episode("e-2021", when=date(2021, 6, 30)),
            episode("e-2023", when=date(2023, 3, 31)),
            episode("e-2024", when=date(2024, 3, 31)),
        ],
    )


def test_episodes_are_split_by_time_not_at_random():
    """A random split lets a persona refined on 2019 Q3 be tested on 2019 Q2 — same market,
    often the same position, and an answer it has already been shown."""
    data = _dataset()

    assert data.split_of(data.episodes[0]) is Split.train
    assert data.split_of(data.episodes[2]) is Split.validation
    assert data.split_of(data.episodes[3]) is Split.held_out


def test_the_held_out_set_cannot_be_reached_while_refining():
    """Refining against 20 cases until 18 pass is memorisation. The only defence is that the
    scoring examples were never available to the thing being scored."""
    data = _dataset()
    refinement_ids = {e.episode_id for e in data.for_refinement()}

    assert refinement_ids == {"e-2018", "e-2020", "e-2021"}
    assert "e-2023" not in refinement_ids
    assert "e-2024" not in refinement_ids


def test_reading_the_held_out_set_takes_a_deliberate_line_of_code():
    data = _dataset()

    with pytest.raises(PermissionError, match="already frozen"):
        data.held_out()

    scored = data.held_out(i_am_scoring_a_locked_persona=True)
    assert {e.episode_id for e in scored} == {"e-2023", "e-2024"}


def test_an_inverted_boundary_is_rejected():
    with pytest.raises(ValueError, match="after validation ends"):
        EpisodeDataset(
            advisor_id="buffett",
            train_end=date(2023, 1, 1),
            validation_end=date(2020, 1, 1),
        )


# --- the manifest tells on the dataset --------------------------------------------------------------


def test_the_manifest_records_which_episodes_produced_a_score():
    """So a reported number can be checked against the exact examples behind it."""
    manifest = _dataset().manifest()

    assert manifest.train_ids == ["e-2018", "e-2020"]
    assert manifest.validation_ids == ["e-2021"]
    assert manifest.held_out_ids == ["e-2023", "e-2024"]
    assert manifest.total == 5


def test_a_dataset_a_trivial_classifier_would_ace_says_so_on_its_own_manifest():
    """The failure this catches: 90% holds, 91% accuracy, and a model that never acts."""
    data = EpisodeDataset(
        advisor_id="buffett",
        train_end=date(2030, 1, 1),
        validation_end=date(2031, 1, 1),
        episodes=[
            episode(f"hold-{i}", when=date(2020, 3, 31), action=ObservedAction.hold)
            for i in range(9)
        ]
        + [episode("act-1", when=date(2020, 3, 31))],
    )
    warning = data.manifest().imbalance_warning()

    assert warning is not None
    assert "always answers 'hold'" in warning
    assert "per-class F1" in warning


def test_a_balanced_dataset_raises_no_warning():
    data = EpisodeDataset(
        advisor_id="buffett",
        train_end=date(2030, 1, 1),
        validation_end=date(2031, 1, 1),
        episodes=[
            episode("a", when=date(2020, 3, 31), action=ObservedAction.increase),
            episode("b", when=date(2020, 3, 31), action=ObservedAction.reduce),
            episode("c", when=date(2020, 3, 31), action=ObservedAction.hold),
            episode("d", when=date(2020, 3, 31), action=ObservedAction.exit),
        ],
    )
    assert data.manifest().imbalance_warning() is None


def test_a_dataset_of_only_trades_would_teach_constant_trading():
    """The dataset that looks fine and produces a model that never sits still."""
    trades_only = EpisodeDataset(
        advisor_id="buffett",
        train_end=date(2030, 1, 1),
        validation_end=date(2031, 1, 1),
        episodes=[
            episode(f"t-{i}", when=date(2020, 3, 31), action=ObservedAction.increase)
            for i in range(10)
        ],
    )
    manifest = trades_only.manifest()

    assert manifest.hold_ratio == 0.0
    assert manifest.imbalance_warning() is not None


def test_the_manifest_reports_average_evidence_quality():
    """A dataset of weakly-attributed inferences should not read the same as one of exact,
    self-described decisions."""
    weak = EpisodeDataset(
        advisor_id="buffett",
        train_end=date(2030, 1, 1),
        validation_end=date(2031, 1, 1),
        episodes=[
            episode("w", when=date(2020, 3, 31), basis=ActionBasis.raw_value, confidence=0.3)
        ],
    )
    strong = EpisodeDataset(
        advisor_id="buffett",
        train_end=date(2030, 1, 1),
        validation_end=date(2031, 1, 1),
        episodes=[episode("s", when=date(2020, 3, 31))],
    )

    assert weak.manifest().mean_training_weight == pytest.approx(0.09)
    assert strong.manifest().mean_training_weight == pytest.approx(1.0)


# --- the decision window ------------------------------------------------------------------------------


def test_inputs_must_predate_the_window_opening_not_its_close():
    """A quarterly filing places a purchase somewhere in three months. If the trade happened on
    day one, anything dated later is news the buyer never saw."""
    with pytest.raises(ValueError, match="window opens"):
        DecisionEpisode(
            advisor_id="buffett",
            episode_id="leaky",
            inputs=EpisodeInputs(as_of=date(2016, 3, 30), symbol="AAPL"),
            observed_action=ObservedAction.enter,
            action_basis=ActionBasis.share_count,
            decision_window_start=date(2016, 1, 1),
            decision_window_end=date(2016, 3, 31),
        )


def test_a_filing_records_its_own_reporting_delay():
    scope = DisclosureScope.institutional(
        "Berkshire Hathaway Inc",
        period_start=date(2016, 1, 1),
        period_end=date(2016, 3, 31),
        filed_at=date(2016, 5, 16),
    )

    assert scope.reporting_delay_days == 46
    assert scope.decision_window_days == 90
    assert "cash and cash equivalents" in scope.omits
    assert "which manager placed the order" in " ".join(scope.omits)
    assert "Does not show" in scope.describe_limits()


def test_a_filing_cannot_predate_the_period_it_reports():
    with pytest.raises(ValueError, match="cannot be knowable before"):
        DisclosureScope.institutional(
            "Berkshire Hathaway Inc",
            period_start=date(2016, 1, 1),
            period_end=date(2016, 3, 31),
            filed_at=date(2016, 3, 1),
        )


def test_the_window_width_is_available_as_an_evidence_discount():
    """A decision pinned to a week is stronger evidence than one pinned to a quarter."""
    quarterly = episode("q", when=date(2016, 3, 31))
    assert quarterly.decision_window_days == 90
