"""Reading EDGAR's own metadata correctly, without touching the network.

The fetch path is exercised by the backfill script against the live service; what is tested here
is the parsing of what it returns, because those are the places a silent mistake changes the
dataset. `test_the_column_oriented_block_is_read_as_records` in particular: EDGAR returns
parallel arrays rather than a list of filings, and a misaligned index pairs one filing's period
with another's date.
"""

from __future__ import annotations

from datetime import date

from app.distillation.finance_nuwa.edgar import (
    _nodashes,
    _refs_from_block,
    amendment_type_from_primary_doc,
)
from app.distillation.finance_nuwa.store import AmendmentType

BLOCK = {
    "form": ["13F-HR", "8-K", "13F-HR/A", "13F-NT"],
    "reportDate": ["2023-09-30", "2023-10-01", "2023-09-30", "2023-12-31"],
    "filingDate": ["2023-11-14", "2023-10-05", "2023-11-16", "2024-01-10"],
    "accessionNumber": ["acc-1", "acc-8k", "acc-2", "acc-nt"],
}


def test_the_column_oriented_block_is_read_as_records():
    """Parallel arrays, not a list of objects. A misaligned index silently pairs one filing's
    period with another's filing date, which corrupts the decision window."""
    refs = _refs_from_block(BLOCK, ("13F-HR",))

    assert [r.accession for r in refs] == ["acc-1", "acc-2"]
    first = refs[0]
    assert first.period_end == date(2023, 9, 30)
    assert first.filed_at == date(2023, 11, 14)
    assert not first.is_amendment
    assert refs[1].is_amendment


def test_unrelated_forms_are_left_alone():
    """8-K and 13F-NT share the filing list. 13F-NT is a notice that holdings are reported
    elsewhere and contains no positions at all."""
    assert all(r.form_type.startswith("13F-HR") for r in _refs_from_block(BLOCK, ("13F-HR",)))


def test_a_row_missing_a_date_is_skipped_rather_than_defaulted():
    """A filing with no period cannot be placed in time, and inventing one puts an episode in
    the wrong split."""
    broken = {
        "form": ["13F-HR", "13F-HR"],
        "reportDate": ["", "2023-09-30"],
        "filingDate": ["2023-11-14", "2023-11-14"],
        "accessionNumber": ["bad", "good"],
    }
    assert [r.accession for r in _refs_from_block(broken, ("13F-HR",))] == ["good"]


def test_an_empty_block_yields_nothing():
    assert _refs_from_block({}, ("13F-HR",)) == []


# --- amendment type decides whether a quarter is replaced or supplemented --------------------


def test_a_restatement_is_recognised():
    xml = "<edgarSubmission><amendmentType>RESTATEMENT</amendmentType></edgarSubmission>"
    assert amendment_type_from_primary_doc(xml) is AmendmentType.restatement


def test_an_additive_amendment_is_recognised():
    xml = "<edgarSubmission><amendmentType>NEW HOLDINGS</amendmentType></edgarSubmission>"
    assert amendment_type_from_primary_doc(xml) is AmendmentType.new_holdings


def test_an_absent_or_unreadable_type_becomes_unknown_not_a_guess():
    """Getting this backwards either drops real positions or double-counts them, so an
    unreadable value sends the quarter to review instead of picking the likelier option."""
    assert amendment_type_from_primary_doc("<edgarSubmission/>") is AmendmentType.unknown
    assert (
        amendment_type_from_primary_doc("<amendmentType>SOMETHING ELSE</amendmentType>")
        is AmendmentType.unknown
    )


def test_the_accession_path_form_drops_dashes():
    assert _nodashes("0000950123-23-011029") == "000095012323011029"
