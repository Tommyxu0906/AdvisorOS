"""Parsing a holdings filing without inheriting its traps.

Every fixture here is shaped from real Berkshire filings. `test_the_value_unit_is_inferred_...`
is the one that matters most: SEC changed `value` from thousands to dollars, so reading a decade
of filings the same way understates the older half by 1000x — and makes the transition quarter
look like every position was multiplied by a thousand, which the drift classifier would read as
the largest buying spree ever recorded.
"""

from __future__ import annotations

from datetime import date

import pytest

from app.distillation.finance_nuwa.identity import SecurityIdentity
from app.distillation.finance_nuwa.sec_13f import (
    VALUE_UNIT_CHANGE_DATE,
    ValidationOutcome,
    ValueUnit,
    normalization_for,
    parse_information_table,
    parse_manager_table,
    unit_for_filing,
    validate_unit,
)

NS = 'xmlns="http://www.sec.gov/edgar/document/thirteenf/informationtable"'


def table(rows: str) -> str:
    return f"<informationTable {NS}>{rows}</informationTable>"


def row(
    issuer: str,
    cusip: str,
    value: str,
    shares: str,
    *,
    title: str = "COM",
    kind: str = "SH",
    managers: str = "4",
) -> str:
    return f"""
    <infoTable>
      <nameOfIssuer>{issuer}</nameOfIssuer>
      <titleOfClass>{title}</titleOfClass>
      <cusip>{cusip}</cusip>
      <value>{value}</value>
      <shrsOrPrnAmt><sshPrnamt>{shares}</sshPrnamt><sshPrnamtType>{kind}</sshPrnamtType></shrsOrPrnAmt>
      <otherManager>{managers}</otherManager>
    </infoTable>"""


def parse(xml: str, **kw):
    base = dict(
        entity="Berkshire Hathaway Inc",
        cik="1067983",
        accession="0000000000-00-000000",
        period_end=date(2016, 12, 31),
        filed_at=date(2017, 2, 14),
    )
    base.update(kw)
    return parse_information_table(xml, **base)


# --- the unit changed under us -----------------------------------------------------------------


def test_the_value_unit_comes_from_the_published_rule_not_from_the_data():
    """The SEC changed value from nearest-thousand to nearest-dollar for filings submitted on or
    after 2023-01-03. Being wrong here is a 1000x error across a whole quarter, and a published
    rule exists, so the data does not get a vote."""
    old = parse(
        table(
            row("AMERICAN AIRLS GROUP INC", "02376R102", "1061824", "22742000")
            + row("KRAFT HEINZ CO", "500754106", "28434000", "325634818")
            + row("COCA COLA CO", "191216100", "16584000", "400000000")
        )
    )

    assert old.normalization.unit is ValueUnit.thousands_usd
    assert "pre-2023-01-03 specification" in old.normalization.rule_source
    assert old.normalization.validation is ValidationOutcome.passed
    assert not old.needs_review
    # The figure as filed survives beside the normalized one, so the decision is reversible.
    ko_raw = next(p for p in old.positions if "COCA" in p.identity.issuer_name).raw_value
    assert ko_raw == pytest.approx(16_584_000)
    # Normalized to dollars, Coca-Cola is $16.6bn at a plausible $41 a share.
    ko = next(p for p in old.positions if "COCA" in p.identity.issuer_name)
    assert ko.market_value == pytest.approx(16_584_000_000)
    assert ko.implied_price == pytest.approx(41.46, abs=0.01)


def test_a_modern_filing_in_dollars_is_left_alone():
    new = parse(
        table(
            row("APPLE INC", "037833100", "57840000000", "227917808")
            + row("AMERICAN EXPRESS CO", "025816109", "45860000000", "151610700")
            + row("COCA COLA CO", "191216100", "30420000000", "400000000")
        ),
        period_end=date(2026, 3, 31),
        filed_at=date(2026, 5, 15),
    )

    assert new.normalization.unit is ValueUnit.dollars_usd
    assert "22.4.1" in new.normalization.rule_source
    apple = next(p for p in new.positions if "APPLE" in p.identity.issuer_name)
    assert apple.market_value == pytest.approx(57_840_000_000)
    assert apple.implied_price == pytest.approx(253.8, abs=0.5)


