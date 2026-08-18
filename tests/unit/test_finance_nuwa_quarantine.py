"""Withholding only the observations we cannot interpret.

The scoping is the whole design. `test_the_same_security_is_usable_again_once_its_identity_is_stable`
is why this is not a CUSIP blocklist: a position recapitalised in 2016 has a perfectly readable
identity in 2019, and its decisions then are real evidence that a blanket exclusion would burn.
"""

from __future__ import annotations

from datetime import date

from app.distillation.finance_nuwa.audit import DatasetAudit
from app.distillation.finance_nuwa.corporate_actions import ActionCandidate, CorporateActionKind
from app.distillation.finance_nuwa.identity import SecurityKey
from app.distillation.finance_nuwa.quarantine import (
    EpisodeExclusion,
    ExclusionRegistry,
    ExclusionScope,
    ExclusionStatus,
    quarantine_unresolved,
)


def key(cusip: str, title: str = "COM") -> SecurityKey:
    return SecurityKey(cusip=cusip, title_of_class=title)


LIBERTY_A, LIBERTY_B, LIBERTY_C = key("531229102"), key("531229607"), key("531229409")
UNRELATED = key("037833100")
Q2_2016, Q3_2016 = date(2016, 6, 30), date(2016, 9, 30)


def candidate(
    from_security: SecurityKey, to_security: SecurityKey, period_end: date = Q2_2016
) -> ActionCandidate:
    return ActionCandidate(
        period_end=period_end,
        suspected=CorporateActionKind.cusip_change,
        from_security=from_security,
        to_security=to_security,
        reason="issuer names match",
    )


def liberty_registry() -> ExclusionRegistry:
    """The real shape: one security mapping to two successors in one transition."""
    return quarantine_unresolved(
        [candidate(LIBERTY_A, LIBERTY_B), candidate(LIBERTY_A, LIBERTY_C)],
        dataset_version="berkshire-v1.0",
    )


# --- what quarantine removes ---------------------------------------------------------------


def test_an_unresolved_one_to_many_transition_cannot_produce_labels():
    registry = liberty_registry()

    for security in (LIBERTY_A, LIBERTY_B, LIBERTY_C):
        assert registry.is_excluded(period_end=Q2_2016, security=security)


def test_the_one_to_many_shape_is_recognised_and_recorded():
    """One source with several successors is not a rename, and the status says so rather than
    leaving a reader to infer it from a count."""
    exclusion = liberty_registry().exclusions[0]

    assert exclusion.status is ExclusionStatus.unresolved_one_to_many
    assert "several successors" in exclusion.reason
    assert exclusion.scope is ExclusionScope.labels_only
    assert exclusion.dataset_version == "berkshire-v1.0"
    assert exclusion.evidence  # carries the arithmetic that raised it


def test_a_plain_unresolved_candidate_is_not_called_one_to_many():
    registry = quarantine_unresolved(
        [candidate(key("111111111"), key("222222222"))], dataset_version="v"
    )
    assert registry.exclusions[0].status is ExclusionStatus.unresolved_unknown


# --- what quarantine leaves alone ------------------------------------------------------------


def test_unrelated_securities_in_the_same_quarter_stay_usable():
    """The event touched Liberty, not Apple. Excluding the quarter would discard real decisions
    that had nothing to do with it."""
    assert not liberty_registry().is_excluded(period_end=Q2_2016, security=UNRELATED)


def test_the_same_security_is_usable_again_once_its_identity_is_stable():
    """Scoped to the transition, not the security. This is the difference between quarantine and
    a blocklist, and it is worth eleven years of behaviour."""
    registry = liberty_registry()

    assert registry.is_excluded(period_end=Q2_2016, security=LIBERTY_B)
    assert not registry.is_excluded(period_end=Q3_2016, security=LIBERTY_B)


def test_both_conditions_are_required_never_one():
    """Matching on the security alone removes it from every quarter; matching on the quarter
    alone removes positions that had nothing to do with the event."""
    exclusion = liberty_registry().exclusions[0]

    assert exclusion.covers(period_end=Q2_2016, security=LIBERTY_A)
    assert not exclusion.covers(period_end=Q3_2016, security=LIBERTY_A)
    assert not exclusion.covers(period_end=Q2_2016, security=UNRELATED)


def test_a_whole_quarter_exclusion_exists_but_is_not_the_default():
    """Reserved for corruption, not ambiguity — ambiguity is scoped."""
    whole = EpisodeExclusion(
        transition_period_end=Q2_2016,
        securities=(LIBERTY_A,),
        detected_kind=CorporateActionKind.unknown,
        reason="quarter unreadable",
        evidence="test",
        scope=ExclusionScope.whole_quarter,
    )
    assert whole.covers(period_end=Q2_2016, security=UNRELATED)


# --- the audit must not confuse withheld with resolved ------------------------------------------


def test_a_quarantined_transition_is_withheld_not_resolved():
    """The gate asks whether an unresolved action reaches the modelling data, never whether one
    exists. Counting quarantine as resolution would let the ambiguity back in by relabelling."""
    audit = DatasetAudit(
        dataset_version="berkshire-v1.0",
        entity="Berkshire Hathaway Inc",
        action_counts={"hold": 530, "reduce": 244},
        unresolved_blocking_actions=5,
        quarantined_transitions=2,
        quarantined_securities=6,
        episodes_removed_by_quarantine=8,
        unresolved_reaching_modelling=0,
        artifact_verified=True,
        split_manifest_matches=True,
    )

    assert audit.passes  # nothing unresolved reached the data
    assert audit.unresolved_blocking_actions == 5  # but five still exist, and are reported
    rendered = audit.render()
    assert "Quarantined transitions   2" in rendered
    assert "8 episodes withheld" in rendered
    assert "Reaching modelling data   0" in rendered


def test_an_unresolved_action_that_escapes_quarantine_still_blocks():
    audit = DatasetAudit(
        dataset_version="berkshire-v1.0",
        entity="Berkshire Hathaway Inc",
        action_counts={"hold": 10},
        unresolved_blocking_actions=3,
        quarantined_transitions=1,
        unresolved_reaching_modelling=1,
    )

    assert not audit.passes
    failing = [g for g in audit.gates if not g.passed]
    assert failing[0].name == "unresolved blocking reaching modelling data"
    assert "withheld, not resolved" in failing[0].detail


def test_an_empty_registry_excludes_nothing():
    registry = ExclusionRegistry()
    assert not registry.is_excluded(period_end=Q2_2016, security=LIBERTY_A)
    assert registry.transitions == 0
    assert registry.securities == 0
