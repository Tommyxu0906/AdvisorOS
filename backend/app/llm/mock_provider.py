"""Deterministic mock provider.

Lets the entire committee pipeline, the evaluation harness, and CI run with no API key and no
network. Responses are shaped like the real ones (valid JSON against the requested schema) and
vary by advisor so persona-differentiation checks are meaningful.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from app.core.run_context import RunContext
from app.llm.provider import LLMResponse, Message
from app.llm.usage import LLMCallUsage

# Rough token accounting so cost tests exercise real arithmetic.
_CHARS_PER_TOKEN = 4


def _tokens(text: str) -> int:
    return max(1, len(text) // _CHARS_PER_TOKEN)


class MockLLMProvider:
    """Implements `LLMProvider` without any network access.

    Still requires a RunContext with credentials — the interface contract is identical, so tests
    exercise the same call path the real provider does.
    """

    def __init__(
        self, *, fail_on_roles: set[str] | None = None, refuse_on_roles: set[str] | None = None
    ):
        self.fail_on_roles = fail_on_roles or set()
        self.refuse_on_roles = refuse_on_roles or set()
        self.calls: list[dict[str, Any]] = []

    async def generate(
        self,
        messages: list[Message],
        context: RunContext,
        *,
        system: str | None = None,
        stable_system: str | None = None,
        role: str = "unspecified",
        advisor_id: str | None = None,
        schema: dict[str, Any] | None = None,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        # Record what was sent, so tests can assert no secret ever appears in a prompt.
        self.calls.append(
            {
                "role": role,
                "advisor_id": advisor_id,
                "system": system,
                "stable_system": stable_system,
                "messages": [m.model_dump() for m in messages],
                "schema": schema,
                "model": context.model_config.model,
            }
        )

        prompt_text = (stable_system or "") + (system or "") + "".join(m.content for m in messages)
        call_id = context.new_call_id()

        if role in self.fail_on_roles:
            usage = LLMCallUsage(
                call_id=call_id,
                run_id=context.run_id,
                role=role,
                advisor_id=advisor_id,
                model=context.model_config.model,
                input_tokens=_tokens(prompt_text),
                latency_ms=1.0,
                error="Anthropic request failed (MockInducedError).",
            )
            context.usage_tracker.record(usage)
            return LLMResponse(text="", usage=usage)

        if role in self.refuse_on_roles:
            usage = LLMCallUsage(
                call_id=call_id,
                run_id=context.run_id,
                role=role,
                advisor_id=advisor_id,
                model=context.model_config.model,
                input_tokens=_tokens(prompt_text),
                latency_ms=1.0,
            )
            context.usage_tracker.record(usage)
            return LLMResponse(text="", usage=usage, stop_reason="refusal", refused=True)

        payload = self._payload_for(role, advisor_id, schema)
        text = json.dumps(payload, indent=2) if schema else self._prose_for(role, advisor_id)

        usage = LLMCallUsage(
            call_id=call_id,
            run_id=context.run_id,
            role=role,
            advisor_id=advisor_id,
            model=context.model_config.model,
            input_tokens=_tokens(prompt_text),
            output_tokens=_tokens(text),
            # Simulate a cache hit on the stable prefix after the first call in a run.
            cache_read_tokens=_tokens(stable_system or "")
            if context.usage_tracker.call_count
            else 0,
            cache_creation_tokens=_tokens(stable_system or "")
            if not context.usage_tracker.call_count
            else 0,
            latency_ms=5.0,
        )
        context.usage_tracker.record(usage)

        return LLMResponse(
            text=text,
            parsed=payload if schema else None,
            usage=usage,
            stop_reason="end_turn",
        )

    # --- canned content ---------------------------------------------------------------

    def _seed(self, advisor_id: str | None, role: str) -> int:
        digest = hashlib.sha256(f"{advisor_id}:{role}".encode()).hexdigest()
        return int(digest[:8], 16)

    def _prose_for(self, role: str, advisor_id: str | None) -> str:
        who = advisor_id or "the committee"
        return f"[mock:{role}] Deterministic response from {who}."

    def _payload_for(
        self, role: str, advisor_id: str | None, schema: dict[str, Any] | None
    ) -> dict[str, Any]:
        who = advisor_id or "committee"
        seed = self._seed(advisor_id, role)
        confidence = round(0.4 + (seed % 50) / 100.0, 2)

        if role == "independent":
            return {
                "thesis": f"{who}: the binding constraint here is balance-sheet fragility, not asset selection.",
                "reasoning": (
                    f"{who} reasons from the supplied analytics: the guardrails identify the "
                    "immediate constraint, and any allocation change is downstream of resolving it."
                ),
                "recommendations": [
                    f"{who} recommendation one: address the flagged guardrail before reallocating.",
                    f"{who} recommendation two: size any change to what the profile can hold through a drawdown.",
                ],
                "risks_flagged": [f"{who} risk: the plan fails if income is interrupted."],
                "confidence": confidence,
                "declined": False,
                "declined_reason": "",
            }

        if role == "cross_examination":
            return {
                "agreement": f"{who} agrees the guardrail is the binding constraint.",
                "disagreement": f"{who} disputes the sequencing and would act on the debt first.",
                "strength": round(0.3 + (seed % 40) / 100.0, 2),
            }

        if role == "revised_memo":
            return {
                "thesis": f"{who} (revised): sequencing matters more than I first allowed.",
                "reasoning": f"{who} incorporates the critique and narrows the recommendation.",
                "recommendations": [
                    f"{who} revised recommendation: stage the change over two steps."
                ],
                "risks_flagged": [f"{who} revised risk: staging delays the benefit."],
                "confidence": min(1.0, confidence + 0.1),
                "declined": False,
                "declined_reason": "",
            }

        if role == "risk_challenge":
            return {
                "scenarios": [
                    "Income stops for nine months while the buffer is still thin.",
                    "The concentrated position halves before any trimming occurs.",
                ],
                "unaddressed_risks": ["Nobody costed the tax consequence of trimming."],
                "worst_case": "Forced selling at a loss into a drawdown with no cash reserve.",
            }

        if role == "synthesis":
            return {
                "summary": "The committee converges on resolving the flagged constraint first.",
                "consensus": [
                    "The triggered guardrail is the binding constraint.",
                    "Any allocation change should follow, not precede, it.",
                ],
                "disagreements": ["Sequencing: debt-first versus buffer-first."],
                "recommended_actions": [
                    "Resolve the blocking guardrail.",
                    "Then reduce single-position concentration in stages.",
                ],
                "open_questions": ["What is the actual tax basis of the concentrated position?"],
            }

        if role == "intent":
            return {"topics": ["general"], "is_decision_request": True, "has_urgency": False}

        if role == "nuwa_plan":
            # Return more questions than any depth needs; the distiller truncates to its budget.
            return {
                "questions": [f"How does the subject decide question {i}?" for i in range(1, 11)],
                "rationale": "Mock research plan.",
            }

        if role == "nuwa_research":
            return {
                "findings": [f"{who} finding."],
                "decision_rules": [f"{who} decision rule."],
                "limitations": [f"{who} limitation."],
                "sources": [f"{who} source."],
                "uncertainty": "Mock uncertainty.",
            }

        if role == "nuwa_synthesis":
            return {
                "display_name": "Mock Subject",
                "one_line": "A distilled reasoning profile produced by the mock provider.",
                "expertise": {
                    "liquidity_risk": 0.2,
                    "debt_pressure": 0.3,
                    "concentration_risk": 0.5,
                    "valuation_sensitivity": 0.9,
                    "behavioral_risk": 0.4,
                    "tax_complexity": 0.2,
                    "longevity_risk": 0.3,
                },
                "topic_affinity": ["valuation", "allocation"],
                "mental_models": ["Mock mental model."],
                "heuristics": ["Mock heuristic."],
                "reasoning_rules": ["Mock reasoning rule."],
                "blind_spots": ["Mock blind spot."],
                "honest_boundaries": ["Will not forecast markets."],
                "evidence": [{"label": "Mock source", "source": "mock", "note": ""}],
            }

        # Nuwa distillation roles and anything else: return a schema-shaped stub.
        if schema:
            return _stub_from_schema(schema, who)
        return {"text": self._prose_for(role, advisor_id)}


def _stub_from_schema(schema: dict[str, Any], who: str) -> dict[str, Any]:
    """Build a minimal object satisfying the required keys of a JSON schema."""
    props: dict[str, Any] = schema.get("properties", {})
    required: list[str] = schema.get("required", list(props))
    out: dict[str, Any] = {}
    for key in required:
        spec = props.get(key, {})
        out[key] = _stub_value(spec, key, who)
    return out


def _stub_value(spec: dict[str, Any], key: str, who: str) -> Any:
    kind = spec.get("type", "string")
    if kind == "string":
        return f"{who}:{key}"
    if kind in ("number", "integer"):
        return 1 if kind == "integer" else 0.5
    if kind == "boolean":
        return False
    if kind == "array":
        item = spec.get("items", {"type": "string"})
        return [_stub_value(item, key, who)]
    if kind == "object":
        return _stub_from_schema(spec, who)
    return None
