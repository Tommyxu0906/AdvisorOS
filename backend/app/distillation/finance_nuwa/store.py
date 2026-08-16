"""Three layers, one of them immutable, and a paper trail back to the filing.

    raw/         exactly what SEC returned, never rewritten
      ↓          parser vN
    snapshots/   normalized positions, rebuildable offline
      ↓          builder vN
    episodes/    decisions, with a dataset version that never moves

The layering exists for one reason: **parser bugs are certain**. Something in the value unit,
the share classes, the amendments, or a corporate action will turn out to be wrong months from
now, and the fix must not require re-fetching a decade of filings from SEC — partly out of
courtesy to a public service, mostly because a re-fetch silently changes the inputs underneath
every result already reported. Raw bytes are written once and never touched again; everything
below them is derived and disposable.

**A dataset version is immutable.** When 2014-2016 lands later, it does not quietly extend
`berkshire-v1.0` — it becomes `v1.1`, and `v1.0` keeps meaning exactly what it meant when a
score was reported against it. Otherwise "held-out F1 was 0.71" is unanswerable six weeks later,
because nobody can say which examples that was.

**Amendments keep their lineage.** Berkshire amended 2023 Q3 twice, five months apart. Keeping
only the survivor makes a strange episode untraceable, so every filing for a period is recorded
along with which one was chosen and why. The choice itself is not always obvious: a restatement
replaces the original outright, while an amendment that adds holdings has to be combined with
it, and the two need different handling. Where the type is unclear the quarter is marked for
review rather than resolved by guessing.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime
from enum import Enum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from app.distillation.finance_nuwa.sec_13f import PARSER_VERSION, HoldingsSnapshot

BUILDER_VERSION = "episode-builder-1"


class AmendmentType(str, Enum):
    restatement = "restatement"
    """Replaces the original filing outright. The amendment alone is canonical."""

    new_holdings = "new_holdings"
    """Adds holdings to the original. Both are needed, and combining them is not automatic."""

    unknown = "unknown"
    """The filing does not say. Resolved by review, never by assumption."""


class FilingRef(BaseModel):
    """One filing for one period, as EDGAR describes it."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    accession: str
    form_type: str
    period_end: date
    filed_at: date
    amendment_type: AmendmentType = AmendmentType.unknown

    @property
    def is_amendment(self) -> bool:
        return self.form_type.upper().endswith("/A")


class QuarterLineage(BaseModel):
    """Every filing for a period, which one was chosen, and why.

    Kept whole so that a strange episode can be traced to a specific SEC document rather than to
    "the 2023 Q3 data". That trace is the difference between finding a bug and arguing about one.
    """

    model_config = ConfigDict(extra="forbid")

    period_end: date
    filings: list[FilingRef] = Field(default_factory=list)
    canonical_accession: str = ""
    reason: str = ""
    needs_review: bool = False

    @property
    def amendment_count(self) -> int:
        return sum(1 for f in self.filings if f.is_amendment)


def resolve_quarter(filings: list[FilingRef]) -> QuarterLineage:
    """Choose the canonical filing for a period, keeping the whole trail.

    Restatements replace, so the latest one wins outright. Additive amendments do not — they
    supplement an original that is still authoritative — and combining them correctly is a
    judgement this function deliberately declines to make on its own.
    """
    if not filings:
        raise ValueError("cannot resolve a quarter with no filings")

    ordered = sorted(filings, key=lambda f: (f.filed_at, f.accession))
    period_end = ordered[0].period_end
    originals = [f for f in ordered if not f.is_amendment]
    amendments = [f for f in ordered if f.is_amendment]

    if not amendments:
        return QuarterLineage(
            period_end=period_end,
            filings=ordered,
            canonical_accession=ordered[-1].accession,
            reason="single original filing, no amendments",
        )

    latest = amendments[-1]
    if latest.amendment_type is AmendmentType.restatement:
        return QuarterLineage(
            period_end=period_end,
            filings=ordered,
            canonical_accession=latest.accession,
            reason=(
                f"latest restatement {latest.accession} filed {latest.filed_at} replaces "
                f"{len(ordered) - 1} earlier filing(s) outright"
            ),
        )

    # Additive or unknown: the original still holds and the amendment adds to it. Merging is not
    # something to do silently, so the quarter is flagged and the original stays canonical.
    return QuarterLineage(
        period_end=period_end,
        filings=ordered,
        canonical_accession=originals[-1].accession if originals else latest.accession,
        reason=(
            f"amendment {latest.accession} is {latest.amendment_type.value}, which supplements "
            "rather than replaces — needs review before the quarter can be trusted"
        ),
        needs_review=True,
    )


