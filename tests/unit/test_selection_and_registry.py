"""Advisor registry, runtime profiles, and deterministic selection."""

from __future__ import annotations

import json

import pytest

from app.advisors.registry import AdvisorNotFound, AdvisorRegistry, render_skill_md
from app.advisors.selection import (
    COVERAGE_THRESHOLD,
    score_advisors,
    select_committee,
)
from app.analytics.profile_analytics import analyze_profile
from app.domain.needs import NEED_DIMENSIONS
from app.domain.question import QuestionTopic, UserQuestion
from app.domain.report import AnalysisDepth
from app.nuwa.importer import ImportError_, validate_external


def test_registry_loads_all_builtins(registry: AdvisorRegistry) -> None:
    assert len(registry) >= 6
    assert {"bogle", "buffett", "munger", "marks", "damodaran", "housel"} <= set(registry.ids())


def test_every_advisor_declares_limits(registry: AdvisorRegistry) -> None:
    """A persona with no acknowledged limits is not safe to ship."""
    for m in registry.all_manifests():
        assert m.blind_spots, f"{m.advisor_id} declares no blind spots"
        assert m.honest_boundaries, f"{m.advisor_id} declares no honest boundaries"
        assert m.mental_models or m.heuristics


def test_expertise_vectors_are_discriminating(registry: AdvisorRegistry) -> None:
    """No advisor may be strong at everything — that would break routing."""
    for m in registry.all_manifests():
        scores = m.expertise.as_dict()
        high = [v for v in scores.values() if v >= 0.8]
        assert len(high) <= 3, f"{m.advisor_id} is a generalist and cannot differentiate"
        assert any(v >= 0.6 for v in scores.values()), f"{m.advisor_id} is strong at nothing"


def test_runtime_profile_is_bounded_and_excludes_source_material(
    registry: AdvisorRegistry,
) -> None:
    """The runtime profile is the cost lever: it must stay small and exclude provenance."""
    for m in registry.all_manifests():
        rp = m.to_runtime_profile()
        rendered = rp.render()
        assert rp.approx_tokens() < 1200, f"{m.advisor_id} runtime profile is too large"
        assert m.provenance not in rendered
        assert "SKILL.md" not in rendered
        # Evidence appears as labels only, never full source text.
        for e in m.evidence:
            assert e.note not in rendered or not e.note


def test_skill_md_is_not_sent_at_runtime(registry: AdvisorRegistry) -> None:
    m = registry.manifest("bogle")
    skill = render_skill_md(m)
    runtime = m.to_runtime_profile().render()
    assert "Provenance" in skill
    assert "Provenance" not in runtime
    assert len(skill) > len(runtime)


def test_unknown_advisor_raises(registry: AdvisorRegistry) -> None:
    with pytest.raises(AdvisorNotFound):
        registry.manifest("nobody")


def test_selection_is_deterministic(
    registry, stressed_profile, concentrated_portfolio, analyzed
) -> None:
    analytics, _, rails = analyzed
    intent = UserQuestion(text="Should I sell NVDA to pay the card?").classify()
    a = select_committee(registry.all_manifests(), analytics.need_vector, intent, rails)
    b = select_committee(registry.all_manifests(), analytics.need_vector, intent, rails)
    assert a.advisor_ids == b.advisor_ids


def test_selection_respects_depth_sizing(registry, analyzed) -> None:
    analytics, _, rails = analyzed
    intent = UserQuestion(text="How should I allocate?").classify()
    for depth in AnalysisDepth:
        sel = select_committee(
            registry.all_manifests(), analytics.need_vector, intent, rails, depth
        )
        assert depth.min_advisor_count <= len(sel.selected) <= depth.advisor_count
        # Never the 8-10 agent sprawl the architecture could technically support.
        assert len(sel.selected) <= 4


def test_committee_is_never_a_single_advisor(registry, healthy_profile) -> None:
    """Even when one advisor covers everything, a committee needs peers to disagree."""
    analytics = analyze_profile(healthy_profile)
    intent = UserQuestion(text="Anything I should change?").classify()
    sel = select_committee(registry.all_manifests(), analytics.need_vector, intent, [])
    assert len(sel.selected) >= 3


