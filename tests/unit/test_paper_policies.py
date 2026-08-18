"""The three v1 providers: what each one claims, and what it refuses to claim."""

from __future__ import annotations

import json

import pytest

from app.distillation.finance_nuwa import baselines
from app.distillation.finance_nuwa.prediction import BehavioralAction
from app.domain.policy import PolicyParameterName, Provenance
from app.paper.frozen_policy import (
    DEFAULT_POLICY_PATH,
    FrozenPolicyArtifact,
    FrozenPolicyProvider,
    load_artifact,
)
from app.paper.mock_policy import MOCK_NOTE, MockInvestorPolicy
from app.paper.quant_policy import QuantBehaviorProvider, encode_live, live_features
from tests.unit.paper_fixtures import sample_portfolio, sample_profile

# --- MockInvestorPolicy ------------------------------------------------------------------


def test_the_mock_is_deterministic_across_runs():
    profile, portfolio = sample_profile(), sample_portfolio()
    first = MockInvestorPolicy().decide(profile, portfolio)
    second = MockInvestorPolicy().decide(profile, portfolio)
    assert [(s.symbol, s.action, s.abstain) for s in first.stances] == [
        (s.symbol, s.action, s.abstain) for s in second.stances
    ]


def test_the_mock_says_on_every_stance_that_it_is_not_a_judgment():
    """A mock that read as plausible would produce runs that look like results."""
    view = MockInvestorPolicy().decide(sample_profile(), sample_portfolio())
    assert all(s.note == MOCK_NOTE for s in view.stances)


def test_the_mock_contributes_no_thresholds():
    """Invented policy numbers would appear in rationales a user reads."""
    view = MockInvestorPolicy().decide(sample_profile(), sample_portfolio())
    assert view.policy.parameters == {}


def test_the_mock_exercises_the_abstention_path_on_a_large_book():
    """Otherwise abstention is only tested when someone remembers to test it."""
    from app.domain.portfolio import Holding, Portfolio

    portfolio = Portfolio(
        holdings=[
            Holding(
                symbol=f"SYM{i:03d}",
                name=f"Holding {i}",
                asset_class="us_equity",
                quantity=10,
                market_value=1_000,
                account_type="taxable",
            )
            for i in range(60)
        ]
    )
    view = MockInvestorPolicy().decide(sample_profile(), portfolio)
    assert view.abstentions, "60 symbols should have produced at least one abstention"
    assert view.coverage < 1.0


# --- FrozenPolicyProvider ----------------------------------------------------------------


def test_the_shipped_artifact_loads_and_carries_a_concentration_threshold():
    artifact = load_artifact()
    profile = artifact.to_policy_profile()
    resolved = profile.resolve(PolicyParameterName.single_name_concentration, 0.20)
    assert resolved.value == pytest.approx(0.25)
    assert resolved.provenance is Provenance.derived


def test_the_artifact_never_claims_berkshire_published_a_number():
    """`direct` is a claim about the world, and for a single-name cap it is false."""
    artifact = load_artifact()
    for name, body in artifact.parameters.items():
        assert body.get("provenance") != Provenance.direct.value, (
            f"{name} claims the subject published this threshold; they did not"
        )


def test_a_direct_claim_without_a_source_is_refused(tmp_path):
    body = json.loads((_repo_root() / DEFAULT_POLICY_PATH).read_text())
    body["parameters"]["single_name_concentration"]["provenance"] = "direct"
    body["parameters"]["single_name_concentration"]["source_labels"] = []
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(body))

    with pytest.raises(ValueError, match="cites no source"):
        load_artifact(path)


def test_an_unknown_parameter_name_raises_rather_than_being_dropped():
    """Dropping it would run the engine on house numbers under this investor's name."""
    artifact = FrozenPolicyArtifact(
        policy_id="x",
        display_name="X",
        schema_version="v1",
        parameters={"max_single_name_weight": {"value": 0.3}},
    )
    with pytest.raises(ValueError, match="unknown policy parameter"):
        artifact.to_policy_profile()


def test_a_missing_artifact_raises_rather_than_falling_back():
    with pytest.raises(FileNotFoundError):
        load_artifact("config/paper/does-not-exist.json")