class DatasetVersion(BaseModel):
    """What a reported number was actually computed against.

    Immutable by convention and by name. Extending coverage produces a new version rather than
    editing this one, so that a score reported six weeks ago still identifies its own inputs.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(pattern=r"^[a-z0-9]+-v\d+\.\d+$")
    entity: str
    cik: str

    requested_start: date
    requested_end: date
    actual_quarters: list[date] = Field(default_factory=list)

    parser_version: str = PARSER_VERSION
    builder_version: str = BUILDER_VERSION
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @property
    def coverage(self) -> str:
        """Actual coverage, never the requested range.

        These differ in practice and the difference is exactly what a reader needs: claiming
        2014-2024 while holding 2017-2024 is the kind of quiet overstatement that makes every
        other number in a report suspect.
        """
        if not self.actual_quarters:
            return "no quarters"
        first, last = min(self.actual_quarters), max(self.actual_quarters)
        return f"{_quarter_label(first)}-{_quarter_label(last)}"

    @property
    def missing_quarters(self) -> list[date]:
        """Quarter ends in the requested window with no filing behind them."""
        have = set(self.actual_quarters)
        return [q for q in _quarter_ends(self.requested_start, self.requested_end) if q not in have]

    def describe(self) -> str:
        got, asked = (
            len(self.actual_quarters),
            len(_quarter_ends(self.requested_start, self.requested_end)),
        )
        return (
            f"{self.name}  coverage {self.coverage}  ({got}/{asked} quarters)  "
            f"parser {self.parser_version}"
        )


class FilingStore:
    """Filesystem layout for the three layers. Raw is write-once."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def raw_dir(self, accession: str) -> Path:
        return self.root / "raw" / accession

    def write_raw(self, accession: str, filename: str, content: bytes) -> Path:
        """Persist a fetched document. Refuses to change one that already exists.

        Re-fetching and overwriting would silently move the ground under every result already
        computed. Identical content is accepted as a no-op so a resumed backfill is safe.
        """
        path = self.raw_dir(accession)
        path.mkdir(parents=True, exist_ok=True)
        target = path / filename

        if target.exists():
            existing = target.read_bytes()
            if existing != content:
                raise ValueError(
                    f"{target} already exists with different content. Raw filings are immutable "
                    "— if the parser changed, rebuild snapshots from raw instead of re-fetching"
                )
            return target

        target.write_bytes(content)
        return target

    def read_raw(self, accession: str, filename: str) -> bytes | None:
        target = self.raw_dir(accession) / filename
        return target.read_bytes() if target.exists() else None

    def has_raw(self, accession: str, filename: str) -> bool:
        return (self.raw_dir(accession) / filename).exists()

    def write_snapshot(self, snapshot: HoldingsSnapshot) -> Path:
        """Derived, so freely overwritten when the parser improves."""
        path = self.root / "snapshots"
        path.mkdir(parents=True, exist_ok=True)
        target = path / f"{snapshot.period_end.isoformat()}_{snapshot.accession}.json"
        target.write_text(snapshot.model_dump_json(indent=2))
        return target

    def load_snapshots(self) -> list[HoldingsSnapshot]:
        path = self.root / "snapshots"
        if not path.exists():
            return []
        snapshots = [
            HoldingsSnapshot.model_validate_json(p.read_text()) for p in sorted(path.glob("*.json"))
        ]
        return sorted(snapshots, key=lambda s: s.period_end)

    def write_version(self, version: DatasetVersion, lineage: list[QuarterLineage]) -> Path:
        path = self.root / "episodes"
        path.mkdir(parents=True, exist_ok=True)
        target = path / f"{version.name}.manifest.json"
        target.write_text(
            json.dumps(
                {
                    "version": version.model_dump(mode="json"),
                    "lineage": [q.model_dump(mode="json") for q in lineage],
                },
                indent=2,
            )
        )
        return target

    def content_hash(self, accession: str, filename: str) -> str | None:
        """Stable fingerprint of a raw document, for the audit trail."""
        content = self.read_raw(accession, filename)
        return hashlib.sha256(content).hexdigest()[:16] if content else None


def _quarter_ends(start: date, end: date) -> list[date]:
    """Every calendar quarter end in a window, inclusive."""
    quarters: list[date] = []
    year, month = start.year, ((start.month - 1) // 3) * 3 + 3
    while True:
        last_day = 31 if month in (3, 12) else 30
        candidate = date(year, month, last_day)
        if candidate > end:
            break
        if candidate >= start:
            quarters.append(candidate)
        month += 3
        if month > 12:
            month, year = 3, year + 1
    return quarters


def _quarter_label(when: date) -> str:
    return f"{when.year}Q{(when.month - 1) // 3 + 1}"
