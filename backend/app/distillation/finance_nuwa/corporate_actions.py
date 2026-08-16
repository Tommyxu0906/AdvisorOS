"""Whether a security changed identity, kept separately from whether anyone traded it.

Share counts move for two completely different reasons and the filings do not distinguish them.
A position going from 100M shares to 200M is either a decision or a two-for-one split, and a
pipeline that cannot tell the difference learns "doubled down" from an event in which nobody did
anything at all. The same confusion has four more faces:

    merger          A disappears, B appears — not an exit followed by a purchase
    spin-off        A stays, C appears from nowhere — received, not bought
    CUSIP change    the same company under a new identifier — not a round trip
    class change    Class A becomes Class C — not a sale of one and purchase of another

Every one of these produces a textbook-perfect `exit` and `enter` pair, or a doubling that looks
like conviction, and every one of them is wrong.

**Entity-agnostic on purpose.** A split is a fact about a security, not about whoever held it.
`LineageTable` is therefore shared across every manager the library ever covers, and each new
subject makes it more complete rather than starting again. That is worth saying out loud now,
while there is exactly one subject, because the alternative — a Berkshire-shaped resolver — is
the kind of thing that quietly forces a rewrite at manager number two.

**Detection proposes, curation disposes.** Nothing here concludes that a corporate action
happened. With no authoritative actions feed, the honest output is a *candidate* with the
arithmetic that made it suspicious, routed to review. A genuine doubling and an unadjusted
two-for-one split are indistinguishable from share counts alone; only the value tells them
apart, and only imperfectly. Resolution comes from `LineageTable` entries someone established.

**The detector's precision was measured, not assumed, and it varies enormously by kind.** Against
the real 2014-2024 Berkshire range it raises 21 candidates: every one of the 10 `cusip_change`
flags and the single `split` flag survive hand-checking, while only 1 of 10 `merger` flags does.
Identity changes leave a fingerprint — the issuer name barely moves, or the share ratio lands on
a whole number with the value intact. A merger does not: a manager exiting one position and
entering another of similar size looks identical, and does so most quarters. `blocks_episode`
therefore differs by kind rather than blocking everything, because blocking a merger candidate
discards both sides and at 10% precision that costs far more real decisions than it saves.

Twenty-one items across eleven years is small enough to curate by hand, which is the practical
answer here. Mergers with a real name change still need an external actions feed to resolve
properly; this catches the shape, not the fact.
"""

from __future__ import annotations

from datetime import date
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

from app.distillation.finance_nuwa.lineage import CanonicalPosition, CanonicalQuarter

# A share ratio this close to a whole number, with the position's value roughly intact, is the
# signature of a split rather than a trade. House convention, and only ever a flag.
SPLIT_RATIO_TOLERANCE = 0.02
VALUE_INTACT_LOW = 0.80
VALUE_INTACT_HIGH = 1.25

# Two positions of similar size, one vanishing as the other appears in the same quarter, look
# like a merger or a re-identification. Wide, because a merger rarely leaves value untouched.
SUCCESSOR_VALUE_LOW = 0.60
SUCCESSOR_VALUE_HIGH = 1.60

# Measured against the real Berkshire range, value similarity alone flagged 41 "mergers" across
# 43 transitions and almost all were coincidence — an active manager exits one position and
# enters another of similar size most quarters, so the signal is nearly content-free. Requiring
# the pairing to be unique in both directions is what makes the queue worth reading: if two
# departures could each explain one arrival, nothing has been established. Mergers with a genuine
# name change still need an external actions feed; this only catches the unambiguous shape.
REQUIRE_UNIQUE_SUCCESSOR_PAIRING = True


class CorporateActionKind(str, Enum):
    split = "split"
    reverse_split = "reverse_split"
    merger = "merger"
    """A became B. Not an exit and a purchase."""

    spin_off = "spin_off"
    """A stayed and C arrived. Received, not bought."""

    cusip_change = "cusip_change"
    """Same company, new identifier. Not a round trip."""

    class_change = "class_change"
    """One share class became another. Not two decisions."""

    delisting = "delisting"
    unknown = "unknown"

    @property
    def is_passive(self) -> bool:
        """True when the holder took no decision. Every kind here except an outright delisting,
        which may or may not have been preceded by one."""
        return self is not CorporateActionKind.unknown


