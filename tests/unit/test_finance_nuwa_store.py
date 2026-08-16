"""Immutable raw filings, versioned datasets, and a trail back to the SEC document.

`test_a_raw_filing_cannot_be_rewritten` is the load-bearing one. Parser bugs are certain, and
the fix must never require re-fetching: a re-fetch silently changes the inputs underneath every
result already reported, which turns a reproducible number into an unfalsifiable one.
"""

from __future__ import annotations

from datetime import date

import pytest

from app.distillation.finance_nuwa.store import (
    AmendmentType,
    DatasetVersion,
    FilingRef,
    FilingStore,
    resolve_quarter,
)


def ref(accession: str, *, filed: date, form: str = "13F-HR", **kw) -> FilingRef:
    return FilingRef(
        accession=accession,
        form_type=form,
        period_end=kw.pop("period_end", date(2023, 9, 30)),
        filed_at=filed,
        **kw,
    )


# --- raw is written once -----------------------------------------------------------------------


def test_a_raw_filing_cannot_be_rewritten(tmp_path):
    store = FilingStore(tmp_path)
    store.write_raw("acc-1", "info.xml", b"<informationTable/>")

    with pytest.raises(ValueError, match="immutable"):
        store.write_raw("acc-1", "info.xml", b"<informationTable>changed</informationTable>")


def test_rewriting_identical_content_is_a_safe_no_op(tmp_path):
    """A resumed backfill re-writes what it already has, and that must not be an error."""
    store = FilingStore(tmp_path)
    first = store.write_raw("acc-1", "info.xml", b"<x/>")
    again = store.write_raw("acc-1", "info.xml", b"<x/>")

    assert first == again
    assert store.read_raw("acc-1", "info.xml") == b"<x/>"


def test_raw_content_is_fingerprinted_for_the_audit_trail(tmp_path):
    store = FilingStore(tmp_path)
    store.write_raw("acc-1", "info.xml", b"<x/>")

    assert store.content_hash("acc-1", "info.xml") == store.content_hash("acc-1", "info.xml")
    assert store.content_hash("acc-1", "missing.xml") is None


def test_a_missing_raw_document_reads_as_none_rather_than_raising(tmp_path):
    store = FilingStore(tmp_path)
    assert store.read_raw("never-fetched", "info.xml") is None
    assert not store.has_raw("never-fetched", "info.xml")


# --- amendments keep their lineage ---------------------------------------------------------------


def test_a_quarter_with_no_amendment_resolves_to_its_only_filing():
    lineage = resolve_quarter([ref("acc-1", filed=date(2023, 11, 14))])

    assert lineage.canonical_accession == "acc-1"
    assert lineage.amendment_count == 0
    assert not lineage.needs_review


def test_a_restatement_replaces_the_original_outright():
    """Berkshire amended 2023 Q3 twice, five months apart."""
    lineage = resolve_quarter(
        [
            ref("original", filed=date(2023, 11, 14)),
            ref(
                "amend-1",
                filed=date(2023, 11, 16),
                form="13F-HR/A",
                amendment_type=AmendmentType.restatement,
            ),
            ref(
                "amend-2",
                filed=date(2024, 5, 15),
                form="13F-HR/A",
                amendment_type=AmendmentType.restatement,
            ),
        ]
    )

    assert lineage.canonical_accession == "amend-2"
    assert lineage.amendment_count == 2
    assert not lineage.needs_review
    # The whole trail survives, so a strange episode traces to a specific SEC document.
    assert [f.accession for f in lineage.filings] == ["original", "amend-1", "amend-2"]
    assert "replaces 2 earlier filing(s)" in lineage.reason


def test_an_additive_amendment_is_flagged_rather_than_merged_silently():
    """An amendment that adds holdings supplements an original that is still authoritative.
    Combining them is a judgement, and guessing at it would corrupt a quarter quietly."""
    lineage = resolve_quarter(
        [
            ref("original", filed=date(2023, 11, 14)),
            ref(
                "amend-1",
                filed=date(2023, 11, 16),
                form="13F-HR/A",
                amendment_type=AmendmentType.new_holdings,
            ),
        ]
    )

    assert lineage.needs_review
    assert lineage.canonical_accession == "original"
    assert "supplements rather than replaces" in lineage.reason


def test_an_amendment_of_unknown_type_is_reviewed_not_assumed():
    lineage = resolve_quarter(
        [
            ref("original", filed=date(2023, 11, 14)),
            ref("amend-1", filed=date(2023, 11, 16), form="13F-HR/A"),
        ]
    )
    assert lineage.needs_review


def test_filings_are_ordered_by_submission_regardless_of_input_order():
    lineage = resolve_quarter(
        [
            ref(
                "late",
                filed=date(2024, 5, 15),
                form="13F-HR/A",
                amendment_type=AmendmentType.restatement,
            ),
            ref("original", filed=date(2023, 11, 14)),
        ]
    )
    assert [f.accession for f in lineage.filings] == ["original", "late"]


def test_resolving_nothing_is_an_error_not_an_empty_answer():
    with pytest.raises(ValueError, match="no filings"):
        resolve_quarter([])


# --- a dataset version means one thing forever -------------------------------------------------------


def _version(**kw) -> DatasetVersion:
    base = dict(
        name="berkshire-v1.0",
        entity="Berkshire Hathaway Inc",
        cik="1067983",
        requested_start=date(2017, 1, 1),
        requested_end=date(2017, 12, 31),
        actual_quarters=[date(2017, 3, 31), date(2017, 6, 30), date(2017, 9, 30)],
    )
    base.update(kw)
    return DatasetVersion(**base)


def test_coverage_reports_what_was_obtained_not_what_was_asked_for():
    """Claiming 2014-2024 while holding 2017-2024 is the quiet overstatement that makes every
    other number in a report suspect."""
    version = _version()

    assert version.coverage == "2017Q1-2017Q3"
    assert version.missing_quarters == [date(2017, 12, 31)]
    assert "3/4 quarters" in version.describe()


def test_a_version_name_must_carry_a_version_number():
    with pytest.raises(ValueError):
        _version(name="berkshire")
    assert _version(name="berkshire-v1.1").name == "berkshire-v1.1"


def test_an_empty_dataset_says_so_instead_of_inventing_a_range():
    assert _version(actual_quarters=[]).coverage == "no quarters"


def test_the_version_records_the_code_that_built_it():
    version = _version()
    assert version.parser_version.startswith("13f-parser-")
    assert version.builder_version.startswith("episode-builder-")
    assert version.created_at.tzinfo is not None


def test_a_manifest_persists_the_version_and_the_full_lineage(tmp_path):
    store = FilingStore(tmp_path)
    lineage = resolve_quarter(
        [
            ref("original", filed=date(2023, 11, 14)),
            ref(
                "amend-1",
                filed=date(2023, 11, 16),
                form="13F-HR/A",
                amendment_type=AmendmentType.restatement,
            ),
        ]
    )
    target = store.write_version(_version(), [lineage])
    written = target.read_text()

    assert "berkshire-v1.0" in written
    assert "original" in written and "amend-1" in written
    assert "13f-parser-" in written