def test_the_rule_keys_on_submission_date_not_the_period_covered():
    """Berkshire filed for period 2022-12-31 on 2023-02-14, after the cutover, and that quarter
    reports dollars — while the quarter before it, filed 2022-11-14, reports thousands. A
    period-keyed rule gets the boundary quarter wrong by a factor of a thousand."""
    before, _ = unit_for_filing(date(2022, 11, 14))
    after, _ = unit_for_filing(date(2023, 2, 14))

    assert before is ValueUnit.thousands_usd
    assert after is ValueUnit.dollars_usd
    assert unit_for_filing(VALUE_UNIT_CHANGE_DATE)[0] is ValueUnit.dollars_usd


def test_validation_flags_a_disagreement_instead_of_silently_re_scaling():
    """The check cannot override the rule. It exists to notice early adopters, wrong date
    metadata, and parser bugs — and to say so loudly enough that someone looks."""
    # Values that are really thousands, filed after the cutover so the rule says dollars.
    rows = [
        (16_584_000.0, 400_000_000.0),
        (28_434_000.0, 325_634_818.0),
        (1_061_824.0, 22_742_000.0),
    ]
    outcome, detail = validate_unit(rows, ValueUnit.dollars_usd)

    assert outcome is ValidationOutcome.disagreed
    assert "Flagged for review rather than silently re-scaled" in detail

    normalization = normalization_for(date(2023, 5, 15), rows)
    assert normalization.unit is ValueUnit.dollars_usd  # the rule still stands
    assert normalization.needs_review  # but nothing downstream should trust it


def test_too_few_priceable_rows_leaves_the_rule_standing():
    outcome, detail = validate_unit([(1000.0, 10.0)], ValueUnit.dollars_usd)
    assert outcome is ValidationOutcome.insufficient_data
    assert "too few" in detail


# --- one CUSIP, many rows -----------------------------------------------------------------------


def test_rows_for_one_security_aggregate_into_one_position():
    """Berkshire lists a security once per managing subsidiary — Wells Fargo appeared on 14 rows
    in 2016. Reading rows as positions multiplies the book and breaks every weight."""
    snapshot = parse(
        table(
            row("WELLS FARGO &amp; CO NEW", "949746101", "10000000", "180000000", managers="1")
            + row("WELLS FARGO &amp; CO NEW", "949746101", "10000000", "180000000", managers="4")
            + row("WELLS FARGO &amp; CO NEW", "949746101", "6400000", "119704270", managers="2,4")
            + row("COCA COLA CO", "191216100", "16584000", "400000000")
        )
    )

    wells = next(p for p in snapshot.positions if "WELLS" in p.identity.issuer_name)
    assert wells.row_count == 3
    assert wells.shares == pytest.approx(479_704_270)
    assert wells.manager_sequences == (1, 2, 4)
    assert len(snapshot.positions) == 2


def test_share_classes_are_not_merged_into_one_position():
    """Two classes of the same issuer have different votes and often different prices; summing
    them would invent a position that does not exist."""
    snapshot = parse(
        table(
            row("ALPHABET INC", "02079K305", "1000000", "10000", title="CAP STK CL C")
            + row("ALPHABET INC", "02079K107", "1000000", "10000", title="CAP STK CL A")
        )
    )
    assert len(snapshot.positions) == 2


def test_the_identity_key_is_cusip_and_class_not_ticker():
    identity = SecurityIdentity(cusip="037833100", issuer_name="APPLE INC", title_of_class="COM")
    assert identity.key.token == "037833100:COM"
    assert identity.display == "APPLE INC"  # no ticker mapped yet, and that is fine

    mapped = identity.with_ticker("AAPL", source="test-map", confidence=0.9)
    assert mapped.display == "AAPL"
    assert mapped.mapping_source == "test-map"
    assert identity.ticker is None  # the original is frozen and unchanged


@pytest.mark.parametrize("bad", ["AAPL", "037833100X", "037833!00", ""])
def test_a_malformed_cusip_is_refused(bad: str):
    """One rule owns the definition, so every bad shape gets the same explanation."""
    with pytest.raises(ValueError, match="not a 9-character CUSIP"):
        SecurityIdentity(cusip=bad, issuer_name="APPLE INC")


# --- not every row is equity ---------------------------------------------------------------------


def test_principal_amount_rows_are_skipped_rather_than_read_as_shares():
    """A PRN row's 'share count' is a face value; treating it as shares gives a meaningless
    implied price and a meaningless change between quarters."""
    snapshot = parse(
        table(
            row("COCA COLA CO", "191216100", "16584000", "400000000")
            + row("SOME NOTE 4.5% 2030", "999999AA1", "5000000", "5000000", kind="PRN")
            + row("KRAFT HEINZ CO", "500754106", "28434000", "325634818")
            + row("APPLE INC", "037833100", "6600000", "57400000")
        )
    )

    assert snapshot.skipped_non_equity_rows == 1
    assert all("NOTE" not in p.identity.issuer_name for p in snapshot.positions)


