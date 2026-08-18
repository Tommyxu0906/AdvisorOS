"""Identity is (CUSIP, share class), and one of those alone is not enough.

Two failures sit on opposite sides of this file, and fixing either one carelessly causes the
other.

`test_two_share_classes_under_one_cusip_are_two_independent_episodes` is the failure the typed
key exists to prevent: a book that reports Class A and Class B under one CUSIP, keyed on the
CUSIP, keeps whichever position was seen last and silently drops the other. No error, no count,
no line in any audit.

`test_a_class_label_that_changes_across_quarters_is_a_candidate_not_a_round_trip` is the failure
the fix *introduces* if it stops there. `titleOfClass` is filer free text, and Berkshire's real
filings restyle it: Lennar's 152,572 shares are filed as CL A in 2023 Q2 and as CL B in 2023 Q3
with the share count unchanged. Split naively on the label and that becomes a full exit and a
full entry — a fabricated round trip in a position nobody touched.

So a relabeling is neither merged nor split on the spot. It is detected, and then resolved the
way every other identity change here is resolved: with a curated entry, or not at all.
"""

from __future__ import annotations

from datetime import date

import pytest

from app.distillation.finance_nuwa.builder import ReplayView, build_episode, classify_quarter_pair
from app.distillation.finance_nuwa.corporate_actions import (
    CorporateActionKind,
    LineageTable,
    SecurityLineage,
    apply_lineage,
    detect_candidates,
    unresolved_blocking,
)
from app.distillation.finance_nuwa.drift import ObservedAction
from app.distillation.finance_nuwa.features import build_features, implied_price
from app.distillation.finance_nuwa.identity import (
    SECURITY_KEY_SCHEMA_VERSION,
    SecurityIdentity,
    SecurityKey,
)
from app.distillation.finance_nuwa.lineage import (
    CanonicalPosition,
    CanonicalQuarter,
    PublicQuarterView,
)
from app.distillation.finance_nuwa.sec_13f import ParsedPosition

Q2, Q3 = date(2023, 6, 30), date(2023, 9, 30)
LENNAR = "526057302"


def pos(cusip: str, title: str, value: float, shares: float, issuer: str = "LENNAR CORP"):
    return CanonicalPosition(
        position=ParsedPosition(
            identity=SecurityIdentity(cusip=cusip, issuer_name=issuer, title_of_class=title),
            market_value=value,
            raw_value=value,
            shares=shares,
        ),
        disclosed_at=Q2,
        source_accession="acc",
    )


def quarter(period_end: date, positions: list[CanonicalPosition]) -> CanonicalQuarter:
    return CanonicalQuarter(
        period_end=period_end, positions=positions, contributing_accessions=["acc"]
    )


# --- the key itself ------------------------------------------------------------------------


def test_the_key_round_trips_through_its_serialized_form():
    """Every artifact row stores the token. If parsing one back produced a different key, the
    frozen dataset would not describe the rows it was built from."""
    original = SecurityKey(cusip="526057302", title_of_class="CL A")

    assert original.token == "526057302:CL A"
    assert SecurityKey.parse(original.token) == original
    assert original.slug == "526057302-cl-a"


def test_the_key_normalises_case_and_spacing_but_invents_nothing_else():
    """Whitespace and case are formatting. Anything beyond that would be a claim about what the
    filer meant, and `titleOfClass` is free text."""
    assert SecurityKey(cusip=" 526057302 ", title_of_class=" cl   a ").token == "526057302:CL A"
    # Not normalised into each other, because nothing in the filing says they are the same.
    assert SecurityKey(cusip=LENNAR, title_of_class="COM SER C FRMLA") != SecurityKey(
        cusip=LENNAR, title_of_class="COM C MEDIA GRP"
    )


def test_a_malformed_cusip_is_refused_by_the_key_as_well_as_the_identity():
    with pytest.raises(ValueError, match="not a 9-character CUSIP"):
        SecurityKey(cusip="AAPL", title_of_class="COM")


def test_the_schema_version_is_recorded_because_episode_ids_are_built_from_it():
    assert SECURITY_KEY_SCHEMA_VERSION == "security-key-v1"


# --- the failure this exists to prevent -------------------------------------------------------


def test_two_share_classes_under_one_cusip_are_two_independent_episodes():
    """The whole point. Under bare-CUSIP keying one of these overwrote the other in every
    dictionary in the pipeline, and nothing anywhere reported that a position had vanished."""
    before = quarter(
        Q2,
        [
            pos(LENNAR, "CL A", 17_000_000, 150_000),
            pos(LENNAR, "CL B", 5_000_000, 50_000),
        ],
    )
    after = quarter(
        Q3,
        [
            pos(LENNAR, "CL A", 17_000_000, 150_000),  # untouched
            pos(LENNAR, "CL B", 2_500_000, 25_000),  # halved
        ],
    )

    built = {a.classification.security: a for a in classify_quarter_pair(before, after)}
    class_a = SecurityKey(cusip=LENNAR, title_of_class="CL A")
    class_b = SecurityKey(cusip=LENNAR, title_of_class="CL B")

    # Two securities survive, and they carry different labels — neither is the other's shadow.
    assert set(built) == {class_a, class_b}
    assert built[class_a].classification.action is ObservedAction.hold
    assert built[class_b].classification.action is ObservedAction.reduce

    # ...through to two distinct episodes, which is what a dataset row is keyed on.
    episodes = [
        build_episode(
            [before],
            after,
            built[security],
            advisor_id="berkshire_public_equity",
            entity="Berkshire Hathaway Inc",
            filed_at=date(2023, 11, 14),
            view=ReplayView.public_observer,
        )
        for security in (class_a, class_b)
    ]
    assert len({e.episode_id for e in episodes}) == 2
    assert episodes[0].episode_id.endswith("-cl-a-2023-09-30")
    assert episodes[1].episode_id.endswith("-cl-b-2023-09-30")


