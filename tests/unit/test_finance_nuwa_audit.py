"""The gates, and the numbers a reader checks a score against.

`test_a_dataset_with_a_lookahead_violation_is_blocked` is the whole purpose. Everything else in
this pipeline is defensible engineering; this is the part that refuses to let a compromised
dataset produce a number anyone would quote.
"""

from __future__ import annotations

from datetime import date

import pytest

from app.distillation.finance_nuwa.audit import DatasetAudit
from app.distillation.finance_nuwa.dataset import (
    select_matched_holds,
    stratum_for,
)
from app.distillation.finance_nuwa.features import build_features, implied_price, regime_bucket
from app.distillation.finance_nuwa.identity import SecurityIdentity
from app.distillation.finance_nuwa.lineage import CanonicalPosition, CanonicalQuarter
from app.distillation.finance_nuwa.sec_13f import ParsedPosition


def audit(**kw) -> DatasetAudit:
    base = dict(
        dataset_version="berkshire-v1.0",
        entity="Berkshire Hathaway Inc",
        action_counts={"hold": 526, "reduce": 243, "increase": 224, "exit": 92, "enter": 85},
        share_count_grounded=1170,
    )
    base.update(kw)
    return DatasetAudit(**base)


# --- the gates -------------------------------------------------------------------------------


def test_a_clean_dataset_passes_and_says_so():
    report = audit()
    assert report.passes
    assert all(g.passed for g in report.gates)
    assert "may proceed to modelling" in report.render()


@pytest.mark.parametrize(
    "failure",
    [
        {"lookahead_violations": 1},
        {"value_unit_conflicts": 1},
        {"unresolved_blocking_actions": 1},
    ],
)
def test_any_single_integrity_failure_blocks_the_whole_dataset(failure: dict):
    """No trading off one against the others: each is individually disqualifying."""
    report = audit(**failure)

    assert not report.passes
    rendered = report.render()
    assert "BLOCKED" in rendered
    assert "Modelling must not begin" in rendered


def test_a_dataset_with_a_lookahead_violation_is_blocked():
    report = audit(lookahead_violations=3)
    failing = [g for g in report.gates if not g.passed]

    assert [g.name for g in failing] == ["lookahead violations"]
    assert "would score well and teach nothing" in failing[0].detail


# --- the numbers a score has to be read against -------------------------------------------------


def test_the_majority_class_rate_is_reported_before_any_model_exists():
    """Printed up front, not after someone is attached to a result. An accuracy of 45% against
    this dataset means the model learned nothing at all."""
    report = audit()
    assert report.majority_class_rate == pytest.approx(0.4496, abs=1e-4)
    assert "Always answer the majority class   45.0%" in report.render()


def test_grounding_separates_what_was_observed_from_what_was_inferred():
    observed = audit(share_count_grounded=1170, drift_inferred=0)
    mixed = audit(share_count_grounded=600, drift_inferred=570)

    assert observed.grounding_share == 1.0
    assert mixed.grounding_share == pytest.approx(0.5128, abs=1e-3)


def test_coverage_and_totals_are_rendered_for_a_reader_to_check():
    report = audit(
        coverage="2014-03-31 to 2024-12-31",
        canonical_quarters=44,
        raw_rows=6741,
        canonical_positions=2044,
        unique_cusips=134,
    )
    rendered = report.render()

    assert "2014-03-31 to 2024-12-31" in rendered
    assert "6,741 -> 2,044" in rendered
    assert report.total_episodes == 1170


def test_an_empty_dataset_does_not_divide_by_zero():
    empty = audit(action_counts={}, share_count_grounded=0)
    assert empty.majority_class_rate == 0.0
    assert empty.grounding_share == 0.0
    assert empty.total_episodes == 0


# --- matched controls ------------------------------------------------------------------------------


def test_a_hold_is_matched_to_a_trade_in_the_same_cell():
    """Without matching, if the kept holds are the big volatile positions and the trades are
    everything else, size alone separates the classes and a model wins by learning the sampler."""
    stratum = stratum_for(weight=0.08, trailing_return=0.01, regime="flat")
    other = stratum_for(weight=0.001, trailing_return=-0.40, regime="falling")

    selection = select_matched_holds(
        [("trade-1", stratum)],
        [("hold-far", other, 0.9), ("hold-near", stratum, 0.4)],
    )

    # The nearer cell wins even though the far hold is more salient — matching picks the cell,
    # salience only breaks ties inside one.
    assert selection.kept == ["hold-near"]
    assert selection.unmatched_actions == 0


def test_salience_breaks_ties_within_a_cell():
    stratum = stratum_for(weight=0.08, trailing_return=0.01, regime="flat")
    selection = select_matched_holds(
        [("trade-1", stratum)],
        [("quiet", stratum, 0.31), ("pressured", stratum, 0.95)],
    )
    assert selection.kept == ["pressured"]


