"""The wall between what was knowable and what happened next.

`test_an_episode_carrying_hindsight_cannot_be_constructed` is the one that matters. A persona
replayed against an episode that contains its own answer scores brilliantly and has learned
nothing — and the resulting model looks *validated*, which is worse than looking unvalidated.
The rule is enforced by a validator rather than by care, because "remember not to include future
data" is exactly the kind of rule that survives review and dies in a refactor.
"""

from __future__ import annotations

from datetime import date

import pytest
from pydantic import ValidationError

from app.distillation.finance_nuwa.drift import ActionBasis, ObservedAction
from app.distillation.finance_nuwa.episode import (
    AttributionBasis,
    DecisionEpisode,
    EpisodeInputs,
    EpisodeOutcome,
    Observation,
)
from app.distillation.finance_nuwa.identity import SecurityKey

DECISION_DATE = date(2016, 3, 31)


def obs(label: str, when: date, **kw) -> Observation:
    return Observation(label=label, observed_at=when, **kw)


def inputs(**overrides) -> EpisodeInputs:
    base = dict(
        as_of=DECISION_DATE,
        security=SecurityKey(cusip="037833100", title_of_class="COM"),
        starting_weight=0.0,
        market_context=[obs("10y treasury 1.78%", date(2016, 3, 31))],
        fundamentals=[obs("FY2015 revenue $233.7B", date(2015, 10, 27))],
    )
    base.update(overrides)
    return EpisodeInputs(**base)


def episode(**overrides) -> DecisionEpisode:
    base = dict(
        advisor_id="buffett",
        episode_id="buffett-aapl-2016q1",
        inputs=inputs(),
        observed_action=ObservedAction.enter,
        action_basis=ActionBasis.share_count,
        attribution=AttributionBasis.entity_filing,
        attribution_confidence=0.6,
    )
    base.update(overrides)
    return DecisionEpisode(**base)


# --- the lookahead barrier --------------------------------------------------------------------


def test_an_episode_carrying_hindsight_cannot_be_constructed():
    with pytest.raises(ValidationError, match="after the 2016-03-31 decision date"):
        inputs(market_context=[obs("AAPL up 300%", date(2020, 1, 2))])


@pytest.mark.parametrize(
    "field", ["portfolio_context", "market_context", "fundamentals", "valuation"]
)
def test_every_input_channel_is_guarded_not_just_the_obvious_one(field: str):
    """A guard on three of four fields is a guard on none — the fourth is where it will leak."""
    with pytest.raises(ValidationError, match="became knowable"):
        inputs(**{field: [obs("tomorrow's news", date(2016, 4, 1))]})


def test_information_from_the_decision_date_itself_is_allowed():
    """Same-day is knowable. Excluding it would silently narrow every episode by a day."""
    assert inputs(valuation=[obs("P/E 10.6x", DECISION_DATE)]).valuation


def test_a_filing_is_dated_when_it_was_published_not_when_the_period_ended():
    """The distinction that quietly leaks weeks of information if it is got wrong: a quarter
    that ended in September is not knowable until the filing lands in October."""
    ok = inputs(fundamentals=[obs("Q3 results", date(2015, 10, 27))])
    assert ok.fundamentals[0].observed_at > date(2015, 9, 30)

    with pytest.raises(ValidationError):
        inputs(
            as_of=date(2015, 10, 1),
            fundamentals=[obs("Q3 results", date(2015, 10, 27))],
        )


def test_the_replay_view_hands_over_inputs_and_nothing_else():
    """Leaking the outcome should require going and getting it, not forgetting to strip it."""
    with_outcome = episode(
        outcome=EpisodeOutcome(horizon_months=48, position_return=3.1, benchmark_return=0.5)
    )
    replay = with_outcome.for_replay()

    assert isinstance(replay, EpisodeInputs)
    assert not hasattr(replay, "outcome")
    assert "3.1" not in replay.model_dump_json()
    assert "position_return" not in replay.model_dump_json()


