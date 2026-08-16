"""Composing one canonical quarter out of an original filing and its amendments.

Deliberately separate from corporate-action resolution. Both change what a quarter's positions
are, but they answer different questions — *which documents describe this quarter* versus *did
this security change identity between quarters* — and merging them into one pass makes both
harder to check.

The reason this matters more than it looks: an institution may request confidential treatment
for a position it is still building, file the quarter without it, and disclose it in an amendment
months later. Every amendment in the Berkshire 2014-2024 range is exactly that — one to three
positions, no overlap with the original at all.

Dropping those amendments does not merely lose a position. It **fabricates a decision at the
wrong time**: the holding is absent from the quarter it was actually bought in, then appears in
the next regular filing, and the drift classifier reads that as an `enter` in a quarter where
nothing happened. A model trained on that learns the investor's timing wrong in the specific
cases they cared most about hiding.

So amendments are applied in filing order, and what each one does depends on its type:

    original        the starting set of positions
    RESTATEMENT     replaces everything accumulated so far
    NEW HOLDINGS    unions into it — the original stays authoritative
    unknown         stops the composition and sends the quarter to review

A `NEW HOLDINGS` amendment that repeats a CUSIP already present is ambiguous — a correction and
an addition look identical — so that also goes to review rather than being resolved by a rule
nobody can defend.
"""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel, ConfigDict, Field

from app.distillation.finance_nuwa.sec_13f import HoldingsSnapshot, ParsedPosition
from app.distillation.finance_nuwa.store import AmendmentType, QuarterLineage


class CanonicalQuarter(BaseModel):
    """The positions a quarter actually held, and the documents that establish them."""

    model_config = ConfigDict(extra="forbid")

    period_end: date
    positions: list[ParsedPosition] = Field(default_factory=list)

    contributing_accessions: list[str] = Field(default_factory=list)
    resolution: str = ""
    needs_review: bool = False
    review_reason: str = ""

    # What the amendments added on top of the original, so the audit can show the effect rather
    # than asserting it.
    positions_from_amendments: int = 0
    value_from_amendments: float = 0.0

    @property
    def total_value(self) -> float:
        return sum(p.market_value for p in self.positions)

    @property
    def is_usable(self) -> bool:
        return not self.needs_review and bool(self.positions)


def compose_quarter(
    lineage: QuarterLineage, snapshots: dict[str, HoldingsSnapshot]
) -> CanonicalQuarter:
    """Build one quarter's canonical positions from its filings.

    `snapshots` maps accession to the parsed filing. A missing one is a review, not a silent
    omission — composing a quarter from documents you could not read is how a hole becomes a
    conclusion.
    """
    ordered = sorted(lineage.filings, key=lambda f: (f.filed_at, f.accession))
    if not ordered:
        return CanonicalQuarter(
            period_end=lineage.period_end,
            needs_review=True,
            review_reason="no filings for this period",
        )

    accumulated: dict[tuple[str, str], ParsedPosition] = {}
    contributing: list[str] = []
    steps: list[str] = []
    added_positions = 0
    added_value = 0.0

    for filing in ordered:
        snapshot = snapshots.get(filing.accession)
        if snapshot is None:
            return CanonicalQuarter(
                period_end=lineage.period_end,
                needs_review=True,
                review_reason=f"{filing.accession} was not parsed, so the quarter is incomplete",
                contributing_accessions=contributing,
            )

        incoming = {p.identity.key: p for p in snapshot.positions}

        if not filing.is_amendment:
            accumulated = dict(incoming)
            contributing = [filing.accession]
            steps.append(f"original {filing.accession} ({len(incoming)} positions)")
            continue

        if filing.amendment_type is AmendmentType.restatement:
            accumulated = dict(incoming)
            contributing = [filing.accession]
            added_positions, added_value = 0, 0.0
            steps.append(
                f"restatement {filing.accession} filed {filing.filed_at} replaced everything "
                f"prior ({len(incoming)} positions)"
            )
            continue

        if filing.amendment_type is AmendmentType.new_holdings:
            collisions = sorted(set(accumulated) & set(incoming))
            if collisions:
                # A correction and an addition are indistinguishable here, and picking one would
                # either drop a real position or double-count it.
                return CanonicalQuarter(
                    period_end=lineage.period_end,
                    needs_review=True,
                    review_reason=(
                        f"amendment {filing.accession} repeats {len(collisions)} security(ies) "
                        f"already filed — e.g. {collisions[0][0]}. Correction and addition are "
                        "indistinguishable, so this needs a human"
                    ),
                    contributing_accessions=contributing,
                )
            accumulated.update(incoming)
            contributing.append(filing.accession)
            added_positions += len(incoming)
            added_value += sum(p.market_value for p in incoming.values())
            steps.append(
                f"amendment {filing.accession} filed {filing.filed_at} added {len(incoming)} "
                "previously undisclosed position(s)"
            )
            continue

        return CanonicalQuarter(
            period_end=lineage.period_end,
            needs_review=True,
            review_reason=(
                f"amendment {filing.accession} has an unreadable type, and guessing between "
                "replace and add either drops real positions or double-counts them"
            ),
            contributing_accessions=contributing,
        )

    positions = sorted(accumulated.values(), key=lambda p: p.market_value, reverse=True)
    return CanonicalQuarter(
        period_end=lineage.period_end,
        positions=positions,
        contributing_accessions=contributing,
        resolution="; ".join(steps),
        positions_from_amendments=added_positions,
        value_from_amendments=round(added_value, 2),
    )