# --- who had discretion ----------------------------------------------------------------------------


def test_the_manager_table_makes_the_otherManager_integers_readable():
    primary_doc = """
    <edgarSubmission>
      <otherManager2><sequenceNumber>3</sequenceNumber><name>BH Finance LLC</name></otherManager2>
      <otherManager2><sequenceNumber>4</sequenceNumber><name>Buffett Warren E</name></otherManager2>
      <otherManager2><sequenceNumber>10</sequenceNumber>
        <name>National Fire &amp; Marine Insurance Co</name></otherManager2>
    </edgarSubmission>"""

    managers = parse_manager_table(primary_doc)
    assert managers[4] == "Buffett Warren E"
    assert managers[10] == "National Fire & Marine Insurance Co"  # entities are unescaped


def test_discretion_is_filing_metadata_and_never_individual_attribution():
    """Checked against the real filings: sequence 4 (Buffett) appears on *every* Berkshire
    position, in 2016 and in 2026. The field records who had authority, never who chose to buy,
    so it stays metadata and is not promoted into attribution anywhere."""
    snapshot = parse(
        table(
            row("COCA COLA CO", "191216100", "16584000", "400000000", managers="4,5,6")
            + row("APPLE INC", "037833100", "6600000", "57400000", managers="4")
            + row("KRAFT HEINZ CO", "500754106", "28434000", "325634818", managers="4")
        ),
        manager_names={4: "Buffett Warren E", 5: "Columbia Insurance Co"},
    )

    assert snapshot.managers_matching("buffett") == {4}
    # Every position — so this cannot separate his decisions from a deputy's.
    assert len(snapshot.positions_managed_by("buffett")) == len(snapshot.positions)
    assert snapshot.positions_managed_by("nobody named this") == []


# --- provenance travels with the snapshot -------------------------------------------------------------


def test_a_snapshot_records_what_parsed_it_and_whether_it_was_amended():
    original = parse(table(row("COCA COLA CO", "191216100", "16584000", "400000000")))
    amended = parse(
        table(row("COCA COLA CO", "191216100", "16584000", "400000000")),
        form_type="13F-HR/A",
    )

    assert original.parser_version.startswith("13f-parser-")
    assert not original.is_amendment
    assert amended.is_amendment
    assert original.period_end == date(2016, 12, 31)
    assert original.filed_at == date(2017, 2, 14)  # knowable six weeks after the period ended


# --- the circularity this package must never introduce ---------------------------------------


def test_no_code_in_the_package_derives_attribution_from_position_size():
    """The trap that would void every later evaluation.

    Guess "large positions are Buffett's", train a persona on the result, then report that the
    persona reproduces Buffett's decisions — and the score measures nothing but our own guess
    played back. Position size may be explored as a low-confidence hypothesis, but it must never
    reach a label.

    Stated precisely, so this catches the real thing and not merely filtering by the filing's own
    manager field: no function that sets attribution may compare a position size against
    anything.
    """
    import ast
    from pathlib import Path

    import app.distillation.finance_nuwa as package

    ATTRIBUTION = {"attributionbasis", "attribution_confidence", "attribution"}
    SIZE = {"weight", "market_value", "raw_value", "shares", "position_size", "rank"}

    for path in Path(package.__file__).parent.glob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef):
                continue

            sets_attribution = any(
                (isinstance(n, ast.keyword) and n.arg and n.arg.lower() in ATTRIBUTION)
                or (isinstance(n, ast.Attribute) and n.attr.lower() in ATTRIBUTION)
                or (isinstance(n, ast.Name) and n.id.lower() in ATTRIBUTION)
                for n in ast.walk(node)
            )
            if not sets_attribution:
                continue

            for comparison in (n for n in ast.walk(node) if isinstance(n, ast.Compare)):
                names = {x.id.lower() for x in ast.walk(comparison) if isinstance(x, ast.Name)} | {
                    x.attr.lower() for x in ast.walk(comparison) if isinstance(x, ast.Attribute)
                }
                assert not (names & SIZE), (
                    f"{path.name}:{node.name} sets attribution and compares {names & SIZE} — "
                    "inferring whose decision it was from how big the position is makes every "
                    "later evaluation circular"
                )
