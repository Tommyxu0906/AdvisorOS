"""RunContext — the object that makes credentials explicit at every LLM call site.

Nothing in this codebase reaches the network without one. That is the whole point: there is no
ambient client, no global key, and no way to accidentally bill the project owner.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field

from app.core.credentials import UserLLMCredentials
from app.domain.report import AnalysisDepth
from app.llm.usage import UsageTracker

DEFAULT_MODEL = "claude-opus-5"


class ModelConfig(BaseModel):
    """Model and sampling configuration for one run.

    Note: `temperature` / `top_p` / `top_k` are deliberately absent — they are rejected by the
    current Claude models this app targets. Behaviour is steered by prompting and `effort`.
    """

    model_config = ConfigDict(extra="forbid")

    model: str = DEFAULT_MODEL
    max_tokens: int = Field(default=4096, ge=256, le=64000)
    effort: str = Field(default="medium", pattern="^(low|medium|high|xhigh|max)$")
    thinking: bool = Field(
        default=False,
        description=(
            "Request adaptive thinking explicitly. Note: current Claude models think by default, "
            "so this only makes the intent explicit — it is never used to *disable* thinking. "
            "Disabling thinking is a known source of leaked internal tags and malformed output, "
            "so cost is controlled with `effort` instead."
        ),
    )
    enable_prompt_caching: bool = True

    @classmethod
    def for_depth(cls, depth: AnalysisDepth, model: str = DEFAULT_MODEL) -> ModelConfig:
        """Sensible per-depth defaults. Cost rises with depth on purpose."""
        if depth is AnalysisDepth.quick:
            return cls(model=model, max_tokens=2048, effort="low", thinking=False)
        if depth is AnalysisDepth.balanced:
            return cls(model=model, max_tokens=3072, effort="medium", thinking=False)
        return cls(model=model, max_tokens=4096, effort="high", thinking=True)


@dataclass(slots=True)
class RunContext:
    """Per-run execution context. Required by every method that can reach Anthropic.

    Not a Pydantic model: it holds live objects (a tracker, credentials) that must never be
    serialized by accident.
    """

    credentials: UserLLMCredentials
    model_config: ModelConfig
    usage_tracker: UsageTracker
    depth: AnalysisDepth = AnalysisDepth.balanced
    run_id: str = field(default_factory=lambda: f"run_{uuid.uuid4().hex[:16]}")
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @classmethod
    def create(
        cls,
        credentials: UserLLMCredentials,
        *,
        depth: AnalysisDepth = AnalysisDepth.balanced,
        model: str = DEFAULT_MODEL,
        model_config: ModelConfig | None = None,
    ) -> RunContext:
        run_id = f"run_{uuid.uuid4().hex[:16]}"
        return cls(
            credentials=credentials,
            model_config=model_config or ModelConfig.for_depth(depth, model),
            usage_tracker=UsageTracker(run_id),
            depth=depth,
            run_id=run_id,
        )

    # Never let a context be printed with a key inside it.
    def __repr__(self) -> str:
        return (
            f"RunContext(run_id={self.run_id!r}, depth={self.depth.value!r}, "
            f"model={self.model_config.model!r}, credentials=<redacted>)"
        )

    __str__ = __repr__

    def new_call_id(self) -> str:
        return f"call_{uuid.uuid4().hex[:12]}"

    def elapsed_ms(self) -> float:
        return (datetime.now(UTC) - self.started_at).total_seconds() * 1000
