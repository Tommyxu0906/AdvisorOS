"""Provider contract, usage tracking, and cost estimation."""

from __future__ import annotations

import pytest

from app.core.run_context import ModelConfig, RunContext
from app.domain.report import AnalysisDepth
from app.llm.anthropic_provider import AnthropicBYOKProvider
from app.llm.mock_provider import MockLLMProvider
from app.llm.pricing import load_pricing
from app.llm.provider import LLMProvider, Message
from app.llm.usage import LLMCallUsage, UsageTracker, estimate_run_cost


def test_both_providers_satisfy_the_protocol() -> None:
    assert isinstance(MockLLMProvider(), LLMProvider)
    assert isinstance(AnthropicBYOKProvider(), LLMProvider)


async def test_provider_records_usage_on_the_context(context: RunContext) -> None:
    provider = MockLLMProvider()
    await provider.generate([Message(role="user", content="hi")], context, role="independent")
    usage = context.usage_tracker.aggregate()
    assert usage.total_calls == 1
    assert usage.total_input_tokens > 0
    assert usage.run_id == context.run_id


async def test_failed_call_is_recorded_and_flagged(context: RunContext) -> None:
    provider = MockLLMProvider(fail_on_roles={"independent"})
    resp = await provider.generate(
        [Message(role="user", content="hi")], context, role="independent"
    )
    assert not resp.ok
    usage = context.usage_tracker.aggregate()
    assert usage.failed_calls == 1


async def test_refusal_is_surfaced_not_treated_as_content(context: RunContext) -> None:
    provider = MockLLMProvider(refuse_on_roles={"independent"})
    resp = await provider.generate(
        [Message(role="user", content="hi")], context, role="independent"
    )
    assert resp.refused
    assert resp.text == ""
    assert not resp.ok


async def test_structured_output_is_parsed(context: RunContext) -> None:
    schema = {
        "type": "object",
        "properties": {"thesis": {"type": "string"}},
        "required": ["thesis"],
    }
    resp = await MockLLMProvider().generate(
        [Message(role="user", content="hi")], context, role="independent", schema=schema
    )
    assert resp.parsed is not None
    assert "thesis" in resp.parsed


def test_pricing_table_loads_and_is_versioned() -> None:
    pricing = load_pricing()
    assert pricing.pricing_version
    assert "claude-opus-5" in pricing.models
    price = pricing.price_for("claude-opus-5")
    assert price is not None
    # Cache reads are much cheaper than fresh input; writes cost more.
    assert price.cache_read_per_million < price.input_per_million
    assert price.cache_write_per_million > price.input_per_million


def test_cost_is_arithmetic_not_hardcoded() -> None:
    pricing = load_pricing()
    cost = pricing.cost_usd("claude-opus-5", input_tokens=1_000_000, output_tokens=1_000_000)
    expected = (
        pricing.models["claude-opus-5"].input_per_million
        + pricing.models["claude-opus-5"].output_per_million
    )
    assert cost == pytest.approx(expected)


def test_unknown_model_returns_none_not_zero() -> None:
    """An unpriced model must read as 'unknown', never as 'free'."""
    assert load_pricing().cost_usd("not-a-model", input_tokens=1000) is None


def test_unpriced_model_surfaces_in_aggregate() -> None:
    tracker = UsageTracker("run_x")
    tracker.record(
        LLMCallUsage(
            call_id="c1", run_id="run_x", role="independent", model="mystery", input_tokens=100
        )
    )
    usage = tracker.aggregate()
    assert usage.estimated_cost_usd is None
    assert usage.unpriced_models == ["mystery"]


