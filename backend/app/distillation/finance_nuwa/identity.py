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

# Bumped whenever the fields that constitute an identity change, because episode ids are built
# from it and two datasets with different key schemas cannot be compared row for row.
SECURITY_KEY_SCHEMA_VERSION = "security-key-v1"

# CUSIPs are alphanumeric, so this can never appear inside one and a token always splits cleanly.
KEY_SEPARATOR = ":"


class SecurityKey(BaseModel):
    """What a security *is*, for every purpose downstream of parsing.

    A bare CUSIP was the identifier everywhere below the parser, and it is one field short. A
    filing may report two share classes of the same issuer under one CUSIP, and a dictionary keyed
    on CUSIP alone silently keeps whichever it saw last — one position overwrites another, and
    nothing anywhere reports that it happened. This type makes that impossible rather than
    unlikely: two classes are two keys, and there is no code path that collapses them.

    The class token is the filer's `titleOfClass`, normalised for whitespace and case and
    otherwise left exactly as filed. Parsing it into a canonical class designator was the obvious
    alternative and is a bad idea — it is free text, filers write "COM", "CL A", "COM SER C
    FRMLA", and a regex that maps those to A/B/C is inventing a fact the filing does not contain.
    Where a label genuinely changes for a security that never moved, that is a *relabeling*, and
    it is resolved the way every other identity change in this pipeline is: detected, then either
    curated with evidence or quarantined. Never patched over by a normaliser.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    cusip: str
    title_of_class: str = ""

    @field_validator("cusip")
    @classmethod
    def _well_formed(cls, value: str) -> str:
        upper = value.strip().upper()
        if not CUSIP_PATTERN.match(upper):
            raise ValueError(f"{value!r} is not a 9-character CUSIP")
        return upper

    @field_validator("title_of_class")
    @classmethod
    def _normalised(cls, value: str) -> str:
        return " ".join(value.upper().split())

    @classmethod
    def of(cls, identity: SecurityIdentity) -> SecurityKey:
        return cls(cusip=identity.cusip, title_of_class=identity.title_of_class)

    @classmethod
    def parse(cls, token: str) -> SecurityKey:
        """Round-trip a serialized key. Every artifact row stores `token`, and reading one back
        must produce the identical key or the frozen dataset does not mean what it says."""
        cusip, _, title = token.partition(KEY_SEPARATOR)
        return cls(cusip=cusip, title_of_class=title)

    @property
    def token(self) -> str:
        """Stable serialized form. This is what goes in a JSONL row and a manifest."""
        return f"{self.cusip}{KEY_SEPARATOR}{self.title_of_class}"

    @property
    def slug(self) -> str:
        """Filename- and identifier-safe form, used to build episode ids."""
        cleaned = re.sub(r"[^a-z0-9]+", "-", self.token.lower()).strip("-")
        return cleaned

    def __str__(self) -> str:
        return self.token


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
    def key(self) -> SecurityKey:
        """What positions aggregate on, and what everything downstream is keyed by.

        Share class is part of it: a filing that reports Class A and Class C separately is
        describing two securities with different votes and often different prices, and summing
        them would invent a position that does not exist.

        A typed key rather than a tuple, because a tuple is structurally identical to every other
        two-string tuple in the program and a bare CUSIP string is assignable wherever one is
        expected. Neither mistake type-checks against this.
        """
        return SecurityKey.of(self)

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
