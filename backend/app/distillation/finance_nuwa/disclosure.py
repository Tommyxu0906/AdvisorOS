"""What a filing actually shows, when it became knowable, and what it leaves out.

Three separate dates hide inside a single institutional holdings filing, and conflating any two
of them corrupts the dataset in a way no later modelling can undo.

    period_start ──────── period_end ──────── filed_at
         │                     │                  │
         └── the decision      └── the position   └── the day anyone
             happened              was measured       outside could know

**The decision is a window, not a date.** A quarterly filing says a position existed on the last
day of the quarter. It does not say when it was bought. Dating the decision to the period end
and then feeding it everything knowable up to that day hands the model up to three months of
information the investor did not have when they acted. So an episode carries the window, and
inputs must predate its *opening*. That is deliberately the conservative end: it throws away
real information from inside the quarter rather than risk leaking any.

**Knowable is not the same as true.** The position was real on the period end date; nobody
outside could see it until the filing landed, typically about 45 days later. `filed_at` is what
`Observation.observed_at` must use when a filing is an input to some *other* episode — using the
period end would be the same leak in a different disguise.

**Coverage is partial and the gaps are not random.** A 13F covers US-listed long equity above a
reporting threshold. It says nothing about cash, private operating businesses, bonds, foreign
listings, short positions, or holdings below the threshold — and for an entity like Berkshire,
whose wholly-owned businesses dwarf the marketable book, the omissions are most of the balance
sheet. A model trained on this without knowing that will confidently explain a "portfolio" it
has only partially seen, and "they hold no bonds" is a conclusion the data cannot support.
"""

from __future__ import annotations

from datetime import date
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, model_validator


class DisclosureKind(str, Enum):
    institutional_holdings = "institutional_holdings"
    """A periodic holdings filing, e.g. a 13F. Long US equity above a threshold."""

    shareholder_letter = "shareholder_letter"
    annual_report = "annual_report"
    interview = "interview"
    self_reported = "self_reported"
    """The subject described the position or trade themselves."""


# What an institutional holdings filing does not contain. Stated once, here, so that every
# episode built from one inherits the same honest account rather than each caller improvising.
INSTITUTIONAL_HOLDINGS_OMISSIONS: tuple[str, ...] = (
    "cash and cash equivalents",
    "private and wholly-owned operating businesses",
    "fixed income and most non-equity securities",
    "foreign-listed holdings outside the reporting requirement",
    "short positions and most derivatives",
    "positions below the reporting threshold",
    "the identity of which manager placed the order",
    "any trading that opened and closed within the period",
)

INSTITUTIONAL_HOLDINGS_COVERAGE: tuple[str, ...] = (
    "US-listed long equity positions above the reporting threshold, as at the period end",
)


class DisclosureScope(BaseModel):
    """One source, its dates, and the honest limits of what it shows."""

    model_config = ConfigDict(extra="forbid")

    kind: DisclosureKind
    entity: str = Field(min_length=1, description="Whose filing this is, not whose decision")

    period_start: date
    period_end: date
    filed_at: date = Field(description="When this became knowable outside the entity")

    covers: tuple[str, ...] = ()
    omits: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _dates_are_ordered(self) -> DisclosureScope:
        if self.period_start > self.period_end:
            raise ValueError(
                f"{self.entity}: period starts {self.period_start}, after its end {self.period_end}"
            )
        if self.filed_at < self.period_end:
            raise ValueError(
                f"{self.entity}: filed {self.filed_at}, before the {self.period_end} period it "
                "reports — a filing cannot be knowable before the period it describes has ended"
            )
        return self

    @property
    def reporting_delay_days(self) -> int:
        """Days between the position being real and anyone outside being able to see it."""
        return (self.filed_at - self.period_end).days

    @property
    def decision_window_days(self) -> int:
        """How wide the uncertainty about *when* the decision happened actually is."""
        return (self.period_end - self.period_start).days

    def describe_limits(self) -> str:
        """One paragraph a report can show beside any conclusion drawn from this source."""
        omitted = "; ".join(self.omits) if self.omits else "nothing recorded"
        return (
            f"{self.entity} {self.kind.value.replace('_', ' ')} for the period ending "
            f"{self.period_end}, knowable from {self.filed_at} "
            f"({self.reporting_delay_days} days later). Does not show: {omitted}."
        )

    @classmethod
    def institutional(
        cls, entity: str, *, period_start: date, period_end: date, filed_at: date
    ) -> DisclosureScope:
        """A holdings filing, with its standard coverage and omissions already attached."""
        return cls(
            kind=DisclosureKind.institutional_holdings,
            entity=entity,
            period_start=period_start,
            period_end=period_end,
            filed_at=filed_at,
            covers=INSTITUTIONAL_HOLDINGS_COVERAGE,
            omits=INSTITUTIONAL_HOLDINGS_OMISSIONS,
        )
