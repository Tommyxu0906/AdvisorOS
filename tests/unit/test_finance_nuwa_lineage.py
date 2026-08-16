"""Composing a quarter from its original filing and whatever amended it.

`test_dropping_an_additive_amendment_fabricates_a_decision_next_quarter` is why this module
exists. Losing a confidential-treatment position does not merely lose a holding — it moves the
purchase to the wrong quarter, and the drift classifier then records an `enter` in a quarter
where nothing happened.
"""

from __future__ import annotations

from datetime import date

from app.distillation.finance_nuwa.drift import ObservedAction, PositionSnapshot, classify_portfolio
from app.distillation.finance_nuwa.identity import SecurityIdentity, SecurityKey
from app.distillation.finance_nuwa.lineage import compose_quarter
from app.distillation.finance_nuwa.sec_13f import (
    HoldingsSnapshot,
    ParsedPosition,
    ValidationOutcome,
    ValueNormalization,
    ValueUnit,
)
from app.distillation.finance_nuwa.store import AmendmentType, FilingRef, QuarterLineage

PERIOD = date(2023, 9, 30)


def k(name: str) -> SecurityKey:
    """A legible stand-in for a CUSIP, padded to the nine characters the type requires."""
    return SecurityKey(cusip=f"{name:0<9}"[:9], title_of_class="COM")


NORMALIZATION = ValueNormalization(
    unit=ValueUnit.dollars_usd,
    multiplier=1.0,
    rule_source="test",
    validation=ValidationOutcome.passed,
)


def position(cusip: str, value: float, shares: float = 1000.0) -> ParsedPosition:
    return ParsedPosition(
        identity=SecurityIdentity(cusip=cusip, issuer_name=f"ISSUER {cusip}", title_of_class="COM"),
        market_value=value,
        raw_value=value,
        shares=shares,
    )


def snapshot(accession: str, positions: list[ParsedPosition], **kw) -> HoldingsSnapshot:
    return HoldingsSnapshot(
        entity="Berkshire Hathaway Inc",
        cik="1067983",
        accession=accession,
        period_end=PERIOD,
        filed_at=kw.pop("filed_at", date(2023, 11, 14)),
        form_type=kw.pop("form_type", "13F-HR"),
        positions=positions,
        normalization=NORMALIZATION,
    )


def ref(accession: str, *, filed: date, form: str = "13F-HR", amend=AmendmentType.unknown):
    return FilingRef(
        accession=accession, form_type=form, period_end=PERIOD, filed_at=filed, amendment_type=amend
    )


def lineage(*refs) -> QuarterLineage:
    return QuarterLineage(period_end=PERIOD, filings=list(refs))


# --- the case that actually occurs ---------------------------------------------------------------


def test_an_additive_amendment_unions_into_the_original():
    """Every amendment in the real 2014-2024 Berkshire range is this: one to three previously
    confidential positions, with no overlap against the original at all."""
    original = snapshot("orig", [position("111111111", 1e9), position("222222222", 2e9)])
    added = snapshot(
        "amend",
        [position("333333333", 5e8)],
        form_type="13F-HR/A",
        filed_at=date(2024, 5, 15),
    )

    canonical = compose_quarter(
        lineage(
            ref("orig", filed=date(2023, 11, 14)),
            ref(
                "amend", filed=date(2024, 5, 15), form="13F-HR/A", amend=AmendmentType.new_holdings
            ),
        ),
        {"orig": original, "amend": added},
    )

    assert canonical.is_usable
    assert len(canonical.positions) == 3
    assert canonical.total_value == 3.5e9
    assert canonical.positions_from_amendments == 1
    assert canonical.value_from_amendments == 5e8
    assert canonical.contributing_accessions == ["orig", "amend"]
    assert "previously undisclosed" in canonical.resolution


def test_dropping_an_additive_amendment_fabricates_a_decision_next_quarter():
    """The failure this module prevents, shown end to end.

    A position held under confidential treatment in Q3 and disclosed only by a later amendment
    is absent from the Q3 original. Compose the quarter without the amendment and the position
    appears from nowhere in Q4, so the classifier records an `enter` in a quarter where nothing
    was bought — and the investor's timing is learned wrong in exactly the cases they took care
    to conceal.
    """
    q3_without_amendment = [PositionSnapshot(security=k("AAA"), market_value=1e9, shares=1000)]
    q3_with_amendment = [
        PositionSnapshot(security=k("AAA"), market_value=1e9, shares=1000),
        PositionSnapshot(security=k("SECRET"), market_value=5e8, shares=500),
    ]
    q4 = [
        PositionSnapshot(security=k("AAA"), market_value=1e9, shares=1000),
        PositionSnapshot(security=k("SECRET"), market_value=5e8, shares=500),
    ]

    naive = {c.security: c for c in classify_portfolio(q3_without_amendment, q4)}
    correct = {c.security: c for c in classify_portfolio(q3_with_amendment, q4)}

    assert naive[k("SECRET")].action is ObservedAction.enter  # a purchase that never happened
    assert correct[k("SECRET")].action is ObservedAction.hold  # nothing was traded, which is true