def test_guardrails_force_coverage(registry, analyzed) -> None:
    analytics, _, rails = analyzed
    intent = UserQuestion(text="Should I buy more NVDA?").classify()
    sel = select_committee(registry.all_manifests(), analytics.need_vector, intent, rails)

    assert "debt_pressure" in sel.mandatory_dimensions
    assert "liquidity_risk" in sel.mandatory_dimensions
    # Every mandatory dimension is covered by someone on the committee.
    chosen = [registry.manifest(i) for i in sel.advisor_ids]
    for dim in sel.mandatory_dimensions:
        assert any(m.expertise.as_dict()[dim] >= COVERAGE_THRESHOLD for m in chosen), dim


def test_selection_explains_itself(registry, analyzed) -> None:
    analytics, _, rails = analyzed
    intent = UserQuestion(text="Should I sell NVDA?").classify()
    sel = select_committee(registry.all_manifests(), analytics.need_vector, intent, rails)
    for s in sel.selected:
        assert s.rationale.endswith(".")
        assert not s.rationale.startswith(("and ", "; "))
        assert len(s.rationale) > 20


def test_different_profiles_select_different_committees(
    registry, stressed_profile, healthy_profile, concentrated_portfolio, analyzed
) -> None:
    analytics_stressed, _, rails_stressed = analyzed
    analytics_healthy = analyze_profile(healthy_profile)

    a = select_committee(
        registry.all_manifests(),
        analytics_stressed.need_vector,
        UserQuestion(text="Should I sell NVDA to pay my card?").classify(),
        rails_stressed,
    )
    b = select_committee(
        registry.all_manifests(),
        analytics_healthy.need_vector,
        UserQuestion(text="Am I ready to retire in four years?").classify(),
        [],
    )
    assert a.advisor_ids != b.advisor_ids


def test_scoring_prefers_topic_relevant_advisors(registry, analyzed) -> None:
    analytics, _, rails = analyzed
    intent = UserQuestion(text="Is the market overvalued? What is fair value?").classify()
    assert QuestionTopic.valuation in intent.topics
    scores = score_advisors(registry.all_manifests(), analytics.need_vector, intent, rails)
    top_ids = [s.advisor_id for s in scores[:3]]
    assert any(i in top_ids for i in ("damodaran", "marks", "buffett"))


def test_question_classification_is_deterministic_and_keyword_driven() -> None:
    q = UserQuestion(text="Should I pay off my credit card or invest right now?")
    intent = q.classify()
    assert intent.is_decision_request
    assert intent.has_urgency
    assert QuestionTopic.debt in intent.topics
    assert q.classify().model_dump() == intent.model_dump()


def test_unmatched_question_falls_back_to_general() -> None:
    intent = UserQuestion(text="zzzz qqqq").classify()
    assert intent.topics == [QuestionTopic.general]


def test_need_and_expertise_share_axes() -> None:
    from app.domain.needs import ExpertiseVector, NeedVector

    assert set(NeedVector().as_dict()) == set(ExpertiseVector().as_dict())
    assert len(NEED_DIMENSIONS) == 7


# --- external artifact import ---------------------------------------------------------


def _valid_external() -> dict:
    return {
        "advisor_id": "graham",
        "display_name": "Benjamin Graham",
        "subject": "Benjamin Graham",
        "one_line": "Buy with a margin of safety.",
        "expertise": {"valuation_sensitivity": 0.9, "concentration_risk": 0.5},
        "mental_models": ["Margin of safety"],
        "blind_spots": ["Ignores growth"],
        "honest_boundaries": ["Will not forecast markets"],
    }


def test_import_accepts_valid_artifact(registry: AdvisorRegistry) -> None:
    manifest = validate_external(_valid_external())
    assert manifest.advisor_id == "graham"
    assert manifest.origin.value == "custom"


@pytest.mark.parametrize("drop", ["blind_spots", "honest_boundaries"])
def test_import_rejects_persona_without_declared_limits(drop: str) -> None:
    data = _valid_external()
    data[drop] = []
    with pytest.raises(ImportError_):
        validate_external(data)


def test_import_rejects_all_zero_expertise() -> None:
    data = _valid_external()
    data["expertise"] = {}
    with pytest.raises(ImportError_):
        validate_external(data)


def test_registry_persists_custom_advisor(tmp_path) -> None:
    registry = AdvisorRegistry(custom_dir=tmp_path)
    manifest = validate_external(_valid_external())
    registry.register(manifest, persist=True)

    written = tmp_path / "graham" / "manifest.json"
    assert written.exists()
    assert (tmp_path / "graham" / "SKILL.md").exists()
    assert json.loads(written.read_text())["advisor_id"] == "graham"

    # A fresh registry over the same directory picks it up.
    assert "graham" in AdvisorRegistry(custom_dir=tmp_path)