def test_the_frozen_policy_reduces_a_position_above_its_own_cap():
    view = FrozenPolicyProvider.from_path().decide(sample_profile(), sample_portfolio())
    nvda = view.stance_for("NVDA")
    assert nvda is not None
    assert nvda.action is BehavioralAction.reduce
    assert "25%" in nvda.note


def test_the_frozen_policy_exits_a_vestigial_position():
    view = FrozenPolicyProvider.from_path().decide(sample_profile(), sample_portfolio())
    tiny = view.stance_for("TINY")
    assert tiny is not None
    assert tiny.action is BehavioralAction.exit


def test_the_frozen_policy_drives_the_engine_with_its_own_threshold():
    """The whole point of routing numbers into the engine rather than into a prompt."""
    view = FrozenPolicyProvider.from_path().decide(sample_profile(), sample_portfolio())
    assert PolicyParameterName.single_name_concentration in view.policy.parameters


# --- QuantBehaviorProvider ---------------------------------------------------------------


def test_the_live_encoder_matches_the_frozen_one_column_for_column():
    """Pins `encode_live` against `baselines.encode` so the two cannot drift apart."""
    feature_set = "position+price"
    names = baselines.FEATURE_SETS[feature_set]

    design = encode_live([{n: 0.5 for n in names}], feature_set, ["x"])
    assert design.columns == [c for n in names for c in (n, f"{n}_missing")]
    assert design.rows[0] == [v for _ in names for v in (0.5, 0.0)]


def test_a_missing_feature_encodes_as_an_indicator_and_never_as_zero_alone():
    design = encode_live([{"weight": None}], "position", ["x"])
    # weight value 0.0 paired with missing indicator 1.0
    assert design.rows[0][0] == 0.0
    assert design.rows[0][1] == 1.0


def test_a_portfolio_without_price_history_reports_no_price_features():
    """None, never zero: a zero trailing return is a claim about the stock."""
    features = live_features(sample_portfolio(with_prices=False))["NVDA"]
    assert features["trailing_return_1q"] is None
    assert features["trailing_return_4q"] is None
    assert features["drawdown_from_peak"] is None
    assert features["weight"] == pytest.approx(270_000 / 418_800, rel=1e-6)


def test_price_history_fills_in_the_price_features():
    features = live_features(sample_portfolio(with_prices=True))["NVDA"]
    assert features["trailing_return_1q"] is not None
    assert features["drawdown_from_peak"] is not None
    assert features["drawdown_from_peak"] <= 0.0


def test_quarters_held_is_never_inferred():
    """A portfolio has no acquisition dates, and a price series is not one."""
    features = live_features(sample_portfolio(with_prices=True))["NVDA"]
    assert features["quarters_held"] is None


def test_the_quant_provider_abstains_when_it_has_too_little_to_go_on():
    """A confident label computed from four missing indicators is not an answer."""
    view = QuantBehaviorProvider.load().decide(
        sample_profile(), sample_portfolio(with_prices=False)
    )
    assert view.stances
    assert all(s.abstain for s in view.stances), "29% feature coverage should abstain throughout"
    assert view.coverage == 0.0


def test_the_quant_provider_answers_when_price_history_is_present():
    view = QuantBehaviorProvider.load().decide(sample_profile(), sample_portfolio(with_prices=True))
    assert view.coverage > 0.0
    answered = [s for s in view.stances if not s.abstain]
    assert answered
    assert all(s.action is not None for s in answered)


def test_the_quant_provider_carries_its_validation_score_and_config_hash():
    """So nobody has to go looking for how good it actually is."""
    provider = QuantBehaviorProvider.load()
    assert provider.validation_macro_f1 is not None
    assert provider.config_sha256
    view = provider.decide(sample_profile(), sample_portfolio(with_prices=True))
    assert provider.config_key in view.determinism_key


def test_the_quant_provider_refuses_to_be_used_unloaded():
    """Constructing directly would skip the refit and silently predict from nothing."""
    with pytest.raises(RuntimeError, match=r"\.load\(\)"):
        QuantBehaviorProvider().decide(sample_profile(), sample_portfolio())


def test_an_unknown_config_key_names_what_is_available():
    with pytest.raises(KeyError, match="not in"):
        QuantBehaviorProvider.load(config_key="no-such-config")


def _repo_root():
    from pathlib import Path

    return Path(__file__).resolve().parents[2]
