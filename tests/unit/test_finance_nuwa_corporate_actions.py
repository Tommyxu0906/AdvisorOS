"""Telling an identity change apart from a decision.

Every fixture is a shape that occurs in the real 2014-2024 Berkshire range.
`test_ordinary_turnover_is_not_reported_as_a_merger` is the one that keeps the review queue
worth reading: value similarity alone flagged 41 "mergers" across 43 transitions, almost all of
them a manager simply selling one position and buying another the same size.
"""

from __future__ import annotations

from datetime import date

from app.distillation.finance_nuwa.corporate_actions import (
    CorporateActionKind,
    LineageTable,
    SecurityLineage,
    blocked_securities,
    detect_candidates,
    split_factors_for,
)
from app.distillation.finance_nuwa.identity import SecurityIdentity, SecurityKey
from app.distillation.finance_nuwa.lineage import CanonicalPosition, CanonicalQuarter
from app.distillation.finance_nuwa.sec_13f import ParsedPosition

Q1, Q2 = date(2015, 3, 31), date(2015, 6, 30)


def key(cusip: str, title: str = "COM") -> SecurityKey:
    return SecurityKey(cusip=cusip, title_of_class=title)


def pos(
    cusip: str, issuer: str, value: float, shares: float, title: str = "COM"
) -> CanonicalPosition:
    return CanonicalPosition(
        position=ParsedPosition(
            identity=SecurityIdentity(cusip=cusip, issuer_name=issuer, title_of_class=title),
            market_value=value,
            raw_value=value,
            shares=shares,
        ),
        disclosed_at=Q1,
        source_accession="acc",
    )


def quarter(period_end: date, positions: list[CanonicalPosition]) -> CanonicalQuarter:
    return CanonicalQuarter(
        period_end=period_end, positions=positions, contributing_accessions=["acc"]
    )


# --- splits ---------------------------------------------------------------------------------


def test_visas_four_for_one_split_is_not_read_as_a_purchase():
    """The real one in the dataset: 3.94x shares against 0.98x value, March 2015."""
    before = quarter(Q1, [pos("92826C839", "VISA INC", 1e9, 10_000_000)])
    after = quarter(Q2, [pos("92826C839", "VISA INC", 0.98e9, 39_400_000)])

    candidates = detect_candidates(before, after)

    assert len(candidates) == 1
    assert candidates[0].suspected is CorporateActionKind.split
    assert candidates[0].share_ratio == 3.94
    assert "signature of a split" in candidates[0].reason
    assert candidates[0].blocks_episode


def test_a_genuine_doubling_is_not_flagged_as_a_split():
    """Twice the shares at twice the money is a purchase. Only the value separates the two."""
    before = quarter(Q1, [pos("111111111", "REAL CO", 1e9, 1_000_000)])
    after = quarter(Q2, [pos("111111111", "REAL CO", 2e9, 2_000_000)])

    assert detect_candidates(before, after) == []


def test_an_established_split_is_not_re_flagged():
    """A queue that keeps reporting settled facts trains people to ignore it."""
    table = LineageTable(
        entries=[
            SecurityLineage(
                from_security=key("92826C839"),
                to_security=key("92826C839"),
                kind=CorporateActionKind.split,
                effective_date=date(2015, 5, 12),
                share_ratio=4.0,
                evidence="hand-verified",
            )
        ]
    )
    before = quarter(Q1, [pos("92826C839", "VISA INC", 1e9, 10_000_000)])
    after = quarter(Q2, [pos("92826C839", "VISA INC", 0.98e9, 39_400_000)])

    assert detect_candidates(before, after, table=table) == []
    assert split_factors_for(before, after, table) == {key("92826C839"): 4.0}


# --- identity changes ---------------------------------------------------------------------------


def test_a_matching_issuer_name_across_two_cusips_is_flagged():
    """Liberty Media's tracking-stock recapitalisations do exactly this, repeatedly."""
    before = quarter(Q1, [pos("531229102", "LIBERTY MEDIA CORP DELAWARE", 1e9, 1_000_000)])
    after = quarter(Q2, [pos("531229607", "LIBERTY MEDIA CORP DELAWARE", 1.02e9, 1_000_000)])

    candidates = detect_candidates(before, after)

    assert len(candidates) == 1
    assert candidates[0].suspected is CorporateActionKind.cusip_change
    assert "issuer names match" in candidates[0].reason
    # High measured precision, and reading one as exit-plus-entry fabricates a round trip.
    assert candidates[0].blocks_episode


