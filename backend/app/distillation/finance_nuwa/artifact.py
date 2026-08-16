"""The frozen dataset: bytes on disk with a hash, not a recipe that happens to be deterministic.

Until now `berkshire-v1.0` named a *rebuild*. Every step was reproducible, which sounds like the
same thing and is not. A reproducible pipeline reproduces whatever it currently computes: change
a tolerance, curate one more corporate action, fix a boundary, and the same version name now
denotes a different set of rows. Any score reported against it silently stops being comparable to
the score reported last week, and nothing in the system objects.

So the artifact is written once, hashed, and refuses to change under its own name:

    same version, identical bytes    a no-op — rebuilding is allowed and expected
    same version, different bytes    an error, naming what moved
    different rows or schema         a new version, chosen by a person

The hash is over the rows, not over the file: JSONL is written with sorted keys and no trailing
whitespace so the digest depends on content rather than on how a serializer felt that day.

**Outcomes are not in the row type.** `EpisodeRow` has no field that could hold one — not an
optional one, not a nullable one. That is deliberate and it is the difference between a rule and
a barrier. Fields on one object get serialized together, summarised together, and pasted into
prompts together; an optional `outcome: None` on a training row is one careless `model_dump()`
away from being an outcome on a training row. What happened next lives in a separate file, in a
separate type, reachable only through `access.held_out_dataset`.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.distillation.finance_nuwa.identity import SECURITY_KEY_SCHEMA_VERSION

# Bumped when the row schema changes. A reader that does not recognise this must refuse the file
# rather than guess which columns it has.
ROW_SCHEMA_VERSION = "episode-row-v1"

EPISODES_FILE = "episodes.jsonl"
WITHHELD_FILE = "withheld.jsonl"
OUTCOMES_FILE = "outcomes.jsonl"
MANIFEST_FILE = "manifest.json"


class DatasetFrozenError(RuntimeError):
    """Raised when a version is asked to mean something it does not already mean."""


class MatchedControl(BaseModel):
    """Why a hold is in the dataset, and which action it stands in for.

    Recorded per row rather than derived later, because the sampling decision is part of what the
    dataset *is*: a reader checking whether size separates the classes needs the stratum that was
    actually used, not a reconstruction of it.
    """

    model_config = ConfigDict(extra="forbid")

    stratum: str = Field(description="weight bucket | return bucket | regime, as matched on")
    matched_to: str | None = Field(
        default=None, description="episode_id of the action this hold controls for"
    )
    salience: float | None = None


class EpisodeRow(BaseModel):
    """One frozen modelling row.

    Every field here was knowable when the decision window opened, or is a label, or is
    provenance. Nothing describes what happened afterwards — see the module docstring.
    """

    model_config = ConfigDict(extra="forbid")

    episode_id: str
    target_id: str

    # --- identity
    security: str = Field(description="SecurityKey token: CUSIP:TITLE_OF_CLASS")
    security_cusip: str
    security_title_of_class: str

    # --- label
    observed_action: str
    magnitude: str
    action_basis: str

    # --- whose decision this was
    attribution_basis: str
    attribution_confidence: float
    training_weight: float

    # --- time
    decision_window_start: date
    decision_window_end: date
    public_information_cutoff: date = Field(
        description="Nothing dated after this contributed to any feature or input on this row"
    )

    # --- the information set
    replay_view: str
    feature_source_period_end: date | None = None
    features: dict[str, Any] = Field(default_factory=dict)

    # --- sampling
    is_matched_control: bool = False
    matched_control: MatchedControl | None = None

    # --- provenance
    lineage_refs: list[str] = Field(
        default_factory=list,
        description="Curated corporate actions applied to this transition, as stated identifiers",
    )

    split: str


class WithheldRow(BaseModel):
    """An episode quarantine removed, and the exclusion that removed it.

    Written alongside the dataset rather than discarded, because "we withheld ten episodes" is a
    claim and this is the evidence for it. It also makes the exclusion reference concrete: a
    reader can see exactly which decisions are missing and why, instead of a count.
    """

    model_config = ConfigDict(extra="forbid")

    episode_id: str
    security: str
    decision_window_end: date
    exclusion_status: str
    exclusion_scope: str
    exclusion_reason: str
    detected_kind: str


class OutcomeRow(BaseModel):
    """What happened after a decision. A separate type in a separate file, on purpose."""

    model_config = ConfigDict(extra="forbid")

    episode_id: str
    horizon_months: int
    position_return: float | None = None
    benchmark_return: float | None = None
    note: str = ""


class ArtifactManifest(BaseModel):
    """Everything needed to say whether two results were computed against the same data."""

    model_config = ConfigDict(extra="forbid")

    dataset_version: str
    target_id: str
    frozen_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    # --- the code that produced it
    row_schema_version: str = ROW_SCHEMA_VERSION
    security_key_schema_version: str = SECURITY_KEY_SCHEMA_VERSION
    parser_version: str = ""
    builder_version: str = ""
    feature_schema_version: str = ""

    # --- the curated inputs, by content rather than by filename
    lineage_version: str = ""
    lineage_sha256: str = ""
    quarantine_version: str = ""
    quarantine_sha256: str = ""
    source_manifest_sha256: str = Field(
        default="", description="The raw filing manifest the rows were built from"
    )

    # --- the labels
    share_tolerance: float = 0.0
    row_count: int = 0
    class_counts: dict[str, int] = Field(default_factory=dict)
    withheld_count: int = 0

    # --- the split, by identifier rather than by count
    train_ids: list[str] = Field(default_factory=list)
    validation_ids: list[str] = Field(default_factory=list)
    held_out_ids: list[str] = Field(default_factory=list)

    artifact_sha256: str = ""

    @property
    def split_ids(self) -> list[str]:
        return [*self.train_ids, *self.validation_ids, *self.held_out_ids]

    def split_matches(self, row_ids: list[str]) -> bool:
        """Whether the split accounts for every row exactly once.

        Both halves matter. An id in the split but not the artifact means a score could be
        reported against a row that does not exist; an id in the artifact but no split means a
        row that no rule governs, which is how held-out examples leak into refinement.
        """
        declared = self.split_ids
        return len(declared) == len(set(declared)) == len(row_ids) and set(declared) == set(row_ids)


class FreezeResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    manifest: ArtifactManifest
    path: Path
    written: bool = Field(description="False when the identical artifact was already on disk")


def serialize_rows(rows: list[EpisodeRow]) -> bytes:
    """Canonical JSONL. Sorted keys and a fixed date format, so the hash tracks content only."""
    lines = [
        json.dumps(row.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
        for row in rows
    ]
    return ("\n".join(lines) + "\n").encode() if lines else b""


def sha256_of(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256_of_file(path: Path) -> str:
    return sha256_of(path.read_bytes()) if path.exists() else ""


def freeze(
    directory: Path,
    *,
    manifest: ArtifactManifest,
    rows: list[EpisodeRow],
    withheld: list[WithheldRow] | None = None,
    outcomes: list[OutcomeRow] | None = None,
) -> FreezeResult:
    """Write the artifact, or refuse to.

    The refusal is the feature. A version that can be quietly rewritten is a filename, and every
    number anyone reports against it is uncheckable afterwards.
    """
    directory = Path(directory)
    payload = serialize_rows(rows)
    digest = sha256_of(payload)

    row_ids = [row.episode_id for row in rows]
    if len(set(row_ids)) != len(row_ids):
        duplicates = sorted({i for i in row_ids if row_ids.count(i) > 1})
        raise DatasetFrozenError(
            f"{len(duplicates)} episode id(s) appear more than once, e.g. {duplicates[:3]} — "
            "two rows sharing an id cannot be split, scored, or referenced unambiguously"
        )
    if not manifest.split_matches(row_ids):
        raise DatasetFrozenError(
            f"the split declares {len(manifest.split_ids)} ids for {len(row_ids)} rows and they "
            "do not correspond. Every row must belong to exactly one split, or a held-out "
            "example can reach refinement without anyone noticing"
        )

    manifest = manifest.model_copy(
        update={
            "artifact_sha256": digest,
            "row_count": len(rows),
            "withheld_count": len(withheld or []),
        }
    )

    existing_path = directory / MANIFEST_FILE
    if existing_path.exists():
        existing = ArtifactManifest.model_validate_json(existing_path.read_text())
        if existing.dataset_version == manifest.dataset_version:
            if existing.artifact_sha256 == digest:
                return FreezeResult(manifest=existing, path=directory, written=False)
            raise DatasetFrozenError(
                f"{manifest.dataset_version} is already frozen with "
                f"{existing.row_count} rows at sha256 {existing.artifact_sha256[:12]}, and this "
                f"build produces {len(rows)} rows at {digest[:12]}. A version names one set of "
                "rows. Choose a new dataset version for the new meaning rather than moving this "
                "one under results already reported against it."
            )

    directory.mkdir(parents=True, exist_ok=True)
    (directory / EPISODES_FILE).write_bytes(payload)
    (directory / WITHHELD_FILE).write_bytes(_jsonl(withheld or []))
    (directory / OUTCOMES_FILE).write_bytes(_jsonl(outcomes or []))
    existing_path.write_text(manifest.model_dump_json(indent=2) + "\n")
    return FreezeResult(manifest=manifest, path=directory, written=True)


def verify(directory: Path) -> tuple[bool, str]:
    """Recompute the hash from the bytes on disk and compare it to the manifest.

    Separate from `freeze` so it can run as a gate: the audit should not take the manifest's word
    for what the file contains, since a manifest is exactly as trustworthy as an unchecked
    literal in a report.
    """
    directory = Path(directory)
    manifest_path = directory / MANIFEST_FILE
    episodes_path = directory / EPISODES_FILE
    if not manifest_path.exists() or not episodes_path.exists():
        return False, f"no frozen artifact at {directory}"

    manifest = ArtifactManifest.model_validate_json(manifest_path.read_text())
    actual = sha256_of_file(episodes_path)
    if actual != manifest.artifact_sha256:
        return False, (
            f"{episodes_path.name} hashes to {actual[:12]} but the manifest records "
            f"{manifest.artifact_sha256[:12]} — the file has changed since it was frozen"
        )

    row_ids = [json.loads(line)["episode_id"] for line in episodes_path.read_text().splitlines()]
    if not manifest.split_matches(row_ids):
        return False, "the split manifest does not account for exactly the rows in the artifact"
    return True, f"{len(row_ids)} rows, sha256 {actual[:12]}"


def load_manifest(directory: Path) -> ArtifactManifest | None:
    path = Path(directory) / MANIFEST_FILE
    return ArtifactManifest.model_validate_json(path.read_text()) if path.exists() else None


def _jsonl(rows: list[BaseModel]) -> bytes:
    if not rows:
        return b""
    lines = [
        json.dumps(row.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
        for row in rows
    ]
    return ("\n".join(lines) + "\n").encode()
