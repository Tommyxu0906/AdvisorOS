"""A version names one set of rows, and the code enforces that rather than the README.

`test_the_same_version_with_different_rows_is_refused` is the whole file. A deterministic rebuild
is not a frozen dataset: it reproduces whatever the pipeline computes *today*, so the same version
quietly comes to mean something else the moment a tolerance moves or one more corporate action
gets curated — and every score already reported against it silently stops being comparable.
"""

from __future__ import annotations

import json
from datetime import date

import pytest

from app.distillation.finance_nuwa.access import (
    HeldOutLocked,
    ScoredEpisode,
    held_out_dataset,
    refinement_dataset,
    validation_dataset,
)
from app.distillation.finance_nuwa.artifact import (
    EPISODES_FILE,
    OUTCOMES_FILE,
    ArtifactManifest,
    DatasetFrozenError,
    EpisodeRow,
    OutcomeRow,
    freeze,
    serialize_rows,
    verify,
)
from app.distillation.finance_nuwa.episode import EpisodeOutcome


def row(episode_id: str, *, split: str = "train", action: str = "hold") -> EpisodeRow:
    return EpisodeRow(
        episode_id=episode_id,
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
        features={"weight": 0.1},
        split=split,
    )


def manifest(**kw) -> ArtifactManifest:
    base = dict(dataset_version="test-v1.0", target_id="berkshire_public_equity")
    base.update(kw)
    return ArtifactManifest(**base)


def frozen(tmp_path, rows, **kw):
    ids = {"train": [], "validation": [], "held_out": []}
    for r in rows:
        ids[r.split].append(r.episode_id)
    return freeze(
        tmp_path,
        manifest=manifest(
            train_ids=ids["train"],
            validation_ids=ids["validation"],
            held_out_ids=ids["held_out"],
            **kw,
        ),
        rows=rows,
    )


# --- immutability ------------------------------------------------------------------------------


def test_a_frozen_artifact_records_the_hash_of_its_own_rows(tmp_path):
    result = frozen(tmp_path, [row("a"), row("b")])

    assert result.written
    assert result.manifest.row_count == 2
    assert len(result.manifest.artifact_sha256) == 64
    ok, detail = verify(tmp_path)
    assert ok
    assert "2 rows" in detail


def test_refreezing_identical_rows_is_a_no_op_rather_than_an_error(tmp_path):
    """Rebuilding is normal and expected. It is only *changing* that has to be refused."""
    first = frozen(tmp_path, [row("a"), row("b")])
    second = frozen(tmp_path, [row("a"), row("b")])

    assert first.written
    assert not second.written
    assert first.manifest.artifact_sha256 == second.manifest.artifact_sha256


def test_the_same_version_with_different_rows_is_refused(tmp_path):
    frozen(tmp_path, [row("a"), row("b")])

    with pytest.raises(DatasetFrozenError, match="already frozen"):
        frozen(tmp_path, [row("a"), row("b"), row("c")])


def test_changing_one_field_of_one_row_is_enough_to_refuse(tmp_path):
    """Row counts matching is not the check. The bytes are."""
    frozen(tmp_path, [row("a", action="hold")])

    with pytest.raises(DatasetFrozenError, match="already frozen"):
        frozen(tmp_path, [row("a", action="reduce")])


def test_a_new_version_may_say_something_new(tmp_path):
    frozen(tmp_path, [row("a")])
    result = frozen(tmp_path / "v2", [row("a"), row("b")], dataset_version="test-v2.0")

    assert result.written
    assert result.manifest.dataset_version == "test-v2.0"


def test_the_hash_tracks_content_and_not_serialization_order_of_keys(tmp_path):
    """Canonical JSONL. Otherwise the digest depends on how a serializer felt that day, and an
    immutability check that fires on formatting is one people learn to bypass."""
    payload = serialize_rows([row("a")])
    reordered = json.loads(payload.decode())

    assert list(reordered) == sorted(reordered)


def test_a_tampered_file_fails_verification_even_though_the_manifest_is_intact(tmp_path):
    frozen(tmp_path, [row("a"), row("b")])
    (tmp_path / EPISODES_FILE).write_text(
        (tmp_path / EPISODES_FILE).read_text().replace('"hold"', '"reduce"')
    )

    ok, detail = verify(tmp_path)
    assert not ok
    assert "changed since it was frozen" in detail


# --- the split has to account for every row --------------------------------------------------


def test_a_row_belonging_to_no_split_is_refused(tmp_path):
    """An unaccounted row is how a held-out example reaches refinement unnoticed."""
    with pytest.raises(DatasetFrozenError, match="split"):
        freeze(tmp_path, manifest=manifest(train_ids=["a"]), rows=[row("a"), row("b")])


