"""An amendment corrects what was true. It does not change what was knowable.

Both halves matter and they pull in opposite directions:

    test_a_late_amendment_changes_the_outcome     — it belongs in the ground truth, or a
                                                    purchase gets relocated to the wrong quarter
    test_a_late_amendment_cannot_enter_replay_inputs — it must not reach a replay, or the model
                                                    is handed holdings the market could not see

`test_there_is_no_way_to_build_an_episode_from_a_raw_snapshot` is the structural one. Five
quarters in the real Berkshire range depend on amendments, and a builder that accepted raw
snapshots would let someone reading `snapshots/` off disk resurrect the bug months later.
"""

from __future__ import annotations

import inspect
from datetime import date

import pytest

from app.distillation.finance_nuwa import builder
from app.distillation.finance_nuwa.builder import (
    ReplayView,
    build_episode,
    classify_quarter_pair,
    measure_label_change,
)
from app.distillation.finance_nuwa.drift import ObservedAction
from app.distillation.finance_nuwa.episode import EpisodeInputs
from app.distillation.finance_nuwa.identity import SecurityIdentity, SecurityKey
from app.distillation.finance_nuwa.lineage import CanonicalPosition, CanonicalQuarter
from app.distillation.finance_nuwa.sec_13f import HoldingsSnapshot, ParsedPosition

Q3, Q4 = date(2023, 9, 30), date(2023, 12, 31)
ORIGINAL_FILED = date(2023, 11, 14)
AMENDMENT_FILED = date(2024, 5, 15)
PUBLIC = "111111111"
SECRET = "222222222"


def key(cusip: str) -> SecurityKey:
    return SecurityKey(cusip=cusip, title_of_class="COM")


def canonical_position(cusip: str, value: float, shares: float, **kw) -> CanonicalPosition:
    return CanonicalPosition(
        position=ParsedPosition(
            identity=SecurityIdentity(
                cusip=cusip, issuer_name=f"ISS {cusip}", title_of_class="COM"
            ),
            market_value=value,
            raw_value=value,
            shares=shares,
        ),
        disclosed_at=kw.pop("disclosed_at", ORIGINAL_FILED),
        source_accession=kw.pop("source_accession", "orig"),
        confidential_treatment=kw.pop("confidential_treatment", False),
    )


def q3_public_only() -> CanonicalQuarter:
    """The quarter as filed in November 2023."""
    return CanonicalQuarter(
        period_end=Q3,
        positions=[canonical_position(PUBLIC, 1e9, 1000)],
        contributing_accessions=["orig"],
    )


def q3_canonical() -> CanonicalQuarter:
    """The same quarter once the May 2024 amendment revealed the confidential position."""
    return CanonicalQuarter(
        period_end=Q3,
        positions=[
            canonical_position(PUBLIC, 1e9, 1000),
            canonical_position(
                SECRET,
                5e8,
                500,
                disclosed_at=AMENDMENT_FILED,
                source_accession="amend",
                confidential_treatment=True,
            ),
        ],
        contributing_accessions=["orig", "amend"],
    )


def q2_public() -> CanonicalQuarter:
    """Filed 14 August 2023 — the most recent book an observer has on 1 October."""
    return CanonicalQuarter(
        period_end=date(2023, 6, 30),
        positions=[canonical_position(PUBLIC, 9e8, 900, disclosed_at=date(2023, 8, 14))],
        contributing_accessions=["q2-orig"],
    )


def q4() -> CanonicalQuarter:
    return CanonicalQuarter(
        period_end=Q4,
        positions=[
            canonical_position(PUBLIC, 1e9, 1000, disclosed_at=date(2024, 2, 14)),
            canonical_position(SECRET, 5e8, 500, disclosed_at=date(2024, 2, 14)),
        ],
        contributing_accessions=["q4-orig"],
    )


# --- the amendment must change the outcome ------------------------------------------------------


def test_a_late_amendment_changes_the_outcome():
    """It really was held in Q3, so leaving it out invents a Q4 purchase that never happened."""
    naive = {a.classification.security: a for a in classify_quarter_pair(q3_public_only(), q4())}
    correct = {a.classification.security: a for a in classify_quarter_pair(q3_canonical(), q4())}

    assert naive[key(SECRET)].classification.action is ObservedAction.enter
    assert correct[key(SECRET)].classification.action is ObservedAction.hold


def test_the_label_change_is_measured_rather_than_asserted():
    """Data cleaning that changes no labels is housekeeping. This says whether it changed the
    behavioural ground truth."""
    delta = measure_label_change(q3_public_only(), q3_canonical(), q4())

    assert delta.changed
    assert delta.fabricated_enters_removed == 1
    assert delta.naive_counts.get("enter") == 1
    assert delta.canonical_counts.get("enter", 0) == 0


