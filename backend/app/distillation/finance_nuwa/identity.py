"""What a security is, keyed on the thing that does not change.

Tickers are the obvious identifier and the wrong one. They get reassigned, they change on
rebranding, they differ by share class, and they move between exchanges — and a holdings history
keyed on them silently splices two different companies together or splits one company into two.
A dataset built that way produces episodes where a position appears to be sold and a new one
bought, when the company simply changed its symbol.

Institutional filings key on CUSIP, so this does too. CUSIP is not perfect either — it changes
on some corporate actions, which is exactly the case `CorporateActionResolver` has to handle —
but it is stable across the things that break tickers, and it is what the source actually says.

Ticker is therefore **enrichment, and never a precondition**. An episode must be constructible
with no ticker at all, because the alternative is dropping every position whose symbol lookup
failed, and lookups fail most often on exactly the delisted, merged, and renamed securities that
carry the most interesting decisions. When a ticker is present it carries its own provenance:
which mapping produced it, how confident that mapping is, and when it was true.
"""

from __future__ import annotations

import re
from datetime import date

from pydantic import BaseModel, ConfigDict, Field, field_validator

CUSIP_PATTERN = re.compile(r"^[0-9A-Z]{9}$")


class SecurityIdentity(BaseModel):
    """One security, identified by the field the filing actually keys on."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    # Length is checked by the validator rather than by Field, so one message explains what a
    # CUSIP is instead of two rules disagreeing about which fires first.
    cusip: str
    issuer_name: str = Field(min_length=1)
    title_of_class: str = Field(
        default="", description="COM, CL A, NOTE — share class is part of the identity"
    )

    # Enrichment. All optional together: a ticker with no provenance is worse than none, because
    # it looks authoritative and cannot be audited.
    ticker: str | None = None
    ticker_as_of: date | None = None
    mapping_source: str = ""
    mapping_confidence: float = Field(default=0.0, ge=0.0, le=1.0)

    @field_validator("cusip")
    @classmethod
    def _well_formed(cls, value: str) -> str:
        upper = value.strip().upper()
        if not CUSIP_PATTERN.match(upper):
            raise ValueError(f"{value!r} is not a 9-character CUSIP")
        return upper

    @property
    def key(self) -> tuple[str, str]:
        """What positions aggregate on.

        Share class is part of it: a filing that reports Class A and Class C separately is
        describing two securities with different votes and often different prices, and summing
        them would invent a position that does not exist.
        """
        return (self.cusip, self.title_of_class.strip().upper())

    @property
    def display(self) -> str:
        """Ticker when it has been mapped with any confidence, issuer name otherwise."""
        if self.ticker and self.mapping_confidence > 0:
            return self.ticker
        return self.issuer_name

    def with_ticker(
        self, ticker: str, *, source: str, confidence: float, as_of: date | None = None
    ) -> SecurityIdentity:
        """Attach a symbol without losing the record of where it came from."""
        return self.model_copy(
            update={
                "ticker": ticker.strip().upper(),
                "mapping_source": source,
                "mapping_confidence": confidence,
                "ticker_as_of": as_of,
            }
        )
