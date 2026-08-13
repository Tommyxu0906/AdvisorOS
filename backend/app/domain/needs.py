"""The need vector — the shared vocabulary between profile analytics and advisor selection.

This is the seam that lets advisor routing be deterministic: analytics produce a need vector,
advisors declare an expertise vector over the same dimensions, and selection is arithmetic.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class NeedDimension(str, Enum):
    liquidity_risk = "liquidity_risk"
    debt_pressure = "debt_pressure"
    concentration_risk = "concentration_risk"
    valuation_sensitivity = "valuation_sensitivity"
    behavioral_risk = "behavioral_risk"
    tax_complexity = "tax_complexity"
    longevity_risk = "longevity_risk"


NEED_DIMENSIONS: tuple[NeedDimension, ...] = tuple(NeedDimension)


class NeedVector(BaseModel):
    """Scores in [0, 1]; higher means "this profile needs more help here"."""

    model_config = ConfigDict(extra="forbid")

    liquidity_risk: float = Field(default=0.0, ge=0, le=1)
    debt_pressure: float = Field(default=0.0, ge=0, le=1)
    concentration_risk: float = Field(default=0.0, ge=0, le=1)
    valuation_sensitivity: float = Field(default=0.0, ge=0, le=1)
    behavioral_risk: float = Field(default=0.0, ge=0, le=1)
    tax_complexity: float = Field(default=0.0, ge=0, le=1)
    longevity_risk: float = Field(default=0.0, ge=0, le=1)

    def as_tuple(self) -> tuple[float, ...]:
        return tuple(getattr(self, d.value) for d in NEED_DIMENSIONS)

    def as_dict(self) -> dict[str, float]:
        return {d.value: getattr(self, d.value) for d in NEED_DIMENSIONS}

    def top(self, n: int = 3) -> list[tuple[str, float]]:
        return sorted(self.as_dict().items(), key=lambda kv: -kv[1])[:n]

    def dominant_dimensions(self, threshold: float = 0.4) -> list[str]:
        return [
            k for k, v in sorted(self.as_dict().items(), key=lambda kv: -kv[1]) if v >= threshold
        ]


class ExpertiseVector(BaseModel):
    """How well an advisor covers each need dimension. Same axes as NeedVector."""

    model_config = ConfigDict(extra="forbid")

    liquidity_risk: float = Field(default=0.0, ge=0, le=1)
    debt_pressure: float = Field(default=0.0, ge=0, le=1)
    concentration_risk: float = Field(default=0.0, ge=0, le=1)
    valuation_sensitivity: float = Field(default=0.0, ge=0, le=1)
    behavioral_risk: float = Field(default=0.0, ge=0, le=1)
    tax_complexity: float = Field(default=0.0, ge=0, le=1)
    longevity_risk: float = Field(default=0.0, ge=0, le=1)

    def as_tuple(self) -> tuple[float, ...]:
        return tuple(getattr(self, d.value) for d in NEED_DIMENSIONS)

    def as_dict(self) -> dict[str, float]:
        return {d.value: getattr(self, d.value) for d in NEED_DIMENSIONS}


def dot(need: NeedVector, expertise: ExpertiseVector) -> float:
    return sum(n * e for n, e in zip(need.as_tuple(), expertise.as_tuple(), strict=True))


def cosine(a: ExpertiseVector, b: ExpertiseVector) -> float:
    av, bv = a.as_tuple(), b.as_tuple()
    num = sum(x * y for x, y in zip(av, bv, strict=True))
    na = sum(x * x for x in av) ** 0.5
    nb = sum(y * y for y in bv) ** 0.5
    if na == 0 or nb == 0:
        return 0.0
    return num / (na * nb)
