"""Model pricing and cost estimation.

Pricing is loaded from `config/model_pricing.json` — never hardcoded at call sites — so it can
be updated without touching application code, and so the version used for a given run is
recorded alongside the number.

Everything this module produces is an *estimate*. It is not Anthropic's billing figure.
"""

from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

DEFAULT_PRICING_PATH = Path(__file__).resolve().parents[3] / "config" / "model_pricing.json"


class ModelPrice(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: str = ""
    input_per_million: float = Field(ge=0)
    output_per_million: float = Field(ge=0)
    cache_read_per_million: float = Field(default=0.0, ge=0)
    cache_write_per_million: float = Field(default=0.0, ge=0)


class PricingTable(BaseModel):
    model_config = ConfigDict(extra="allow")

    pricing_version: str
    currency: str = "USD"
    models: dict[str, ModelPrice]

    def price_for(self, model: str) -> ModelPrice | None:
        return self.models.get(model)

    def cost_usd(
        self,
        model: str,
        *,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cache_read_tokens: int = 0,
        cache_creation_tokens: int = 0,
    ) -> float | None:
        """Estimated cost in USD, or None when the model is not in the table.

        Returning None rather than 0.0 matters: an unknown model must surface as "unknown",
        not as "free".
        """
        price = self.price_for(model)
        if price is None:
            return None
        m = 1_000_000
        return (
            input_tokens * price.input_per_million
            + output_tokens * price.output_per_million
            + cache_read_tokens * price.cache_read_per_million
            + cache_creation_tokens * price.cache_write_per_million
        ) / m


@lru_cache(maxsize=4)
def load_pricing(path: str | None = None) -> PricingTable:
    resolved = Path(path or os.environ.get("AIFA_PRICING_PATH") or DEFAULT_PRICING_PATH)
    data = json.loads(resolved.read_text())
    return PricingTable.model_validate(data)


def known_models() -> list[str]:
    return sorted(load_pricing().models)