def test_a_trade_with_no_comparable_hold_is_reported_not_hidden():
    """Silently dropping these would bias the dataset in exactly the direction matching exists
    to remove."""
    selection = select_matched_holds(
        [("trade-1", stratum_for(weight=0.30, trailing_return=0.90, regime="rising"))],
        [("hold-1", stratum_for(weight=0.001, trailing_return=0.0, regime="flat"), 0.5)],
    )

    assert selection.kept == []
    assert selection.unmatched_actions == 1
    assert selection.match_rate == 0.0


def test_a_hold_is_used_once_and_not_reused_across_trades():
    stratum = stratum_for(weight=0.08, trailing_return=0.01, regime="flat")
    selection = select_matched_holds(
        [("trade-1", stratum), ("trade-2", stratum)],
        [("hold-1", stratum, 0.9)],
    )

    assert selection.kept == ["hold-1"]
    assert selection.unmatched_actions == 1


def test_an_unknown_feature_gets_its_own_cell_rather_than_the_smallest_band():
    """Pooling unknown with 'smallest' would quietly match a new position against a tiny one."""
    unknown = stratum_for(weight=None, trailing_return=None, regime="unknown")
    tiny = stratum_for(weight=0.001, trailing_return=-0.30, regime="falling")

    assert unknown.key != tiny.key
    assert unknown.weight_bucket == -1


def test_regime_buckets_are_coarse_on_purpose():
    assert regime_bucket(-0.15) == "falling"
    assert regime_bucket(0.0) == "flat"
    assert regime_bucket(0.20) == "rising"
    assert regime_bucket(None) == "unknown"


# --- point-in-time features ---------------------------------------------------------------------------


def _q(period_end: date, entries: list[tuple[str, float, float]]) -> CanonicalQuarter:
    return CanonicalQuarter(
        period_end=period_end,
        positions=[
            CanonicalPosition(
                position=ParsedPosition(
                    identity=SecurityIdentity(cusip=c, issuer_name=c, title_of_class="COM"),
                    market_value=v,
                    raw_value=v,
                    shares=s,
                ),
                disclosed_at=period_end,
                source_accession="acc",
            )
            for c, v, s in entries
        ],
        contributing_accessions=["acc"],
    )


def test_an_implied_price_comes_straight_out_of_the_filing():
    """No ticker mapping and no price vendor: value over shares is a price series."""
    quarter = _q(date(2020, 3, 31), [("111111111", 1e9, 10_000_000)])
    assert implied_price(quarter, "111111111") == 100.0
    assert implied_price(quarter, "999999999") is None


def test_a_short_history_reports_none_rather_than_a_partial_window():
    """A four-quarter return computed over two quarters is a different quantity with the same
    name, and a model cannot tell them apart."""
    history = [
        _q(date(2020, 3, 31), [("111111111", 1e9, 10_000_000)]),
        _q(date(2020, 6, 30), [("111111111", 1.2e9, 10_000_000)]),
    ]
    features = build_features(history, "111111111", as_of=date(2020, 7, 1))

    assert features.trailing_return_1q == pytest.approx(0.2)
    assert features.trailing_return_4q is None


def test_features_are_computed_only_from_the_history_they_are_handed():
    """Disclosure timing is decided once, in the builder. This function trusts its input and
    reaches outside it for nothing."""
    history = [_q(date(2020, 3, 31), [("111111111", 1e9, 10_000_000)])]
    features = build_features(history, "111111111", as_of=date(2020, 4, 1))

    assert features.source_period_end == date(2020, 3, 31)
    assert features.as_of == date(2020, 4, 1)
    assert features.weight == 1.0
    assert features.rank == 1


def test_a_position_absent_from_the_visible_book_has_no_weight_rather_than_zero():
    history = [_q(date(2020, 3, 31), [("111111111", 1e9, 10_000_000)])]
    features = build_features(history, "222222222", as_of=date(2020, 4, 1))

    assert features.weight is None
    assert features.rank is None
    assert features.quarters_held is None


def test_holding_duration_stops_at_a_gap():
    """Held, sold, re-bought is not held throughout — and duration of conviction is the point."""
    history = [
        _q(date(2019, 3, 31), [("111111111", 1e9, 1e6)]),
        _q(date(2019, 6, 30), [("222222222", 1e9, 1e6)]),
        _q(date(2019, 9, 30), [("111111111", 1e9, 1e6)]),
        _q(date(2019, 12, 31), [("111111111", 1e9, 1e6)]),
    ]
    assert build_features(history, "111111111", as_of=date(2020, 1, 1)).quarters_held == 2
