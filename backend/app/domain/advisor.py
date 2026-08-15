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

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.persona_text import sanitize_persona_lines, sanitize_persona_text
from app.domain.needs import ExpertiseVector
from app.domain.policy import PolicyProfile
from app.domain.question import QuestionTopic

# Every field whose contents end up inside a system prompt. Distillation reads untrusted
# material, so these are validated for content and not only for type — see core/persona_text.py.
PROMPT_BEARING_LISTS = (
    "mental_models",
    "heuristics",
    "reasoning_rules",
    "blind_spots",
    "honest_boundaries",
)


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

    policy: PolicyProfile = Field(default_factory=PolicyProfile)

    evidence: list[EvidenceRef] = Field(default_factory=list)
    provenance: str = ""
    distilled_at: datetime | None = None
    schema_version: int = 1

    # The gate sits on the model rather than on the importer and the distiller, because those are
    # two of the paths that build a manifest today and a validator that a new caller can skip is
    # not a validator. A rejection here fails the whole artifact, which is the intent: a persona
    # carrying an instruction aimed at the model is not a persona with one bad bullet.
    @field_validator(*PROMPT_BEARING_LISTS)
    @classmethod
    def _sanitize_lists(cls, value: list[str], info) -> list[str]:
        return sanitize_persona_lines(value, field=info.field_name)

    @field_validator("one_line", "display_name")
    @classmethod
    def _sanitize_text(cls, value: str, info) -> str:
        return sanitize_persona_text(value, field=info.field_name)

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
            policy=self.policy,
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
    # Carried as data, never rendered into the prompt — same treatment `expertise` gets. The
    # policy engine reads these; the model sees the actions they produced, not the constants.
    expertise: ExpertiseVector
    topic_affinity: list[QuestionTopic] = Field(default_factory=list)
    policy: PolicyProfile = Field(default_factory=PolicyProfile)

    # Repeated from the manifest rather than inherited from it. A runtime profile is normally
    # compiled from an already-validated manifest, but it is also the object that gets stored,
    # cached, and reconstructed from a database row, and it is the one that actually reaches the
    # prompt. The last object before the boundary should not be trusting an earlier one.
    @field_validator(*PROMPT_BEARING_LISTS)
    @classmethod
    def _sanitize_lists(cls, value: list[str], info) -> list[str]:
        return sanitize_persona_lines(value, field=info.field_name)

    @field_validator("one_line", "display_name")
    @classmethod
    def _sanitize_text(cls, value: str, info) -> str:
        return sanitize_persona_text(value, field=info.field_name)

    def render(self) -> str:
        """Stable text block for the system prompt. Deterministic — safe to prompt-cache.

        Two things this wording is careful about, both of which cost nothing and matter.

        **A lens, not a person.** The obvious framing — "you are Warren Buffett, answer in the
        first person" — is what a persona system normally does, and it is the wrong choice when
        real money is involved. It invites "I would hold this stock", which is a sentence the
        real person never said about a portfolio they never saw, presented as though they had.
        The honest object is a framework read off the public record, so the bullets describe what
        *it* applies rather than what *you* would do.

        **The description is data.** These bullets were distilled from material about a public
        figure, and for a custom advisor that material came off the open web.

        The rules enforcing both — never write in the subject's voice, treat this block as
        reference content and not instructions — live in `COMMITTEE_CHARTER`, because they are
        identical for every advisor and this block is repeated per advisor inside a cached
        prefix. Saying them here would buy nothing and pay for it on every call.
        """

        def bullets(title: str, items: list[str]) -> str:
            if not items:
                return ""
            body = "\n".join(f"- {i}" for i in items)
            return f"\n{title}:\n{body}\n"

        parts = [
            f"You are reasoning in the style of a decision framework distilled from the public "
            f"record of {self.display_name}.",
            f"The edge it claims: {self.one_line}",
            bullets("Mental models it applies", self.mental_models),
            bullets("Heuristics it relies on", self.heuristics),
            bullets("Rules that govern its reasoning", self.reasoning_rules),
            bullets("Its known blind spots (acknowledge them when relevant)", self.blind_spots),
            bullets("Questions it declines to answer", self.honest_boundaries),
        ]
        if self.evidence_labels:
            parts.append("\nEvidence it draws on: " + "; ".join(self.evidence_labels) + "\n")
        return "".join(p for p in parts if p).strip()

    def approx_tokens(self) -> int:
        """Rough size estimate (~4 chars/token) for the evaluation harness."""
        return max(1, len(self.render()) // 4)
