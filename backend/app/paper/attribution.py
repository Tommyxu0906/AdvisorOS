"""Who wanted each action, and who changed its size.

`ProposedAction.proposed_by` already records an author. What it cannot record is the second
fact, and the second fact is the one that gets misreported: when an investor stance produces a
trade that the deterministic engine then caps, the trade was *authored* by the investor and
*sized* by the engine, and collapsing that into one label makes the investor look like they
computed a number they never proposed.

So attribution is a pair, kept beside the action rather than inside it:

    proposed_by     who wanted this to happen
    constrained_by  who changed or refused it, or None

Held in the paper package rather than added to `domain/action.py` because `ProposedAction` is
shared with the committee and the API, and a field only the replay engine writes would be a
field every other consumer has to learn to ignore.

`ActionOrigin` is the coarse bucket used for counting and for the comparison report:

    HOUSE            a blocking guardrail — debt, liquidity — that no policy may override
    DECISION_ENGINE  computed from a threshold: concentration trims and their sizing
    INVESTOR_POLICY  a directional stance from an InvestorDecisionProvider
    COMPOSED         wanted by the investor, resized or bounded by the engine
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

from app.domain.action import ActionSet, ProposedAction

HOUSE_AUTHOR = "house"
ENGINE_AUTHOR = "policy"


class ActionOrigin(str, Enum):
    house = "HOUSE"
    decision_engine = "DECISION_ENGINE"
    investor_policy = "INVESTOR_POLICY"
    composed = "COMPOSED"


class ActionAttribution(BaseModel):
    """One action's authorship, kept separate from its arithmetic."""

    model_config = ConfigDict(extra="forbid")

    action_id: str = Field(min_length=1)
    origin: ActionOrigin
    proposed_by: str = Field(min_length=1)
    constrained_by: str | None = None
    note: str = ""

    @property
    def was_constrained(self) -> bool:
        return self.constrained_by is not None


class RefusedAction(BaseModel):
    """Something a provider wanted that never became an action, and the reason.

    Refusals are first-class output. A run where the investor layer proposed eleven trades and
    the engine allowed two is a finding about that layer, and it is invisible if the refusals are
    dropped on the floor.
    """

    model_config = ConfigDict(extra="forbid")

    symbol: str
    proposed_by: str
    refused_by: str
    reason: str


class AttributionSet(BaseModel):
    """Attribution for one round's action set, plus what was refused."""

    model_config = ConfigDict(extra="forbid")

    attributions: list[ActionAttribution] = Field(default_factory=list)
    refused: list[RefusedAction] = Field(default_factory=list)

    def for_action(self, action_id: str) -> ActionAttribution | None:
        for attribution in self.attributions:
            if attribution.action_id == action_id:
                return attribution
        return None

    def by_origin(self, origin: ActionOrigin) -> list[ActionAttribution]:
        return [a for a in self.attributions if a.origin is origin]

    @property
    def investor_originated(self) -> list[ActionAttribution]:
        """Anything the investor layer wanted, whether or not the engine resized it."""
        return [
            a
            for a in self.attributions
            if a.origin in (ActionOrigin.investor_policy, ActionOrigin.composed)
        ]

    @property
    def counts(self) -> dict[str, int]:
        return {origin.value: len(self.by_origin(origin)) for origin in ActionOrigin}


def classify(action: ProposedAction, investor_provider_id: str) -> ActionOrigin:
    """Bucket one action by who its `proposed_by` names.

    The engine writes `proposed_by` as the advisor id it was handed, so a scenario computed with
    an investor's thresholds carries that investor's id on its concentration trims. Those are
    still DECISION_ENGINE actions — the investor supplied a threshold, not a trade — which is why
    this checks the action id prefix the harness uses for stance-derived actions rather than
    trusting the author string alone.
    """
    if action.proposed_by == HOUSE_AUTHOR:
        return ActionOrigin.house
    if action.action_id.startswith("stance-"):
        return ActionOrigin.investor_policy
    if action.proposed_by == investor_provider_id and investor_provider_id != ENGINE_AUTHOR:
        # A threshold from the investor, arithmetic from the engine.
        return ActionOrigin.decision_engine
    return ActionOrigin.decision_engine


def attribute(
    action_set: ActionSet,
    investor_provider_id: str,
    *,
    engine_display_name: str = "decision engine",
) -> list[ActionAttribution]:
    out: list[ActionAttribution] = []
    for action in action_set.actions:
        origin = classify(action, investor_provider_id)
        constrained_by = None
        note = ""

        if origin is ActionOrigin.investor_policy:
            # Sized against the post-trim book and bounded by the concentration ceiling, so the
            # engine has already had a say in every stance-derived action that survives.
            constrained_by = engine_display_name
            origin = ActionOrigin.composed
            note = "wanted by the investor policy, sized within the engine's ceiling"
        elif origin is ActionOrigin.decision_engine:
            note = "computed from a threshold, not from a directional view"

        out.append(
            ActionAttribution(
                action_id=action.action_id,
                origin=origin,
                proposed_by=action.proposed_by,
                constrained_by=constrained_by,
                note=note,
            )
        )
    return out