def test_a_class_reorganisation_is_caught_by_the_same_rule():
    """Charter's D shares becoming N shares — one security, not two decisions."""
    before = quarter(Q1, [pos("16117M305", "CHARTER COMMUNICATIONS INC D", 1e9, 1_000_000)])
    after = quarter(Q2, [pos("16119P108", "CHARTER COMMUNICATIONS INC N", 1.02e9, 1_000_000)])

    assert detect_candidates(before, after)[0].suspected is CorporateActionKind.cusip_change


# --- the noisy case -----------------------------------------------------------------------------


def test_ordinary_turnover_is_not_reported_as_a_merger():
    """Two exits that could each explain one arrival establish nothing.

    The real shape: in 2016 Q1 both AT&T and Precision Castparts left while Apple arrived at a
    similar size. Neither is a merger — Berkshire sold two things and bought another.
    """
    before = quarter(
        Q1,
        [
            pos("00206R102", "AT&T INC", 1e9, 1_000_000),
            pos("740189105", "PRECISION CASTPARTS CORP", 1.1e9, 1_000_000),
        ],
    )
    after = quarter(Q2, [pos("037833100", "APPLE INC", 1.05e9, 1_000_000)])

    assert detect_candidates(before, after) == []


def test_an_unambiguous_one_to_one_pairing_is_still_raised():
    """DirecTV into AT&T, 2015 — the one real merger in the range. Ambiguity is the filter,
    not size similarity on its own."""
    before = quarter(Q1, [pos("25490A309", "DIRECTV", 1e9, 1_000_000)])
    after = quarter(Q2, [pos("00206R102", "AT&T INC", 0.9e9, 2_000_000)])

    candidates = detect_candidates(before, after)

    assert len(candidates) == 1
    assert candidates[0].suspected is CorporateActionKind.merger
    assert "no other position on either side could explain it" in candidates[0].reason


def test_a_merger_candidate_does_not_block_because_the_detector_is_unreliable():
    """Measured at roughly one in ten. Blocking discards both sides, so it would cost about
    eighteen genuine decisions to catch two false ones — worse than the disease."""
    before = quarter(Q1, [pos("25490A309", "DIRECTV", 1e9, 1_000_000)])
    after = quarter(Q2, [pos("00206R102", "AT&T INC", 0.9e9, 2_000_000)])

    candidate = detect_candidates(before, after)[0]
    assert not candidate.blocks_episode
    assert blocked_securities([candidate]) == set()


def test_blocking_covers_both_sides_of_a_reliable_candidate():
    before = quarter(Q1, [pos("531229102", "LIBERTY MEDIA CORP DELAWARE", 1e9, 1_000_000)])
    after = quarter(Q2, [pos("531229607", "LIBERTY MEDIA CORP DELAWARE", 1.02e9, 1_000_000)])

    assert blocked_securities(detect_candidates(before, after)) == {
        key("531229102"),
        key("531229607"),
    }


# --- the table is a fact about securities, not about a manager ---------------------------------------


def test_the_lineage_table_is_entity_agnostic():
    """A split is a property of the security. Every subject the library adds later reuses this
    rather than rediscovering it."""
    table = LineageTable(
        entries=[
            SecurityLineage(
                from_security=key("92826C839"),
                kind=CorporateActionKind.split,
                effective_date=date(2015, 5, 12),
                share_ratio=4.0,
            )
        ]
    )
    fields = set(SecurityLineage.model_fields)

    assert not fields & {"manager", "entity", "cik", "advisor_id"}
    assert table.split_ratio(key("92826C839"), during=(Q1, Q2)) == 4.0
    # Windows are bounded by the two period ends, so an action outside them belongs to a
    # different transition and must not be applied to this one.
    assert (
        table.split_ratio(key("92826C839"), during=(date(2020, 1, 1), date(2020, 12, 31))) is None
    )


def test_an_established_successor_suppresses_the_candidate():
    table = LineageTable(
        entries=[
            SecurityLineage(
                from_security=key("25490A309"),
                to_security=key("00206R102"),
                kind=CorporateActionKind.merger,
                effective_date=date(2015, 5, 12),
                evidence="hand-verified",
            )
        ]
    )
    before = quarter(Q1, [pos("25490A309", "DIRECTV", 1e9, 1_000_000)])
    after = quarter(Q2, [pos("00206R102", "AT&T INC", 0.9e9, 2_000_000)])

    assert detect_candidates(before, after, table=table) == []


def test_every_passive_kind_means_the_holder_decided_nothing():
    for kind in (
        CorporateActionKind.split,
        CorporateActionKind.merger,
        CorporateActionKind.spin_off,
        CorporateActionKind.cusip_change,
        CorporateActionKind.class_change,
    ):
        assert kind.is_passive
    assert not CorporateActionKind.unknown.is_passive