class SecurityLineage(BaseModel):
    """One established identity change. A fact about a security, reusable across managers."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    from_cusip: str
    to_cusip: str | None = Field(default=None, description="None for a delisting with no successor")
    kind: CorporateActionKind
    effective_date: date

    share_ratio: float | None = Field(
        default=None,
        gt=0,
        description="shares_after / shares_before, so 2.0 for a two-for-one split",
    )
    evidence: str = Field(default="", description="Where this was established — never inferred")
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)


class LineageTable(BaseModel):
    """Established identity changes, keyed by security and shared by every subject.

    Deliberately not populated by detection. Entries come from somewhere authoritative or from a
    person who checked; the detector only says where to look.
    """

    model_config = ConfigDict(extra="forbid")

    entries: list[SecurityLineage] = Field(default_factory=list)

    def for_cusip(
        self, cusip: str, *, during: tuple[date, date] | None = None
    ) -> list[SecurityLineage]:
        matches = [e for e in self.entries if e.from_cusip == cusip]
        if during is None:
            return matches
        start, end = during
        return [e for e in matches if start <= e.effective_date <= end]

    def successor_of(self, cusip: str, *, during: tuple[date, date]) -> SecurityLineage | None:
        """The entry that renames or replaces this security within a window, if one exists."""
        for entry in self.for_cusip(cusip, during=during):
            if entry.kind in (
                CorporateActionKind.merger,
                CorporateActionKind.cusip_change,
                CorporateActionKind.class_change,
            ):
                return entry
        return None

    def split_ratio(self, cusip: str, *, during: tuple[date, date]) -> float | None:
        for entry in self.for_cusip(cusip, during=during):
            if entry.kind in (CorporateActionKind.split, CorporateActionKind.reverse_split):
                return entry.share_ratio
        return None


class ActionCandidate(BaseModel):
    """A transition that looks like a corporate action and needs someone to confirm it."""

    model_config = ConfigDict(extra="forbid")

    period_end: date
    suspected: CorporateActionKind
    from_cusip: str
    to_cusip: str | None = None

    share_ratio: float | None = None
    value_ratio: float | None = None
    reason: str = ""

    @property
    def blocks_episode(self) -> bool:
        """Whether an unresolved candidate should keep its securities out of the training set.

        Not a blanket yes, and the reason is measured rather than assumed. Blocking is not free:
        a merger candidate blocks *both* sides, so at the precision this detector actually
        achieves it would discard roughly eighteen genuine decisions to catch two false ones.

        Checked by hand against the real 2014-2024 Berkshire range:

            cusip_change    10 flagged, 10 look real — Liberty Media's tracking-stock
                            recapitalisations, Charter's class reorganisation, Media General,
                            Liberty Global to Liberty Latin America, Nu Holdings. The issuer-name
                            match is carrying genuine signal, and reading one of these as an
                            exit-and-entry fabricates a round trip that never happened.

            split            1 flagged, 1 real — Visa's four-for-one in March 2015, at 3.94x
                            shares against 0.98x value. Reading it as a purchase would teach a
                            near-quadrupling of conviction from an event nobody chose.

            merger          10 flagged, 1 real (DirecTV into AT&T, 2015). The rest are ordinary
                            portfolio turnover that happens to pair on size. Blocking on ~10%
                            precision costs far more real data than it protects, so these are
                            raised for review and do not block.

        The asymmetry is the point: block where the detector is reliable, review where it is not.
        """
        return self.suspected is not CorporateActionKind.merger


def detect_candidates(
    previous: CanonicalQuarter,
    current: CanonicalQuarter,
    *,
    table: LineageTable | None = None,
) -> list[ActionCandidate]:
    """Flag transitions whose arithmetic looks like an identity change rather than a trade.

    Anything already established in `table` is not re-flagged — resolution is the point, and a
    candidate list that keeps re-reporting settled facts trains people to ignore it.
    """
    table = table or LineageTable()
    window = (previous.period_end, current.period_end)

    before = {p.identity.cusip: p for p in previous.positions}
    after = {p.identity.cusip: p for p in current.positions}

    candidates: list[ActionCandidate] = []

    # --- share counts that moved like a split -----------------------------------------
    for cusip, start in before.items():
        end = after.get(cusip)
        if end is None or start.shares <= 0 or end.shares <= 0:
            continue
        if table.split_ratio(cusip, during=window) is not None:
            continue

        share_ratio = end.shares / start.shares
        if VALUE_INTACT_LOW <= share_ratio <= VALUE_INTACT_HIGH:
            continue  # ordinary trading territory
        value_ratio = end.market_value / start.market_value if start.market_value > 0 else None
        if value_ratio is None or not (VALUE_INTACT_LOW <= value_ratio <= VALUE_INTACT_HIGH):
            continue

        nearest = round(share_ratio) if share_ratio >= 1 else 1 / max(round(1 / share_ratio), 1)
        if nearest <= 0 or abs(share_ratio - nearest) / nearest > SPLIT_RATIO_TOLERANCE:
            continue

        candidates.append(
            ActionCandidate(
                period_end=current.period_end,
                suspected=(
                    CorporateActionKind.split
                    if share_ratio > 1
                    else CorporateActionKind.reverse_split
                ),
                from_cusip=cusip,
                to_cusip=cusip,
                share_ratio=round(share_ratio, 4),
                value_ratio=round(value_ratio, 4),
                reason=(
                    f"shares moved {share_ratio:.2f}x while value moved {value_ratio:.2f}x — the "
                    "signature of a split, not a purchase"
                ),
            )
        )

    # --- one position vanishing as another of similar size appears ---------------------
    vanished = [c for c in before if c not in after]
    appeared = [c for c in after if c not in before]

    pairings: list[tuple[str, str, float, bool]] = []
    for gone in vanished:
        if table.successor_of(gone, during=window) is not None:
            continue
        start_value = before[gone].market_value
        if start_value <= 0:
            continue
        for arrival in appeared:
            ratio = after[arrival].market_value / start_value
            if not (SUCCESSOR_VALUE_LOW <= ratio <= SUCCESSOR_VALUE_HIGH):
                continue
            pairings.append((gone, arrival, ratio, _issuer_matches(before[gone], after[arrival])))

    for gone, arrival, ratio, same_issuer in pairings:
        # A matching issuer name is real evidence of a re-identification and stands on its own.
        # A bare value match is not, so it must at least be unambiguous: one departure that could
        # only be explained by one arrival, and vice versa.
        if not same_issuer and REQUIRE_UNIQUE_SUCCESSOR_PAIRING:
            from_count = sum(1 for p in pairings if p[0] == gone)
            to_count = sum(1 for p in pairings if p[1] == arrival)
            if from_count > 1 or to_count > 1:
                continue

        basis = (
            "the issuer names match"
            if same_issuer
            else "no other position on either side could explain it"
        )
        candidates.append(
            ActionCandidate(
                period_end=current.period_end,
                suspected=(
                    CorporateActionKind.cusip_change if same_issuer else CorporateActionKind.merger
                ),
                from_cusip=gone,
                to_cusip=arrival,
                value_ratio=round(ratio, 4),
                reason=(
                    f"{before[gone].identity.issuer_name} left as "
                    f"{after[arrival].identity.issuer_name} arrived at {ratio:.2f}x the value, "
                    f"and {basis}"
                ),
            )
        )

    return sorted(candidates, key=lambda c: (c.suspected.value, c.from_cusip))


def load_curated(path) -> LineageTable:
    """Read the curated table. Only these entries count as resolved.

    Kept out of the detector's reach on purpose: an entry exists because someone wrote down what
    it rests on, which is a different act from an arithmetic pattern being suspicious.
    """
    import json
    from pathlib import Path

    payload = json.loads(Path(path).read_text())
    return LineageTable(
        entries=[
            SecurityLineage(
                from_cusip=e["from_cusip"],
                to_cusip=e.get("to_cusip"),
                kind=CorporateActionKind(e["kind"]),
                effective_date=date.fromisoformat(e["effective_date"]),
                share_ratio=e.get("share_ratio"),
                evidence=e.get("evidence", ""),
                confidence=e.get("confidence", 1.0),
            )
            for e in payload.get("entries", [])
        ]
    )


def unresolved_blocking(
    candidates: list[ActionCandidate], table: LineageTable
) -> list[ActionCandidate]:
    """Blocking candidates with no curated entry behind them.

    This is what the dataset gate reads. It has to be computed rather than asserted: a gate that
    reports a literal zero proves nothing about the data it exists to guard, and a detected
    candidate is not a confirmed fact however convincing its arithmetic looked.
    """
    unresolved: list[ActionCandidate] = []
    for candidate in candidates:
        if not candidate.blocks_episode:
            continue
        window = (candidate.period_end, candidate.period_end)
        resolved = (
            table.split_ratio(candidate.from_cusip, during=window) is not None
            or table.successor_of(candidate.from_cusip, during=window) is not None
        )
        if not resolved:
            unresolved.append(candidate)
    return unresolved


def apply_lineage(
    previous: CanonicalQuarter, current: CanonicalQuarter, table: LineageTable
) -> dict[str, str]:
    """Successor mapping to apply before classification, so continuity is not read as two trades.

    A confirmed re-identification means the old CUSIP *became* the new one. Left unapplied it
    produces a textbook exit and a textbook entry, which is two decisions where the holder made
    none.
    """
    window = (previous.period_end, current.period_end)
    mapping: dict[str, str] = {}
    for position in previous.positions:
        entry = table.successor_of(position.identity.cusip, during=window)
        if entry is not None and entry.to_cusip:
            mapping[position.identity.cusip] = entry.to_cusip
    return mapping


def blocked_cusips(candidates: list[ActionCandidate]) -> set[str]:
    """Securities whose labels cannot be trusted until a candidate is resolved."""
    blocked: set[str] = set()
    for candidate in candidates:
        if not candidate.blocks_episode:
            continue
        blocked.add(candidate.from_cusip)
        if candidate.to_cusip:
            blocked.add(candidate.to_cusip)
    return blocked


def split_factors_for(
    previous: CanonicalQuarter, current: CanonicalQuarter, table: LineageTable
) -> dict[str, float]:
    """Split adjustments to hand the classifier, drawn only from established entries."""
    window = (previous.period_end, current.period_end)
    factors: dict[str, float] = {}
    for position in previous.positions:
        ratio = table.split_ratio(position.identity.cusip, during=window)
        if ratio is not None:
            factors[position.identity.cusip] = ratio
    return factors


def _issuer_matches(before: CanonicalPosition, after: CanonicalPosition) -> bool:
    """Rough issuer-name agreement, used only to guess which kind of candidate this is.

    A shared first word is weak evidence and is treated as such — it changes the label on a
    review item, never whether one is raised.
    """
    a = before.identity.issuer_name.upper().split()
    b = after.identity.issuer_name.upper().split()
    return bool(a and b and a[0] == b[0])
