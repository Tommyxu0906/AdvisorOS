"""Parsing an institutional holdings filing without inheriting its traps.

Four things in real filings will corrupt a dataset silently, and all four were found by reading
actual Berkshire filings rather than by reasoning about the format.

**The value unit changed.** A 2016 filing reports `147985198` for a portfolio worth $148bn — the
figure is in *thousands*. A 2026 filing reports `263095703570` for $263bn, in dollars. Reading
both the same way understates a decade of positions by 1000x, and — far worse — makes the
transition quarter look like every position was multiplied by a thousand, which a drift
classifier reads as the largest buying spree in history. `detect_value_scale` infers the unit
from the data instead of hardcoding a cutover date, by checking the implied price per share:
real equities trade in dollars and tens of dollars, so a median implied price under a dollar
means the values are thousands.

**One CUSIP appears many times.** Berkshire's filing lists the same security once per managing
subsidiary — ALLY appears three times in one quarter. Reading rows as positions triple-counts
the book and produces weights that sum far above one. Rows aggregate by (CUSIP, class).

**Not every row is equity.** `sshPrnamtType` distinguishes shares from principal amounts; a debt
holding reported as `PRN` has a "share count" that is a face value, and treating it as shares
produces nonsense implied prices and nonsense position changes.

**`otherManager` is an attribution signal.** The combined filing names its managers, and Warren
Buffett is one of them. A row whose managers include him is materially better evidence about his
decisions than a row managed elsewhere in the group. It is still not proof — the field records
shared discretion, not who placed the order — so it raises confidence rather than establishing it.
"""

from __future__ import annotations

import re
import statistics
from datetime import date
from enum import Enum
from xml.etree import ElementTree

from pydantic import BaseModel, ConfigDict, Field

from app.distillation.finance_nuwa.identity import SecurityIdentity

# Bump when parsing changes shape, so a stored snapshot can be traced to the code that made it
# and rebuilt without re-fetching. See `store.py`.
PARSER_VERSION = "13f-parser-1"

_NS = {"t": "http://www.sec.gov/edgar/document/thirteenf/informationtable"}

# Median implied price below this means values are in thousands. A real equity priced under a
# dollar exists, but a whole portfolio with a sub-dollar median does not.
DOLLAR_SCALE_FLOOR = 1.0


class ValueScale(str, Enum):
    dollars = "dollars"
    thousands = "thousands"
    ambiguous = "ambiguous"
    """Too few priceable rows to tell. The snapshot is kept and flagged, never guessed."""

    @property
    def multiplier(self) -> float:
        return 1000.0 if self is ValueScale.thousands else 1.0


class ParsedPosition(BaseModel):
    """One aggregated position, normalized to dollars."""

    model_config = ConfigDict(extra="forbid")

    identity: SecurityIdentity
    market_value: float = Field(ge=0, description="Dollars, after scale normalization")
    shares: float = Field(ge=0)

    manager_sequences: tuple[int, ...] = Field(
        default=(), description="Managers with discretion, per the filing's own manager table"
    )
    row_count: int = Field(default=1, description="Filing rows aggregated into this position")

    @property
    def implied_price(self) -> float | None:
        return self.market_value / self.shares if self.shares > 0 else None


class HoldingsSnapshot(BaseModel):
    """One filing, parsed. The normalized layer between raw XML and episodes."""

    model_config = ConfigDict(extra="forbid")

    entity: str
    cik: str
    accession: str

    period_end: date
    filed_at: date
    form_type: str = "13F-HR"

    positions: list[ParsedPosition] = Field(default_factory=list)
    manager_names: dict[int, str] = Field(default_factory=dict)

    value_scale: ValueScale = ValueScale.dollars
    scale_evidence: str = ""
    parser_version: str = PARSER_VERSION

    skipped_non_equity_rows: int = 0

    @property
    def is_amendment(self) -> bool:
        return self.form_type.upper().endswith("/A")

    @property
    def total_value(self) -> float:
        return sum(p.market_value for p in self.positions)

    def managers_matching(self, fragment: str) -> set[int]:
        """Manager sequence numbers whose name contains `fragment`, case-insensitively."""
        needle = fragment.lower()
        return {seq for seq, name in self.manager_names.items() if needle in name.lower()}

    def positions_managed_by(self, fragment: str) -> list[ParsedPosition]:
        """Positions where a named manager holds discretion.

        The honest reading: this narrows the field, it does not identify a decision-maker. The
        filing records who had authority over a holding, never who chose to buy it.
        """
        sequences = self.managers_matching(fragment)
        if not sequences:
            return []
        return [p for p in self.positions if sequences & set(p.manager_sequences)]


