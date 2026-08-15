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
    ValueScale,
    detect_value_scale,
    parse_information_table,
    parse_manager_table,
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


def test_the_value_unit_is_inferred_from_implied_price_not_a_hardcoded_date():
    """2016: value 1,061,824 against 22,742,000 shares. As dollars that is 4.7 cents a share for
    an airline — the figure is thousands. A cutover date would be wrong for every filer who
    adopted early or late."""
    old = parse(
        table(
            row("AMERICAN AIRLS GROUP INC", "02376R102", "1061824", "22742000")
            + row("KRAFT HEINZ CO", "500754106", "28434000", "325634818")
            + row("COCA COLA CO", "191216100", "16584000", "400000000")
        )
    )

    assert old.value_scale is ValueScale.thousands
    assert "far below any real equity" in old.scale_evidence
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

    assert new.value_scale is ValueScale.dollars
    apple = next(p for p in new.positions if "APPLE" in p.identity.issuer_name)
    assert apple.market_value == pytest.approx(57_840_000_000)
    assert apple.implied_price == pytest.approx(253.8, abs=0.5)


def test_too_few_priceable_rows_is_reported_as_ambiguous_rather_than_guessed():
    scale, evidence = detect_value_scale([(1000.0, 10.0)])
    assert scale is ValueScale.ambiguous
    assert "too few" in evidence


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
    assert identity.key == ("037833100", "COM")
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


def test_discretion_narrows_the_field_it_does_not_name_a_decision_maker():
    """Checked against the real filings: sequence 4 (Buffett) appears on *every* Berkshire
    position, in 2016 and in 2026. So the field confirms he could have decided, never that he
    did — which is why attribution stays `entity_filing` rather than becoming personal."""
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