def test_a_restatement_replaces_everything_before_it():
    original = snapshot("orig", [position("111111111", 1e9), position("222222222", 2e9)])
    restated = snapshot(
        "amend", [position("111111111", 1e9)], form_type="13F-HR/A", filed_at=date(2023, 11, 16)
    )

    canonical = compose_quarter(
        lineage(
            ref("orig", filed=date(2023, 11, 14)),
            ref(
                "amend", filed=date(2023, 11, 16), form="13F-HR/A", amend=AmendmentType.restatement
            ),
        ),
        {"orig": original, "amend": restated},
    )

    assert len(canonical.positions) == 1
    assert canonical.contributing_accessions == ["amend"]
    assert "replaced everything prior" in canonical.resolution


def test_a_restatement_then_an_addition_compose_in_filing_order():
    """The real 2023 Q3 shape: restated two days later, then a confidential position disclosed
    eighteen months after that."""
    original = snapshot("orig", [position("111111111", 1e9)])
    restated = snapshot(
        "amend1",
        [position("111111111", 1e9), position("222222222", 2e9)],
        form_type="13F-HR/A",
        filed_at=date(2023, 11, 16),
    )
    late = snapshot(
        "amend2", [position("333333333", 5e8)], form_type="13F-HR/A", filed_at=date(2024, 5, 15)
    )

    canonical = compose_quarter(
        lineage(
            ref("orig", filed=date(2023, 11, 14)),
            ref(
                "amend1", filed=date(2023, 11, 16), form="13F-HR/A", amend=AmendmentType.restatement
            ),
            ref(
                "amend2", filed=date(2024, 5, 15), form="13F-HR/A", amend=AmendmentType.new_holdings
            ),
        ),
        {"orig": original, "amend1": restated, "amend2": late},
    )

    assert len(canonical.positions) == 3
    assert canonical.total_value == 3.5e9
    assert canonical.positions_from_amendments == 1  # the restatement reset the counter


# --- what must not be resolved automatically ---------------------------------------------------------


def test_an_addition_that_repeats_a_security_goes_to_review():
    """A correction and an addition look identical, and picking one either drops a real position
    or double-counts it."""
    original = snapshot("orig", [position("111111111", 1e9)])
    overlapping = snapshot(
        "amend", [position("111111111", 3e9)], form_type="13F-HR/A", filed_at=date(2024, 5, 15)
    )

    canonical = compose_quarter(
        lineage(
            ref("orig", filed=date(2023, 11, 14)),
            ref(
                "amend", filed=date(2024, 5, 15), form="13F-HR/A", amend=AmendmentType.new_holdings
            ),
        ),
        {"orig": original, "amend": overlapping},
    )

    assert canonical.needs_review
    assert not canonical.is_usable
    assert "indistinguishable" in canonical.review_reason


def test_an_unreadable_amendment_type_stops_the_composition():
    canonical = compose_quarter(
        lineage(
            ref("orig", filed=date(2023, 11, 14)),
            ref("amend", filed=date(2024, 5, 15), form="13F-HR/A"),
        ),
        {
            "orig": snapshot("orig", [position("111111111", 1e9)]),
            "amend": snapshot("amend", [position("222222222", 1e9)], form_type="13F-HR/A"),
        },
    )

    assert canonical.needs_review
    assert "unreadable type" in canonical.review_reason


def test_an_unparsed_filing_is_a_review_not_a_silent_omission():
    """Composing a quarter from documents you could not read is how a hole becomes a conclusion."""
    canonical = compose_quarter(lineage(ref("orig", filed=date(2023, 11, 14))), {})

    assert canonical.needs_review
    assert "was not parsed" in canonical.review_reason


def test_a_period_with_no_filings_is_refused():
    canonical = compose_quarter(QuarterLineage(period_end=PERIOD), {})
    assert canonical.needs_review
    assert canonical.positions == []


def test_a_plain_quarter_needs_no_amendment_machinery():
    canonical = compose_quarter(
        lineage(ref("orig", filed=date(2023, 11, 14))),
        {"orig": snapshot("orig", [position("111111111", 1e9), position("222222222", 2e9)])},
    )

    assert canonical.is_usable
    assert canonical.positions_from_amendments == 0
    assert canonical.contributing_accessions == ["orig"]
    # Largest first, so downstream ranking does not have to re-sort.
    assert canonical.positions[0].market_value == 2e9