def detect_value_scale(rows: list[tuple[float, float]]) -> tuple[ValueScale, str]:
    """Infer whether `value` is dollars or thousands, from implied price per share.

    Inferred rather than keyed to the date the rule changed, because a hardcoded cutover is
    wrong for every filer who adopted early or late, silently, and only in one direction.
    """
    prices = [value / shares for value, shares in rows if shares > 0 and value > 0]
    if len(prices) < 3:
        return ValueScale.ambiguous, f"only {len(prices)} priceable rows — too few to infer"

    median = statistics.median(prices)
    if median < DOLLAR_SCALE_FLOOR:
        return (
            ValueScale.thousands,
            f"median implied price ${median:.4f}/share is far below any real equity, so values "
            "are thousands",
        )
    return ValueScale.dollars, f"median implied price ${median:,.2f}/share is a plausible price"


def parse_information_table(
    xml_text: str,
    *,
    entity: str,
    cik: str,
    accession: str,
    period_end: date,
    filed_at: date,
    form_type: str = "13F-HR",
    manager_names: dict[int, str] | None = None,
) -> HoldingsSnapshot:
    """Parse one information table into aggregated, dollar-normalized positions."""
    root = ElementTree.fromstring(xml_text)
    raw_rows: list[dict] = []
    skipped = 0

    for entry in root.findall(".//t:infoTable", _NS) or root.findall(".//infoTable"):
        cusip = _text(entry, "cusip")
        shares_type = _text(entry, "sshPrnamtType") or "SH"
        if not cusip:
            continue
        # PRN rows report a principal amount, not a share count. Treating one as shares gives a
        # meaningless implied price and a meaningless change between quarters.
        if shares_type.upper() != "SH":
            skipped += 1
            continue

        raw_rows.append(
            {
                "cusip": cusip.strip().upper(),
                "issuer": _text(entry, "nameOfIssuer") or "",
                "title": _text(entry, "titleOfClass") or "",
                "value": _number(_text(entry, "value")),
                "shares": _number(_text(entry, "sshPrnamt")),
                "managers": _manager_sequences(_text(entry, "otherManager")),
            }
        )

    scale, evidence = detect_value_scale([(r["value"], r["shares"]) for r in raw_rows])
    multiplier = scale.multiplier

    # Aggregate: the same security is listed once per managing subsidiary, and reading rows as
    # positions triple-counts the book.
    merged: dict[tuple[str, str], dict] = {}
    for row in raw_rows:
        key = (row["cusip"], row["title"].strip().upper())
        bucket = merged.setdefault(
            key,
            {
                "cusip": row["cusip"],
                "issuer": row["issuer"],
                "title": row["title"],
                "value": 0.0,
                "shares": 0.0,
                "managers": set(),
                "rows": 0,
            },
        )
        bucket["value"] += row["value"]
        bucket["shares"] += row["shares"]
        bucket["managers"].update(row["managers"])
        bucket["rows"] += 1

    positions = [
        ParsedPosition(
            identity=SecurityIdentity(
                cusip=bucket["cusip"],
                issuer_name=bucket["issuer"] or bucket["cusip"],
                title_of_class=bucket["title"],
            ),
            market_value=round(bucket["value"] * multiplier, 2),
            shares=bucket["shares"],
            manager_sequences=tuple(sorted(bucket["managers"])),
            row_count=bucket["rows"],
        )
        for bucket in merged.values()
    ]
    positions.sort(key=lambda p: p.market_value, reverse=True)

    return HoldingsSnapshot(
        entity=entity,
        cik=cik,
        accession=accession,
        period_end=period_end,
        filed_at=filed_at,
        form_type=form_type,
        positions=positions,
        manager_names=manager_names or {},
        value_scale=scale,
        scale_evidence=evidence,
        skipped_non_equity_rows=skipped,
    )


def parse_manager_table(primary_doc_xml: str) -> dict[int, str]:
    """Sequence number to manager name, from the cover page of a combined filing.

    This is what makes `otherManager` on each holding readable — without it those integers say
    nothing, and the attribution signal is lost.
    """
    managers: dict[int, str] = {}
    for block in re.findall(r"<otherManager2>(.*?)</otherManager2>", primary_doc_xml, re.S):
        sequence = re.search(r"<sequenceNumber>\s*(\d+)\s*</sequenceNumber>", block)
        name = re.search(r"<name>(.*?)</name>", block, re.S)
        if sequence and name:
            managers[int(sequence.group(1))] = _unescape(name.group(1).strip())
    return managers


def _text(entry, tag: str) -> str | None:
    node = entry.find(f".//t:{tag}", _NS)
    if node is None:
        node = entry.find(f".//{tag}")
    return node.text if node is not None and node.text else None


def _number(value: str | None) -> float:
    if not value:
        return 0.0
    try:
        return float(value.replace(",", "").strip())
    except ValueError:
        return 0.0


def _manager_sequences(raw: str | None) -> set[int]:
    """`otherManager` is a comma-separated list of sequence numbers, and sometimes empty."""
    if not raw:
        return set()
    return {int(part) for part in re.findall(r"\d+", raw)}


def _unescape(text: str) -> str:
    return (
        text.replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", '"')
        .replace("&#39;", "'")
    )