def test_the_two_classes_keep_separate_prices_and_weights():
    """Point-in-time features are per security. Collapsing the classes would give both the
    weight of whichever one survived the overwrite."""
    book = PublicQuarterView.of(
        quarter(
            Q2,
            [
                pos(LENNAR, "CL A", 15_000_000, 150_000),  # $100
                pos(LENNAR, "CL B", 5_000_000, 100_000),  # $50
            ],
        ),
        as_of=date(2023, 8, 15),
    )
    class_a = SecurityKey(cusip=LENNAR, title_of_class="CL A")
    class_b = SecurityKey(cusip=LENNAR, title_of_class="CL B")

    assert implied_price(book, class_a) == 100.0
    assert implied_price(book, class_b) == 50.0
    assert build_features([book], class_a, as_of=date(2023, 8, 15)).weight == pytest.approx(0.75)
    assert build_features([book], class_b, as_of=date(2023, 8, 15)).weight == pytest.approx(0.25)


# --- and the failure the fix would otherwise introduce -------------------------------------------


def test_a_class_label_that_changes_across_quarters_is_a_candidate_not_a_round_trip():
    """The real Lennar shape: 152,572 shares filed as CL A, then as CL B, unchanged.

    Splitting on the label alone turns that into an exit and an entry. Neither happened.
    """
    before = quarter(Q2, [pos(LENNAR, "CL A", 17_237_585, 152_572)])
    after = quarter(Q3, [pos(LENNAR, "CL B", 15_597_436, 152_572)])

    candidates = detect_candidates(before, after)

    assert len(candidates) == 1
    assert candidates[0].suspected is CorporateActionKind.class_relabel
    assert candidates[0].share_ratio == 1.0
    assert candidates[0].blocks_episode  # unresolved, it would fabricate a round trip


def test_a_curated_relabeling_restores_the_position_as_one_continuous_holding():
    table = LineageTable(
        entries=[
            SecurityLineage(
                from_security=SecurityKey(cusip=LENNAR, title_of_class="CL A"),
                to_security=SecurityKey(cusip=LENNAR, title_of_class="CL B"),
                kind=CorporateActionKind.class_relabel,
                effective_date=Q3,
                evidence="identical share count across the boundary",
            )
        ]
    )
    before = quarter(Q2, [pos(LENNAR, "CL A", 17_237_585, 152_572)])
    after = quarter(Q3, [pos(LENNAR, "CL B", 15_597_436, 152_572)])

    assert detect_candidates(before, after, table=table) == []
    assert unresolved_blocking(detect_candidates(before, after, table=table), table) == []

    built = classify_quarter_pair(before, after, successors=apply_lineage(before, after, table))
    assert [a.classification.action for a in built] == [ObservedAction.hold]


def test_a_cusip_carrying_both_classes_at_once_is_never_treated_as_a_relabeling():
    """The guard that keeps the relabeling rule from undoing the typed key. Two classes present
    in the same book are two securities, whatever their labels do afterwards."""
    before = quarter(
        Q2,
        [pos(LENNAR, "CL A", 17_000_000, 150_000), pos(LENNAR, "CL B", 5_000_000, 50_000)],
    )
    after = quarter(
        Q3,
        [pos(LENNAR, "CL A", 17_000_000, 150_000), pos(LENNAR, "CL C", 5_000_000, 50_000)],
    )

    relabels = [
        c
        for c in detect_candidates(before, after)
        if c.suspected is CorporateActionKind.class_relabel
    ]
    assert relabels == []


def test_a_curated_entry_without_a_class_applies_to_whichever_class_was_filed():
    """Most curated entries record an issuer-level re-identification and name no class, because
    that is what their evidence establishes. Binding happens against the receiving book rather
    than by inventing a class on the entry."""
    entry = SecurityLineage(
        from_security=SecurityKey(cusip="584404107"),
        to_security=SecurityKey(cusip="58441K100"),
        kind=CorporateActionKind.cusip_change,
        effective_date=Q3,
        evidence="issuer name continuity",
    )
    table = LineageTable(entries=[entry])
    before = quarter(Q2, [pos("584404107", "COM", 1e9, 1e6, issuer="MEDIA GEN INC")])
    after = quarter(Q3, [pos("58441K100", "CL A", 1.02e9, 1e6, issuer="MEDIA GEN INC NEW")])

    assert entry.matches_any_class
    assert apply_lineage(before, after, table) == {
        SecurityKey(cusip="584404107", title_of_class="COM"): SecurityKey(
            cusip="58441K100", title_of_class="CL A"
        )
    }


def test_an_ambiguous_successor_is_left_unmapped_rather_than_guessed():
    """If the successor arrived under two classes, a classless entry does not say which. Guessing
    would be the same mistake as one-to-many lineage, one layer down."""
    table = LineageTable(
        entries=[
            SecurityLineage(
                from_security=SecurityKey(cusip="584404107"),
                to_security=SecurityKey(cusip="58441K100"),
                kind=CorporateActionKind.cusip_change,
                effective_date=Q3,
                evidence="issuer name continuity",
            )
        ]
    )
    before = quarter(Q2, [pos("584404107", "COM", 1e9, 1e6, issuer="MEDIA GEN INC")])
    after = quarter(
        Q3,
        [
            pos("58441K100", "CL A", 6e8, 6e5, issuer="MEDIA GEN INC NEW"),
            pos("58441K100", "CL B", 4e8, 4e5, issuer="MEDIA GEN INC NEW"),
        ],
    )

    assert apply_lineage(before, after, table) == {}