def test_a_row_in_two_splits_is_refused(tmp_path):
    with pytest.raises(DatasetFrozenError, match="split"):
        freeze(
            tmp_path,
            manifest=manifest(train_ids=["a"], held_out_ids=["a"]),
            rows=[row("a")],
        )


def test_duplicate_episode_ids_are_refused(tmp_path):
    with pytest.raises(DatasetFrozenError, match="more than once"):
        freeze(tmp_path, manifest=manifest(train_ids=["a", "a"]), rows=[row("a"), row("a")])


# --- outcomes are structurally elsewhere -----------------------------------------------------


def test_the_row_type_has_no_field_an_outcome_could_occupy():
    """Not a convention and not a validator — there is nowhere to put one.

    An optional `outcome: None` on a training row is one careless `model_dump()` away from being
    an outcome on a training row, and it would be invisible in review.
    """
    row_fields = set(EpisodeRow.model_fields)
    outcome_fields = set(EpisodeOutcome.model_fields)

    assert not row_fields & outcome_fields
    assert not {f for f in row_fields if "outcome" in f or "future" in f or "return" in f}


def test_outcomes_live_in_their_own_file(tmp_path):
    freeze(
        tmp_path,
        manifest=manifest(held_out_ids=["a"]),
        rows=[row("a", split="held_out")],
        outcomes=[OutcomeRow(episode_id="a", horizon_months=12, position_return=0.4)],
    )

    assert "0.4" not in (tmp_path / EPISODES_FILE).read_text()
    assert "0.4" in (tmp_path / OUTCOMES_FILE).read_text()


# --- and the reader cannot reach them by accident ------------------------------------------------


def test_refinement_returns_rows_that_cannot_carry_an_outcome(tmp_path):
    freeze(
        tmp_path,
        manifest=manifest(train_ids=["a"], held_out_ids=["b"]),
        rows=[row("a"), row("b", split="held_out")],
        outcomes=[OutcomeRow(episode_id="b", horizon_months=12, position_return=0.4)],
    )

    rows = refinement_dataset(tmp_path)

    assert [r.episode_id for r in rows] == ["a"]
    assert all(isinstance(r, EpisodeRow) for r in rows)


def test_held_out_is_unreachable_without_saying_the_model_is_frozen(tmp_path):
    freeze(
        tmp_path,
        manifest=manifest(held_out_ids=["b"]),
        rows=[row("b", split="held_out")],
    )

    with pytest.raises(HeldOutLocked, match="can no longer change"):
        held_out_dataset(tmp_path)


def test_the_unlock_is_the_only_path_that_pairs_a_row_with_an_outcome(tmp_path):
    freeze(
        tmp_path,
        manifest=manifest(train_ids=["a"], validation_ids=["v"], held_out_ids=["b"]),
        rows=[row("a"), row("v", split="validation"), row("b", split="held_out")],
        outcomes=[OutcomeRow(episode_id="b", horizon_months=12, position_return=0.4)],
    )

    scored = held_out_dataset(tmp_path, require_frozen_model=True)

    assert [s.row.episode_id for s in scored] == ["b"]
    assert scored[0].outcome.position_return == 0.4
    # And nothing else in the module returns a type that could hold one.
    assert not any(
        isinstance(r, ScoredEpisode)
        for r in [*refinement_dataset(tmp_path), *validation_dataset(tmp_path)]
    )


def test_validation_is_separate_from_refinement(tmp_path):
    """Choosing between candidates is a weaker use than refining against, and pooling them makes
    it impossible to say afterwards which one happened."""
    freeze(
        tmp_path,
        manifest=manifest(train_ids=["a"], validation_ids=["v"]),
        rows=[row("a"), row("v", split="validation")],
    )

    assert [r.episode_id for r in refinement_dataset(tmp_path)] == ["a"]
    assert [r.episode_id for r in validation_dataset(tmp_path)] == ["v"]


def test_an_unverified_dataset_is_refused_on_every_read_not_just_at_build_time(tmp_path):
    """Checked when written and trusted forever after is checked at the one moment nothing could
    have gone wrong yet."""
    freeze(tmp_path, manifest=manifest(train_ids=["a"]), rows=[row("a")])
    (tmp_path / EPISODES_FILE).write_text(
        (tmp_path / EPISODES_FILE).read_text().replace('"hold"', '"exit"')
    )

    with pytest.raises(ValueError, match="refusing to read an unverified dataset"):
        refinement_dataset(tmp_path)
