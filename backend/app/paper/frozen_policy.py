"""The local "distilled investor" path: a frozen policy artifact, read and applied by code.

No model runs here. The artifact carries thresholds with provenance, and this module turns a
position's weight into a stance by comparing it against them. That is the whole mechanism, and
its transparency is the feature — every stance can be traced to one number in one file, and the
file says who inferred that number and from what.

**What this is not.** It is not distillation, and it is not inference. The artifact was written
by a person; loading JSON is not a research pipeline, and a rule table is not a model. The
loader refuses artifacts claiming `provenance: "direct"` on a parameter that no cited source
actually published, because "Berkshire states a 25% cap" is a claim about the world and it is
false. See `_contamination` in the artifact for the stronger caveat about what any result here
can and cannot show.

The thresholds drive `compute_scenario` as well as the stances. That is the point of routing a
persona's numbers into the deterministic engine rather than into a prompt: two investors with
different caps produce differently-sized, individually-costed action sets over the same
holdings, instead of two paragraphs that disagree in prose.
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from app.distillation.finance_nuwa.prediction import BehavioralAction, ReasonCode
from app.domain.policy import (
    PolicyParameter,
    PolicyParameterName,
    PolicyProfile,
    PolicyScope,
    Provenance,
)
from app.domain.portfolio import Portfolio
from app.domain.profile import FinancialProfile
from app.paper.provider import InvestorStance, InvestorView
from app.policy import concentration

DEFAULT_POLICY_PATH = "config/paper/berkshire_public_equity.policy.json"


class StanceRules(BaseModel):
    """Weight thresholds, expressed as multiples of the cap so they cannot drift apart."""

    model_config = ConfigDict(extra="ignore")

    exit_below_weight: float = Field(default=0.005, ge=0.0, le=1.0)
    reduce_above_cap_multiple: float = Field(default=1.0, gt=0.0)
    increase_below_cap_multiple: float = Field(default=0.25, gt=0.0)
    abstain_when_price_unknown: bool = True


class FrozenPolicyArtifact(BaseModel):
    """The parsed file. `extra="ignore"` so the `_`-prefixed commentary travels without a schema."""

    model_config = ConfigDict(extra="ignore")

    policy_id: str = Field(min_length=1)
    display_name: str = Field(min_length=1)
    schema_version: str = Field(min_length=1)

    parameters: dict[str, dict] = Field(default_factory=dict)
    scopes: list[str] = Field(default_factory=list)
    allows_concentration_on_conviction: bool = False
    stance_rules: StanceRules = Field(default_factory=StanceRules)

    def to_policy_profile(self) -> PolicyProfile:
        parameters: dict[PolicyParameterName, PolicyParameter] = {}
        for raw_name, body in self.parameters.items():
            try:
                name = PolicyParameterName(raw_name)
            except ValueError as exc:
                # Raise rather than skip. Dropping an unrecognised name would leave the engine
                # running on house defaults under this investor's display name — a result
                # labelled with their thresholds and computed without them, which is the exact
                # failure `load_artifact` refuses to allow for a missing file.
                raise ValueError(
                    f"unknown policy parameter {raw_name!r} in artifact {self.policy_id!r}; "
                    f"known names are {sorted(p.value for p in PolicyParameterName)}"
                ) from exc
            parameters[name] = PolicyParameter.model_validate({**body, "name": name})

        scopes = []
        for raw_scope in self.scopes:
            try:
                scopes.append(PolicyScope(raw_scope))
            except ValueError as exc:
                raise ValueError(
                    f"unknown policy scope {raw_scope!r} in artifact {self.policy_id!r}; "
                    f"known scopes are {sorted(s.value for s in PolicyScope)}"
                ) from exc

        return PolicyProfile(
            parameters=parameters,
            scopes=scopes or [PolicyScope.concentration],
            allows_concentration_on_conviction=self.allows_concentration_on_conviction,
        )


def load_artifact(path: str | Path = DEFAULT_POLICY_PATH) -> FrozenPolicyArtifact:
    """Read and validate one policy artifact.

    Raises rather than falling back to defaults. A harness that quietly ran on house numbers
    because it could not find the investor's file would produce a result labelled with that
    investor's name and none of their thresholds.
    """
    resolved = Path(path)
    if not resolved.is_absolute():
        resolved = _repo_root() / resolved
    if not resolved.exists():
        raise FileNotFoundError(f"no frozen policy artifact at {resolved}")

    artifact = FrozenPolicyArtifact.model_validate(json.loads(resolved.read_text()))
    _refuse_unsupported_direct_claims(artifact, resolved)
    return artifact


def _refuse_unsupported_direct_claims(artifact: FrozenPolicyArtifact, path: Path) -> None:
    """`direct` means the subject published this number. Claiming it without a source is a lie.

    Checked at load rather than at author time because the artifact is data: it can be edited by
    anyone, including later, including by someone who did not read this docstring.
    """
    for name, body in artifact.parameters.items():
        if body.get("provenance") != Provenance.direct.value:
            continue
        if not body.get("source_labels"):
            raise ValueError(
                f"{path}: parameter {name!r} claims provenance 'direct' — that the subject "
                "published this number — but cites no source. Use 'derived' for a number read "
                "from behaviour."
            )


def _repo_root() -> Path:
    # backend/app/paper/frozen_policy.py -> repo root is four parents up.
    return Path(__file__).resolve().parents[3]


class FrozenPolicyProvider(BaseModel):
    """Applies a frozen artifact's thresholds to the current portfolio. Deterministic, no I/O
    beyond reading the artifact once."""

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    artifact: FrozenPolicyArtifact
    house_cap: float = Field(
        default=concentration.HOUSE_SINGLE_NAME_CAP,
        description="Used only when the artifact carries no single-name cap of its own.",
    )

    @classmethod
    def from_path(cls, path: str | Path = DEFAULT_POLICY_PATH) -> FrozenPolicyProvider:
        return cls(artifact=load_artifact(path))

    @property
    def provider_id(self) -> str:
        return self.artifact.policy_id

    @property
    def display_name(self) -> str:
        return self.artifact.display_name

    def decide(self, profile: FinancialProfile, portfolio: Portfolio) -> InvestorView:
        policy = self.artifact.to_policy_profile()
        cap = policy.resolve(PolicyParameterName.single_name_concentration, self.house_cap)
        rules = self.artifact.stance_rules

        total = portfolio.total_value
        weights: dict[str, float] = {}
        for holding in portfolio.holdings:
            symbol = holding.symbol.strip().upper()
            if not symbol:
                continue
            weights[symbol] = weights.get(symbol, 0.0) + holding.market_value

        stances: list[InvestorStance] = []
        for symbol in sorted(weights):
            stances.append(self._stance(symbol, weights[symbol], total, cap.value, rules))

        return InvestorView(
            provider_id=self.provider_id,
            display_name=self.display_name,
            stances=stances,
            policy=policy,
            # A rule table is not a language model. See the module docstring.
            is_language_model=False,
            determinism_key=f"artifact={self.artifact.policy_id}@{self.artifact.schema_version}",
        )

    def _stance(
        self,
        symbol: str,
        value: float,
        total: float,
        cap: float,
        rules: StanceRules,
    ) -> InvestorStance:
        if total <= 0:
            return InvestorStance(
                symbol=symbol,
                abstain=True,
                note="portfolio has no value to compute a weight against",
            )

        weight = value / total

        if weight <= rules.exit_below_weight:
            return InvestorStance(
                symbol=symbol,
                action=BehavioralAction.exit,
                confidence=0.5,
                reason_codes=[ReasonCode.capital_allocation],
                note=(
                    f"{weight:.2%} of the book is too small to matter to a concentrated "
                    "portfolio; holding it costs attention rather than money"
                ),
            )

        if weight > cap * rules.reduce_above_cap_multiple:
            return InvestorStance(
                symbol=symbol,
                action=BehavioralAction.reduce,
                confidence=0.6,
                reason_codes=[ReasonCode.concentration_tolerance],
                note=f"{weight:.2%} exceeds the {cap:.0%} single-name threshold in this policy",
            )

        if weight < cap * rules.increase_below_cap_multiple:
            return InvestorStance(
                symbol=symbol,
                action=BehavioralAction.increase,
                confidence=0.4,
                reason_codes=[ReasonCode.conviction_scaling],
                note=(
                    f"{weight:.2%} is far below the {cap:.0%} threshold this policy tolerates; "
                    "a position worth holding is worth holding meaningfully"
                ),
            )

        return InvestorStance(
            symbol=symbol,
            action=BehavioralAction.hold,
            confidence=0.55,
            reason_codes=[ReasonCode.long_holding_horizon],
            note=f"{weight:.2%} sits inside the band this policy leaves alone",
        )
