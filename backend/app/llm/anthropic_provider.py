"""Anthropic BYOK provider.

Every call builds a fresh client from the RunContext's credentials. There is no shared client,
no module-level key, and no environment fallback.
"""

from __future__ import annotations

import json
import time
from typing import Any

from app.core.credentials import AnthropicClientFactory, safe_error_message
from app.core.run_context import RunContext
from app.llm.provider import LLMResponse, Message
from app.llm.usage import LLMCallUsage


class AnthropicBYOKProvider:
    """Implements `LLMProvider` against the real Anthropic API using the user's key."""

    def __init__(self, factory: AnthropicClientFactory | None = None) -> None:
        self._factory = factory or AnthropicClientFactory()

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
        cfg = context.model_config
        call_id = context.new_call_id()
        started = time.perf_counter()

        client = self._factory.create(context.credentials)
        request = self._build_request(
            messages=messages,
            cfg=cfg,
            system=system,
            stable_system=stable_system,
            schema=schema,
            max_tokens=max_tokens,
        )

        try:
            # Streaming keeps long, high-max_tokens calls from hitting HTTP timeouts.
            async with client.messages.stream(**request) as stream:
                message = await stream.get_final_message()
        except Exception as exc:  # noqa: BLE001 - normalized into a safe usage record
            usage = LLMCallUsage(
                call_id=call_id,
                run_id=context.run_id,
                role=role,
                advisor_id=advisor_id,
                model=cfg.model,
                latency_ms=(time.perf_counter() - started) * 1000,
                error=safe_error_message(exc),
            )
            context.usage_tracker.record(usage)
            return LLMResponse(text="", usage=usage, refused=False)
        finally:
            await client.close()

        latency_ms = (time.perf_counter() - started) * 1000
        raw_usage = message.usage
        usage = LLMCallUsage(
            call_id=call_id,
            run_id=context.run_id,
            role=role,
            advisor_id=advisor_id,
            model=message.model or cfg.model,
            input_tokens=getattr(raw_usage, "input_tokens", 0) or 0,
            output_tokens=getattr(raw_usage, "output_tokens", 0) or 0,
            cache_read_tokens=getattr(raw_usage, "cache_read_input_tokens", 0) or 0,
            cache_creation_tokens=getattr(raw_usage, "cache_creation_input_tokens", 0) or 0,
            latency_ms=latency_ms,
        )
        context.usage_tracker.record(usage)

        # Safety classifiers can decline a request: HTTP 200 with stop_reason "refusal".
        # Check before reading content — a refusal has no usable text.
        if message.stop_reason == "refusal":
            return LLMResponse(
                text="",
                usage=usage,
                stop_reason="refusal",
                refused=True,
            )

        text = "".join(b.text for b in message.content if getattr(b, "type", None) == "text")
        parsed: dict[str, Any] | None = None
        if schema is not None and text.strip():
            try:
                candidate = json.loads(text)
                parsed = candidate if isinstance(candidate, dict) else {"value": candidate}
            except json.JSONDecodeError:
                parsed = None

        return LLMResponse(
            text=text,
            parsed=parsed,
            usage=usage,
            stop_reason=message.stop_reason,
        )

    def _build_request(
        self,
        *,
        messages: list[Message],
        cfg,  # ModelConfig
        system: str | None,
        stable_system: str | None,
        schema: dict[str, Any] | None,
        max_tokens: int | None,
    ) -> dict[str, Any]:
        # Stable content first so the cacheable prefix is as long as possible; the volatile
        # per-user content follows the cache breakpoint.
        system_blocks: list[dict[str, Any]] = []
        if stable_system:
            block: dict[str, Any] = {"type": "text", "text": stable_system}
            if cfg.enable_prompt_caching:
                block["cache_control"] = {"type": "ephemeral"}
            system_blocks.append(block)
        if system:
            system_blocks.append({"type": "text", "text": system})

        request: dict[str, Any] = {
            "model": cfg.model,
            "max_tokens": max_tokens or cfg.max_tokens,
            "messages": [m.model_dump() for m in messages],
            "output_config": {"effort": cfg.effort},
        }
        if system_blocks:
            request["system"] = system_blocks
        if cfg.thinking:
            request["thinking"] = {"type": "adaptive"}
        if schema is not None:
            request["output_config"]["format"] = {"type": "json_schema", "schema": schema}
        return request