def test_the_outcome_is_a_separate_type_rather_than_optional_fields():
    """Fields on one object get serialized, summarized and pasted into prompts together."""
    assert "outcome" not in EpisodeInputs.model_fields
    assert set(EpisodeOutcome.model_fields) & set(EpisodeInputs.model_fields) == set()


def test_the_outcome_is_still_available_for_scoring():
    """Kept out of prediction, not thrown away — analysis needs it."""
    scored = episode(
        outcome=EpisodeOutcome(horizon_months=12, position_return=0.42, benchmark_return=0.12)
    )
    assert scored.outcome is not None
    assert scored.outcome.excess_return == pytest.approx(0.30)


def test_an_incomplete_outcome_reports_no_excess_rather_than_zero():
    assert EpisodeOutcome(horizon_months=12, position_return=0.4).excess_return is None


# --- whose decision was it -----------------------------------------------------------------------


def test_claiming_a_personal_decision_requires_more_than_a_guess():
    """Training a Buffett persona on a colleague's trades produces a confident model of the
    wrong person, so the strong claim carries a floor."""
    with pytest.raises(ValidationError, match="does not support"):
        episode(attribution=AttributionBasis.self_described, attribution_confidence=0.2)

    assert episode(
        attribution=AttributionBasis.self_described, attribution_confidence=0.9
    ).attribution.is_personal


def test_an_entity_filing_makes_no_claim_about_who_chose_it():
    """A large book may be run by several managers; the filing does not say which one acted."""
    filed = episode(attribution=AttributionBasis.entity_filing, attribution_confidence=0.2)
    assert not filed.attribution.is_personal


# --- how much an episode should count ---------------------------------------------------------------


def test_evidence_quality_and_attribution_discount_independently():
    """They compound: an inferred action at an entity the subject merely chairs is doubly
    uncertain, and averaging the two would hide that."""
    best = episode(
        action_basis=ActionBasis.share_count,
        attribution=AttributionBasis.self_described,
        attribution_confidence=1.0,
    )
    weak_action = episode(action_basis=ActionBasis.raw_value, attribution_confidence=1.0)
    weak_attribution = episode(action_basis=ActionBasis.share_count, attribution_confidence=0.3)
    doubly_weak = episode(action_basis=ActionBasis.raw_value, attribution_confidence=0.3)

    assert best.training_weight == 1.0
    assert weak_action.training_weight == pytest.approx(0.3)
    assert weak_attribution.training_weight == pytest.approx(0.3)
    # 0.3 * 0.3, not (0.3 + 0.3) / 2 — the uncertainties multiply.
    assert doubly_weak.training_weight == pytest.approx(0.09)

    assert best.is_load_bearing
    assert not doubly_weak.is_load_bearing


def test_a_stated_decision_outranks_an_inferred_one():
    stated = episode(action_basis=ActionBasis.stated, attribution_confidence=1.0)
    inferred = episode(action_basis=ActionBasis.drift_adjusted_value, attribution_confidence=1.0)
    assert stated.training_weight > inferred.training_weight


# --- internal coherence -----------------------------------------------------------------------------


def test_a_hold_cannot_carry_a_traded_magnitude():
    with pytest.raises(ValidationError, match="no traded magnitude"):
        episode(observed_action=ObservedAction.hold, magnitude_low=0.1, magnitude_high=0.2)


def test_an_inverted_magnitude_range_is_rejected():
    with pytest.raises(ValidationError, match="inverted"):
        episode(magnitude_low=0.5, magnitude_high=0.2)


def test_a_rationale_may_postdate_the_decision_it_explains():
    """Managers explain themselves afterwards. The explanation is evidence about the decision;
    it is simply not something the replay actor is allowed to see."""
    late = episode(
        rationale_sources=[obs("shareholder letter", date(2017, 2, 25), source_kind="letter")]
    )
    assert late.rationale_sources[0].observed_at > DECISION_DATE
    assert "shareholder letter" not in late.for_replay().model_dump_json()
