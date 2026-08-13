"""LLM provider interface.

Deliberately minimal. Anthropic is the only real provider in V1; the Protocol exists so the
committee orchestrator can be exercised in tests and CI with `MockLLMProvider` and no key.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from app.core.run_context import RunContext
from app.llm.usage import LLMCallUsage


class Message(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: str = Field(pattern="^(user|assistant)$")
    content: str


class LLMResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str
    parsed: dict[str, Any] | None = None
    usage: LLMCallUsage
    stop_reason: str | None = None
    refused: bool = False

    @property
    def ok(self) -> bool:
        return not self.refused and self.usage.error is None


@runtime_checkable
class LLMProvider(Protocol):
    """Every implementation must require a RunContext. No ambient credentials."""

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
        """Generate one completion and record its usage on `context.usage_tracker`.

        `stable_system` is content that does not vary across calls in a run (advisor runtime
        profile, guardrail text). It is placed first and marked cacheable. `system` is the
        volatile remainder. Keeping them separate is what makes prompt caching effective.
        """
        ...
