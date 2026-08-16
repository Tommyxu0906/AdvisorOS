"""Parsing an institutional holdings filing without inheriting its traps.

Four things in real filings will corrupt a dataset silently, and all four were found by reading
actual Berkshire filings rather than by reasoning about the format.

**The value unit changed, and the rule is published.** A 2016 filing reports `147985198` for a
portfolio worth $148bn — *thousands*. A 2026 filing reports `263095703570` for $263bn — dollars.
Reading both the same way understates a decade of positions by 1000x, and, far worse, makes the
transition quarter look like every position was multiplied by a thousand, which a drift
classifier reads as the largest buying spree in history.

The unit is decided by **the SEC's own rule, keyed on filing date**: values changed from nearest
thousand to nearest dollar for filings submitted on or after 2023-01-03. Inferring it from the
data instead is the tempting shortcut and the wrong one — a sub-dollar security, an unusual share
class, a bad share count, or a malformed row can each drag an inference to the wrong answer, and
being wrong here is a 1000x error on a whole quarter. Where a specification exists, the data does
not get a vote.

The implied-price check survives as *validation*, which is what it is good for: it cannot be the
rule, but it will catch a filer who adopted early, a filing whose date metadata is wrong, and a
parser bug. Disagreement is flagged for review rather than silently resolved in either direction.

Note that the rule keys on when the filing was **submitted**, not the period it covers. Berkshire
filed for period 2022-12-31 on 2023-02-14, after the cutover — that quarter is in dollars while
the quarter before it, filed 2022-11-14, is in thousands. A period-keyed rule gets it wrong.

**One CUSIP appears many times.** Berkshire's filing lists the same security once per managing
subsidiary — ALLY appears three times in one quarter. Reading rows as positions triple-counts
the book and produces weights that sum far above one. Rows aggregate by (CUSIP, class).

**Not every row is equity.** `sshPrnamtType` distinguishes shares from principal amounts; a debt
holding reported as `PRN` has a "share count" that is a face value, and treating it as shares
produces nonsense implied prices and nonsense position changes.

**`otherManager` is not an attribution signal.** It looked like one: the combined filing names
its managers and Warren Buffett is sequence 4. Checked against the real filings, sequence 4
appears on *every position*, in 2016 and in 2026 alike. The field records shared investment
discretion — who had authority — and never who chose to buy. It is preserved as filing metadata
and is deliberately not promoted into individual attribution anywhere in this package.

Separating one manager's decisions from a colleague's needs independent evidence: a shareholder
letter, an interview, an annual-meeting answer, a deal announcement. Position size is *not* that
evidence. Guessing "large positions are his" and then training a persona on the result would
make the evaluation circular — the model would be scored on reproducing a rule we invented, and
a good score would mean nothing at all.
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

# SEC EDGAR technical specification 22.4.1: Form 13F `value` changed from nearest-thousand
# dollars to nearest dollar for filings submitted on or after this date. Keyed on submission,
# not on the period covered — see the module docstring for the Berkshire quarter that proves it.
VALUE_UNIT_CHANGE_DATE = date(2023, 1, 3)

# Used only to validate the rule's answer. A real equity can trade under a dollar; a whole
# portfolio with a sub-dollar median implied price cannot.
IMPLIED_PRICE_FLOOR = 1.0
MIN_ROWS_TO_VALIDATE = 3


class ValueUnit(str, Enum):
    thousands_usd = "thousands_usd"
    dollars_usd = "dollars_usd"

    @property
    def multiplier(self) -> float:
        return 1000.0 if self is ValueUnit.thousands_usd else 1.0


class ValidationOutcome(str, Enum):
    passed = "passed"
    """Implied prices agree with the unit the rule chose."""

    disagreed = "disagreed"
    """They do not. Flagged for review — never silently overridden in either direction."""

    insufficient_data = "insufficient_data"
    """Too few priceable rows to say anything. Not a failure; the rule still stands."""


class ValueNormalization(BaseModel):
    """Which unit was applied, on whose authority, and whether the data agreed.

    Both halves are recorded because they answer different questions. `rule_source` says why the
    parser did what it did and is auditable against a published specification. `validation` says
    whether the filing actually looks like that, and catches early adopters, bad date metadata,
    and parser bugs — none of which the rule alone can see.
    """

    model_config = ConfigDict(extra="forbid")

    unit: ValueUnit
    multiplier: float
    rule_source: str
    validation: ValidationOutcome = ValidationOutcome.insufficient_data
    validation_detail: str = ""

    @property
    def needs_review(self) -> bool:
        return self.validation is ValidationOutcome.disagreed


class ParsedPosition(BaseModel):
    """One aggregated position, normalized to dollars."""

    model_config = ConfigDict(extra="forbid")

    identity: SecurityIdentity
    market_value: float = Field(ge=0, description="Dollars, after unit normalization")
    # The figure exactly as filed, kept so a normalization decision can be re-derived or undone
    # without re-fetching. A stored value with no raw counterpart cannot be audited.
    raw_value: float = Field(ge=0, description="Value as reported, in the filing's own unit")
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

    normalization: ValueNormalization
    parser_version: str = PARSER_VERSION

    skipped_non_equity_rows: int = 0

    @property
    def is_amendment(self) -> bool:
        return self.form_type.upper().endswith("/A")

    @property
    def needs_review(self) -> bool:
        """The unit rule and the data disagree, so nothing here should be trusted yet."""
        return self.normalization.needs_review

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


def unit_for_filing(filed_at: date) -> tuple[ValueUnit, str]:
    """The unit the SEC specification says applies, from the submission date.

    The rule, not an inference. Being wrong here is a 1000x error across a whole quarter, and a
    published rule is available, so the data does not get a vote.
    """
    if filed_at >= VALUE_UNIT_CHANGE_DATE:
        return (
            ValueUnit.dollars_usd,
            f"SEC EDGAR spec 22.4.1: filings submitted on or after "
            f"{VALUE_UNIT_CHANGE_DATE} report value in whole dollars",
        )
    return (
        ValueUnit.thousands_usd,
        f"SEC Form 13F pre-{VALUE_UNIT_CHANGE_DATE} specification: value in thousands of dollars",
    )


def validate_unit(
    rows: list[tuple[float, float]], unit: ValueUnit
) -> tuple[ValidationOutcome, str]:
    """Check the rule's answer against implied prices per share.

    Cannot override the rule and is not meant to. It exists to notice the cases the rule cannot
    see — a filer who adopted early, date metadata that is wrong, a parser that broke — and to
    say so loudly enough that someone looks.
    """
    prices = [
        (value * unit.multiplier) / shares for value, shares in rows if shares > 0 and value > 0
    ]
    if len(prices) < MIN_ROWS_TO_VALIDATE:
        return (
            ValidationOutcome.insufficient_data,
            f"only {len(prices)} priceable rows — too few to check the unit against",
        )

    median = statistics.median(prices)
    if median < IMPLIED_PRICE_FLOOR:
        return (
            ValidationOutcome.disagreed,
            f"under the {unit.value} rule the median implied price is ${median:.4f}/share, which "
            "no real portfolio trades at — the filing may predate or postdate its stated date, "
            "or the parser is wrong. Flagged for review rather than silently re-scaled",
        )
    return (
        ValidationOutcome.passed,
        f"median implied price ${median:,.2f}/share is consistent with {unit.value}",
    )


def normalization_for(filed_at: date, rows: list[tuple[float, float]]) -> ValueNormalization:
    """The unit to apply and the full provenance behind it."""
    unit, rule_source = unit_for_filing(filed_at)
    outcome, detail = validate_unit(rows, unit)
    return ValueNormalization(
        unit=unit,
        multiplier=unit.multiplier,
        rule_source=rule_source,
        validation=outcome,
        validation_detail=detail,
    )


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

    normalization = normalization_for(filed_at, [(r["value"], r["shares"]) for r in raw_rows])
    multiplier = normalization.multiplier

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
            raw_value=bucket["value"],
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
        normalization=normalization,
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