def test_a_label_that_leans_on_a_hidden_position_is_flagged():
    """Still a valid episode — the investor knew their own book — but worth counting."""
    built = {a.classification.security: a for a in classify_quarter_pair(q3_canonical(), q4())}
    assert built[key(SECRET)].label_depends_on_late_disclosure
    assert not built[key(PUBLIC)].label_depends_on_late_disclosure


# --- and must not reach the replay -----------------------------------------------------------------


def test_a_late_amendment_cannot_enter_replay_inputs():
    """The position was real in Q3 and unknowable until May 2024. A replay of a Q4 decision, cut
    off at 1 October 2023, must not see it."""
    episode = build_episode(
        [q2_public(), q3_canonical()],
        q4(),
        next(
            a
            for a in classify_quarter_pair(q3_canonical(), q4())
            if a.classification.security == key(PUBLIC)
        ),
        advisor_id="buffett",
        entity="Berkshire Hathaway Inc",
        filed_at=date(2024, 2, 14),
    )
    replay = episode.for_replay().model_dump_json()

    assert SECRET not in replay
    disclosed = next(
        o for o in episode.inputs.portfolio_context if o.label == "disclosed portfolio positions"
    )
    assert disclosed.value == 1


def test_the_input_book_is_the_last_filing_that_was_actually_public():
    """The leak this closes is the largest in the dataset and the easiest to miss. A Q4 window
    opens on 1 October; the Q3 filing does not land until mid-November. Taking the previous
    quarter as the input book grants six weeks of hindsight on every single episode."""
    episode = build_episode(
        [q2_public(), q3_canonical()],
        q4(),
        next(
            a
            for a in classify_quarter_pair(q3_canonical(), q4())
            if a.classification.security == key(PUBLIC)
        ),
        advisor_id="buffett",
        entity="Berkshire Hathaway Inc",
        filed_at=date(2024, 2, 14),
    )

    source = next(
        o for o in episode.inputs.portfolio_context if o.label == "disclosed portfolio positions"
    )
    assert source.source == "q2-orig"  # Q2, not the Q3 that had not been filed yet
    assert episode.inputs.starting_value == 9e8


def test_the_oracle_view_is_available_and_explicit():
    """The cost of the strict default, named rather than hidden: the investor knew their whole
    book on 1 October, including the confidential position."""
    episode = build_episode(
        [q2_public(), q3_canonical()],
        q4(),
        next(
            a
            for a in classify_quarter_pair(q3_canonical(), q4())
            if a.classification.security == key(PUBLIC)
        ),
        advisor_id="buffett",
        entity="Berkshire Hathaway Inc",
        filed_at=date(2024, 2, 14),
        view=ReplayView.oracle_own_book,
    )

    disclosed = next(
        o for o in episode.inputs.portfolio_context if o.label == "disclosed portfolio positions"
    )
    assert disclosed.value == 2  # includes the confidential holding
    assert episode.inputs.starting_value == 1e9  # the Q3 book, not Q2


def test_the_cutoff_is_the_window_opening_not_its_close():
    """A quarterly filing places a trade somewhere in three months. If it happened on day one,
    anything later is news the investor did not have."""
    episode = build_episode(
        [q2_public(), q3_canonical()],
        q4(),
        classify_quarter_pair(q3_canonical(), q4())[0],
        advisor_id="buffett",
        entity="Berkshire Hathaway Inc",
        filed_at=date(2024, 2, 14),
    )

    assert episode.inputs.as_of == date(2023, 10, 1)
    assert episode.decision_window_start == date(2023, 10, 1)
    assert episode.decision_window_end == Q4
    assert episode.decision_window_days == 91


def test_knowable_on_is_what_separates_the_two_views():
    quarter = q3_canonical()

    assert len(quarter.positions) == 2
    assert len(quarter.positions_knowable_on(date(2023, 12, 1))) == 1
    assert len(quarter.positions_knowable_on(date(2024, 6, 1))) == 2
    assert [p.identity.cusip for p in quarter.late_disclosed] == [SECRET]
    assert quarter.late_disclosed[0].disclosure_delay_days(Q3) == 228


def test_an_episode_carrying_a_hidden_position_would_be_refused_anyway():
    """Belt and braces: even hand-built inputs cannot smuggle it in, because the observation
    would be dated after the cutoff."""
    with pytest.raises(ValueError, match="became knowable"):
        EpisodeInputs(
            as_of=date(2023, 10, 1),
            security=key(PUBLIC),
            portfolio_context=[
                {"label": "secret holding", "observed_at": AMENDMENT_FILED},  # type: ignore[list-item]
            ],
        )


