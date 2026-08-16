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

**The information-time boundary.** An amendment corrects what was *true* without changing what
was *knowable*, and those are different facts about the same quarter. A position held in 2023 Q3
under confidential treatment and disclosed in May 2024 really was held in Q3 — so it belongs in
the ground truth, and leaving it out fabricates a purchase in Q4. But nobody outside the firm
could see it until 2024, so it must never appear in a replay of a 2023 decision. Both statements
are true at once, which is why every position carries the date it became public and
`knowable_on()` exists to filter by it. Ground truth reads `positions`; replay inputs read
`positions_knowable_on(...)`, and nothing may use the former to build the latter.
"""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel, ConfigDict, Field

from app.distillation.finance_nuwa.identity import SecurityIdentity
from app.distillation.finance_nuwa.sec_13f import HoldingsSnapshot, ParsedPosition
from app.distillation.finance_nuwa.store import AmendmentType, QuarterLineage


class CanonicalPosition(BaseModel):
    """One holding, plus the date the outside world could first see it.

    The two dates differ whenever confidential treatment was granted, and that gap is the whole
    reason this type exists rather than a bare `ParsedPosition`.
    """

    model_config = ConfigDict(extra="forbid")

    position: ParsedPosition
    disclosed_at: date = Field(description="Filing date of the document that first revealed it")
    source_accession: str
    confidential_treatment: bool = Field(
        default=False,
        description="Revealed by a later amendment rather than by the original filing",
    )

    @property
    def identity(self) -> SecurityIdentity:
        return self.position.identity

    @property
    def market_value(self) -> float:
        return self.position.market_value

    @property
    def shares(self) -> float:
        return self.position.shares

    def knowable_on(self, when: date) -> bool:
        """Whether an outside observer could have known about this position on `when`."""
        return self.disclosed_at <= when

    def disclosure_delay_days(self, period_end: date) -> int:
        return (self.disclosed_at - period_end).days


class CanonicalQuarter(BaseModel):
    """The positions a quarter actually held, and the documents that establish them."""

    model_config = ConfigDict(extra="forbid")

    period_end: date
    positions: list[CanonicalPosition] = Field(default_factory=list)

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

    @property
    def late_disclosed(self) -> list[CanonicalPosition]:
        """Positions the public learned about after the regular filing."""
        return [p for p in self.positions if p.confidential_treatment]

    def positions_knowable_on(self, when: date) -> list[CanonicalPosition]:
        """The quarter as an outside observer could have seen it on a given date.

        This — never `positions` — is what a replay of a contemporaneous decision may read. The
        difference is exactly the confidential-treatment holdings, which is to say the ones the
        firm went to some trouble to keep private at the time.
        """
        return [p for p in self.positions if p.knowable_on(when)]

    def as_parsed(self) -> list[ParsedPosition]:
        """Underlying positions, for arithmetic that has no view on disclosure timing."""
        return [p.position for p in self.positions]


class PublicQuarterView(BaseModel):
    """A quarter as an outside observer could actually see it on a given date.

    A separate type rather than a convention, because the convention failed. The build script
    filtered *quarters* by filing date and then handed the whole `CanonicalQuarter` to the
    feature layer, which read `.positions` — so a holding that was real in the quarter but not
    disclosed until a confidential-treatment amendment months later still reached portfolio
    weight, rank, HHI, the regime proxy, and hold matching.

    That leak is nastier than the one the episode audit checks for. Those features never enter
    `EpisodeInputs`, so a lookahead check that only inspects episode inputs reports a clean zero
    while the control selection has already been contaminated. A model would then be scored on
    matched holds chosen using information nobody had.

    So filtering happens exactly once, here, at construction. Anything downstream that wants the
    public book asks for this type and cannot accidentally receive the unfiltered one.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    period_end: date
    as_of: date = Field(description="The date this view was taken as at")
    positions: list[CanonicalPosition] = Field(default_factory=list)
    withheld: int = Field(
        default=0,
        description="Positions held but not yet public at as_of — counted so the audit can show "
        "the filter did something rather than assume it",
    )

    @classmethod
    def of(cls, quarter: CanonicalQuarter, *, as_of: date) -> PublicQuarterView:
        """The only supported construction path, and the only place the filter lives."""
        visible = quarter.positions_knowable_on(as_of)
        return cls(
            period_end=quarter.period_end,
            as_of=as_of,
            positions=visible,
            withheld=len(quarter.positions) - len(visible),
        )

    @property
    def total_value(self) -> float:
        return sum(p.market_value for p in self.positions)


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

    accumulated: dict[tuple[str, str], CanonicalPosition] = {}
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

        incoming = {
            p.identity.key: CanonicalPosition(
                position=p,
                disclosed_at=filing.filed_at,
                source_accession=filing.accession,
                confidential_treatment=filing.is_amendment
                and filing.amendment_type is AmendmentType.new_holdings,
            )
            for p in snapshot.positions
        }

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
            collisions = sorted(set(accumulated) & set(incoming), key=lambda k: k.token)
            if collisions:
                # A correction and an addition are indistinguishable here, and picking one would
                # either drop a real position or double-count it.
                return CanonicalQuarter(
                    period_end=lineage.period_end,
                    needs_review=True,
                    review_reason=(
                        f"amendment {filing.accession} repeats {len(collisions)} security(ies) "
                        f"already filed — e.g. {collisions[0].cusip}. Correction and addition are "
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