def test_usage_breaks_down_by_stage_and_advisor() -> None:
    tracker = UsageTracker("run_y")
    for advisor in ("bogle", "marks"):
        tracker.record(
            LLMCallUsage(
                call_id=f"c_{advisor}",
                run_id="run_y",
                role="independent",
                advisor_id=advisor,
                model="claude-opus-5",
                input_tokens=1000,
                output_tokens=200,
            )
        )
    tracker.record(
        LLMCallUsage(
            call_id="c_s",
            run_id="run_y",
            role="synthesis",
            model="claude-opus-5",
            input_tokens=2000,
            output_tokens=400,
        )
    )
    usage = tracker.aggregate()
    assert usage.total_calls == 3
    assert {line.label for line in usage.by_stage} == {"independent", "synthesis"}
    assert {line.label for line in usage.by_advisor} == {"bogle", "marks"}
    assert sum(line.calls for line in usage.by_stage) == 3
    # Per-stage costs sum to the run total.
    assert sum(line.estimated_cost_usd or 0 for line in usage.by_stage) == pytest.approx(
        usage.estimated_cost_usd
    )


def test_cache_tokens_are_tracked_separately() -> None:
    tracker = UsageTracker("run_z")
    tracker.record(
        LLMCallUsage(
            call_id="c1",
            run_id="run_z",
            role="independent",
            model="claude-opus-5",
            input_tokens=100,
            cache_read_tokens=5000,
            cache_creation_tokens=1000,
        )
    )
    usage = tracker.aggregate()
    assert usage.total_cache_read_tokens == 5000
    assert usage.total_cache_creation_tokens == 1000
    assert usage.total_tokens == 6100


def test_pre_run_estimate_uses_supplied_averages() -> None:
    cost = estimate_run_cost("claude-opus-5", calls=8, avg_input_tokens=1500, avg_output_tokens=320)
    assert cost is not None and cost > 0
    doubled = estimate_run_cost(
        "claude-opus-5", calls=16, avg_input_tokens=1500, avg_output_tokens=320
    )
    assert doubled == pytest.approx(cost * 2)


def test_depth_call_counts_match_documented_workflow() -> None:
    assert AnalysisDepth.quick.expected_calls(3) == 4
    assert AnalysisDepth.balanced.expected_calls(3) == 8
    assert AnalysisDepth.deep.expected_calls(4) == 14
    # Deeper always costs more.
    counts = [d.expected_calls(3) for d in AnalysisDepth]
    assert counts == sorted(counts)


def test_model_config_scales_with_depth() -> None:
    quick = ModelConfig.for_depth(AnalysisDepth.quick)
    deep = ModelConfig.for_depth(AnalysisDepth.deep)
    assert quick.max_tokens < deep.max_tokens
    assert quick.effort == "low" and deep.effort == "high"


def test_model_config_rejects_sampling_parameters() -> None:
    """temperature/top_p are rejected by the target models; they must not be settable here."""
    with pytest.raises(ValueError):
        ModelConfig(temperature=0.5)  # type: ignore[call-arg]


def test_anthropic_request_separates_stable_and_dynamic_system_blocks(context) -> None:
    provider = AnthropicBYOKProvider()
    request = provider._build_request(
        messages=[Message(role="user", content="hi")],
        cfg=context.model_config,
        system="volatile user facts",
        stable_system="stable advisor profile",
        schema=None,
        max_tokens=None,
    )
    blocks = request["system"]
    assert blocks[0]["text"] == "stable advisor profile"
    assert blocks[0]["cache_control"] == {"type": "ephemeral"}
    assert blocks[1]["text"] == "volatile user facts"
    assert "cache_control" not in blocks[1]
    # Sampling params must never be sent.
    assert "temperature" not in request and "top_p" not in request


def test_anthropic_request_includes_structured_output_when_schema_given(context) -> None:
    schema = {"type": "object", "properties": {}, "required": []}
    request = AnthropicBYOKProvider()._build_request(
        messages=[Message(role="user", content="hi")],
        cfg=context.model_config,
        system=None,
        stable_system=None,
        schema=schema,
        max_tokens=512,
    )
    assert request["output_config"]["format"]["type"] == "json_schema"
    assert request["max_tokens"] == 512
