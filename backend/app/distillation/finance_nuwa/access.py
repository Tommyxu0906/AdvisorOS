"""The only ways to read a frozen dataset, and the reason there are exactly three.

Refining a persona against the examples it will later be scored on is the oldest way to produce a
number that means nothing, and it does not require bad faith — it requires a convenient import.
Someone loads "the dataset", filters it later, and the held-out outcomes have already been in
context for twenty turns. The defence cannot be a docstring asking people not to.

So the barrier is structural, in the type system rather than in a rule:

    refinement_dataset()    -> list[EpisodeRow]        train rows. No outcome field exists.
    validation_dataset()    -> list[EpisodeRow]        validation rows. Same.
    held_out_dataset(...)   -> list[ScoredEpisode]     the only type that pairs an outcome

`EpisodeRow` has no field an outcome could occupy — not optional, not nullable. A patcher holding
one cannot reach a future value by any accessor, any `model_dump()`, any summary, or any
accidental prompt interpolation, because there is nothing there to reach. The outcomes live in a
different file, and the only function in this module that opens it is `held_out_dataset`, which
takes a keyword the caller has to write out in full and which shows up in review as a line
somebody chose to add.

`validation_dataset` is separate from `refinement_dataset` for a different reason: validation is
for choosing between candidate personas, which is a weaker use than refining against, and mixing
them makes it impossible to say afterwards which of the two happened.
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from app.distillation.finance_nuwa.artifact import (
    EPISODES_FILE,
    OUTCOMES_FILE,
    ArtifactManifest,
    EpisodeRow,
    OutcomeRow,
    load_manifest,
    verify,
)

TRAIN, VALIDATION, HELD_OUT = "train", "validation", "held_out"


class HeldOutLocked(PermissionError):
    """Raised when held-out data is requested by something that could still change."""


class ScoredEpisode(BaseModel):
    """A held-out row paired with what actually happened.

    The only type in the system that holds both, and it is constructible only by
    `held_out_dataset`. Anything that can produce a `ScoredEpisode` has already declared that the
    thing being measured is frozen.
    """

    model_config = ConfigDict(extra="forbid")

    row: EpisodeRow
    outcome: OutcomeRow | None = None


def refinement_dataset(directory: Path | str) -> list[EpisodeRow]:
    """Training rows. This is what a patcher gets, and it cannot carry an outcome."""
    return _rows(directory, split=TRAIN)


def validation_dataset(directory: Path | str) -> list[EpisodeRow]:
    """Validation rows, for choosing between candidates rather than refining against."""
    return _rows(directory, split=VALIDATION)


def held_out_dataset(
    directory: Path | str, *, require_frozen_model: bool = False
) -> list[ScoredEpisode]:
    """The examples nothing may be refined against.

    The keyword is not security — anyone can pass it. That is the point: it cannot be reached by
    accident, and it appears in a diff as a claim somebody made about the model under test.
    """
    if not require_frozen_model:
        raise HeldOutLocked(
            "held_out_dataset() scores a model that can no longer change. If you are refining, "
            "call refinement_dataset(); if you are choosing between candidates, call "
            "validation_dataset(). Pass require_frozen_model=True once the version under test is "
            "frozen — and note that having read these outcomes disqualifies every later revision "
            "of that model from being scored on them."
        )
    rows = _rows(directory, split=HELD_OUT)
    outcomes = _read_outcomes(Path(directory))
    return [ScoredEpisode(row=row, outcome=outcomes.get(row.episode_id)) for row in rows]


def manifest_of(directory: Path | str) -> ArtifactManifest:
    manifest = load_manifest(Path(directory))
    if manifest is None:
        raise FileNotFoundError(f"no frozen dataset at {directory}")
    return manifest


def _rows(directory: Path | str, *, split: str) -> list[EpisodeRow]:
    """Read the artifact, refusing one whose bytes no longer match its manifest.

    Verified on every read rather than once at build time. A dataset that is checked when written
    and trusted forever after is checked at the one moment nothing could have gone wrong yet.
    """
    path = Path(directory)
    ok, detail = verify(path)
    if not ok:
        raise ValueError(f"refusing to read an unverified dataset: {detail}")

    rows = [
        EpisodeRow.model_validate_json(line)
        for line in (path / EPISODES_FILE).read_text().splitlines()
        if line
    ]
    return [row for row in rows if row.split == split]


def _read_outcomes(directory: Path) -> dict[str, OutcomeRow]:
    """Open the outcomes file. Called from exactly one place, and that is the whole design."""
    path = directory / OUTCOMES_FILE
    if not path.exists():
        return {}
    parsed = [
        OutcomeRow.model_validate(json.loads(line))
        for line in path.read_text().splitlines()
        if line
    ]
    return {outcome.episode_id: outcome for outcome in parsed}
