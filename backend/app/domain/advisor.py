"""Advisor artifacts.

Two representations, deliberately separated:

  AdvisorManifest       full Nuwa output — evidence, provenance, long-form source. NEVER sent to
                        Claude at committee runtime. Kept for traceability.
  AdvisorRuntimeProfile compact derivative that IS sent, once per advisor call. Bounded size is
                        the single largest cost lever in the system.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

from app.domain.needs import ExpertiseVector
from app.domain.question import QuestionTopic


class AdvisorOrigin(str, Enum):
    builtin = "builtin"
    custom = "custom"


class EvidenceRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str
    source: str = ""
    year: int | None = None
    note: str = ""


class AdvisorManifest(BaseModel):
    """The complete distilled artifact for one advisor persona."""

    model_config = ConfigDict(extra="forbid")

    advisor_id: str = Field(pattern=r"^[a-z0-9_]+$")
    display_name: str
    subject: str = Field(description="The real person or school of thought being distilled")
    origin: AdvisorOrigin = AdvisorOrigin.builtin

    one_line: str = Field(description="How this advisor would describe their own edge")
    expertise: ExpertiseVector
    topic_affinity: list[QuestionTopic] = Field(default_factory=list)

    mental_models: list[str] = Field(default_factory=list, max_length=12)
    heuristics: list[str] = Field(default_factory=list, max_length=12)
    reasoning_rules: list[str] = Field(default_factory=list, max_length=10)
    blind_spots: list[str] = Field(default_factory=list, max_length=8)
    honest_boundaries: list[str] = Field(
        default_factory=list,
        max_length=8,
        description="Things this advisor should explicitly decline to opine on",
    )
    disagrees_with: list[str] = Field(
        default_factory=list,
        description="advisor_ids this persona characteristically pushes back on",
    )

    evidence: list[EvidenceRef] = Field(default_factory=list)
    provenance: str = ""
    distilled_at: datetime | None = None
    schema_version: int = 1

    def to_runtime_profile(self, *, max_items: int = 6) -> AdvisorRuntimeProfile:
        """Compile the compact runtime representation actually sent to Claude."""
        return AdvisorRuntimeProfile(
            advisor_id=self.advisor_id,
            display_name=self.display_name,
            one_line=self.one_line,
            mental_models=self.mental_models[:max_items],
            heuristics=self.heuristics[:max_items],
            reasoning_rules=self.reasoning_rules[:max_items],
            blind_spots=self.blind_spots[: max(2, max_items // 2)],
            honest_boundaries=self.honest_boundaries[: max(2, max_items // 2)],
            evidence_labels=[e.label for e in self.evidence[:4]],
            expertise=self.expertise,
            topic_affinity=list(self.topic_affinity),
        )


class AdvisorRuntimeProfile(BaseModel):
    """Compact, prompt-ready advisor context. Target: ~1,200 tokens or fewer."""

    model_config = ConfigDict(extra="forbid")

    advisor_id: str
    display_name: str
    one_line: str
    mental_models: list[str] = Field(default_factory=list)
    heuristics: list[str] = Field(default_factory=list)
    reasoning_rules: list[str] = Field(default_factory=list)
    blind_spots: list[str] = Field(default_factory=list)
    honest_boundaries: list[str] = Field(default_factory=list)
    evidence_labels: list[str] = Field(default_factory=list)
    expertise: ExpertiseVector
    topic_affinity: list[QuestionTopic] = Field(default_factory=list)

    def render(self) -> str:
        """Stable text block for the system prompt. Deterministic — safe to prompt-cache."""

        def bullets(title: str, items: list[str]) -> str:
            if not items:
                return ""
            body = "\n".join(f"- {i}" for i in items)
            return f"\n{title}:\n{body}\n"

        parts = [
            f"You are reasoning in the style of {self.display_name}.",
            f"Your edge: {self.one_line}",
            bullets("Mental models you apply", self.mental_models),
            bullets("Heuristics you rely on", self.heuristics),
            bullets("Rules that govern your reasoning", self.reasoning_rules),
            bullets("Your known blind spots (acknowledge them when relevant)", self.blind_spots),
            bullets("Questions you decline to answer", self.honest_boundaries),
        ]
        if self.evidence_labels:
            parts.append("\nEvidence you draw on: " + "; ".join(self.evidence_labels) + "\n")
        return "".join(p for p in parts if p).strip()

    def approx_tokens(self) -> int:
        """Rough size estimate (~4 chars/token) for the evaluation harness."""
        return max(1, len(self.render()) // 4)