# --- the wrong path does not exist ---------------------------------------------------------------------


def test_there_is_no_way_to_build_an_episode_from_a_raw_snapshot():
    """Five real quarters depend on amendments. A raw-snapshot overload would let someone
    reading `snapshots/` off disk resurrect the fabricated-purchase bug months from now."""
    pair = inspect.get_annotations(classify_quarter_pair, eval_str=True)
    assert pair["previous"] is CanonicalQuarter
    assert pair["current"] is CanonicalQuarter

    episode = inspect.get_annotations(build_episode, eval_str=True)
    assert episode["current"] is CanonicalQuarter
    assert episode["history"] == list[CanonicalQuarter]

    exported = {name for name in dir(builder) if not name.startswith("_")}
    assert "HoldingsSnapshot" not in exported


def test_passing_a_raw_snapshot_fails_rather_than_silently_working():
    raw = HoldingsSnapshot(
        entity="Berkshire Hathaway Inc",
        cik="1067983",
        accession="orig",
        period_end=Q3,
        filed_at=ORIGINAL_FILED,
        positions=[],
        normalization={  # type: ignore[arg-type]
            "unit": "dollars_usd",
            "multiplier": 1.0,
            "rule_source": "test",
            "validation": "passed",
        },
    )
    with pytest.raises(AttributeError):
        classify_quarter_pair(raw, q4())  # type: ignore[arg-type]


# --- two benchmarks, one ground truth ------------------------------------------------------------


def _both_views():
    action = next(
        a
        for a in classify_quarter_pair(q3_canonical(), q4())
        if a.classification.security == key(PUBLIC)
    )
    common = dict(
        advisor_id="buffett",
        entity="Berkshire Hathaway Inc",
        filed_at=date(2024, 2, 14),
    )
    public = build_episode(
        [q2_public(), q3_canonical()], q4(), action, view=ReplayView.public_observer, **common
    )
    oracle = build_episode(
        [q2_public(), q3_canonical()], q4(), action, view=ReplayView.oracle_own_book, **common
    )
    return public, oracle


def test_the_two_views_share_one_outcome_and_differ_only_in_inputs():
    """One set of outcomes, two information sets. If a refactor ever makes the labels differ,
    the gap between the scores stops meaning anything."""
    public, oracle = _both_views()

    assert public.observed_action is oracle.observed_action
    assert public.action_basis is oracle.action_basis
    assert public.decision_window_start == oracle.decision_window_start
    assert public.decision_window_end == oracle.decision_window_end
    assert public.episode_id == oracle.episode_id


def test_the_two_views_must_not_collapse_into_each_other():
    """Guards the refactor that quietly makes both paths return the same thing. Here they differ
    for two independent reasons at once: a confidential holding, and the ordinary filing lag."""
    public, oracle = _both_views()

    public_count = next(
        o for o in public.inputs.portfolio_context if o.label == "disclosed portfolio positions"
    ).value
    oracle_count = next(
        o for o in oracle.inputs.portfolio_context if o.label == "disclosed portfolio positions"
    ).value

    assert public_count != oracle_count, (
        "public and oracle replays returned identical books — the information boundary has "
        "collapsed and the error decomposition is meaningless"
    )
    assert public.inputs.starting_value != oracle.inputs.starting_value
    # The confidential holding is counted in one book and not the other. Inputs carry aggregates
    # rather than an enumeration, so the difference shows up in the count, not in a CUSIP string.
    assert public_count == 1 and oracle_count == 2


def test_only_the_public_view_claims_to_be_deployable():
    """The name says oracle so nobody mistakes it for a product capability; the flag says so to
    code that might otherwise pick it up as a default."""
    assert ReplayView.public_observer.is_deployable
    assert not ReplayView.oracle_own_book.is_deployable


def test_the_reporting_lag_alone_separates_the_views():
    """Even with no confidential holding anywhere, the ordinary 45-day filing lag means the two
    views see different books — so the gap is not an artefact of amendments."""
    action = next(
        a
        for a in classify_quarter_pair(q3_public_only(), q4())
        if a.classification.security == key(PUBLIC)
    )
    common = dict(advisor_id="buffett", entity="Berkshire Hathaway Inc", filed_at=date(2024, 2, 14))

    public = build_episode(
        [q2_public(), q3_public_only()], q4(), action, view=ReplayView.public_observer, **common
    )
    oracle = build_episode(
        [q2_public(), q3_public_only()], q4(), action, view=ReplayView.oracle_own_book, **common
    )

    assert public.inputs.starting_value == 9e8  # Q2, filed in August
    assert oracle.inputs.starting_value == 1e9  # Q3, which nobody outside could see until November
