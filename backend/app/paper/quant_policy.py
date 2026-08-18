"""The empirically benchmarked local path: the frozen FinanceNuwa quant baseline, applied forward.

This is the only v1 provider whose behaviour was measured against anything. The model is refit
from its frozen config — never re-selected — on the refinement split of the frozen dataset, and
its validation macro F1 travels with every view it produces so that nobody has to go looking for
how good it actually is. The answer, for the record, is *barely better than the floor*: the
selected config scores about 0.39 macro F1 against an always-HOLD baseline of roughly 0.21 on
the natural view. That is a real edge and a small one, and a forward run is not evidence of more.

**The honest problem with using it here, stated up front.** The model was trained on
`pit-features-v1` computed from 13F episodes: position weight and rank, quarters held, trailing
and relative returns implied by successive filings, and portfolio-level concentration. A live
household portfolio supplies some of those and not others. Nothing is imputed — the encoding
carries an explicit missing-indicator per feature, which is how the model was trained — but a
row where most price features are missing is a row unlike the ones it learned from, and its
prediction is extrapolation.

So `QuantBehaviorProvider` reports `feature_coverage` on every view, and abstains outright on a
position whose coverage falls below `min_feature_coverage`. Abstention is the correct output for
"I have no basis here"; a confident label computed from four missing indicators is not.

There is no `EpisodeRow` construction in this module, deliberately. Building one would mean
inventing `decision_window_start`, `public_information_cutoff`, `attribution_basis`, and
`replay_view` for a position that is not an episode and has no observed outcome — fabricated
provenance on a row that then looks exactly like a real one. The design matrix is built directly
instead, and `test_paper_quant.py` pins the encoding against `baselines.encode` so the two
cannot drift.
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from app.distillation.finance_nuwa import access, baselines
from app.distillation.finance_nuwa.prediction import BehavioralAction, ReasonCode
from app.domain.policy import PolicyProfile
from app.domain.portfolio import Portfolio
from app.domain.profile import FinancialProfile
from app.paper.provider import InvestorStance, InvestorView

DEFAULT_CONFIG_PATH = "data/berkshire/baselines/model_configs.json"
DEFAULT_DATASET_DIR = "data/berkshire/dataset/berkshire-v2.0-natural"
DEFAULT_CONFIG_KEY = "berkshire-v2.0-natural::boosted_trees"

MODEL_CLASSES = {
    "logistic": baselines.MultinomialLogistic,
    "boosted_trees": baselines.GradientBoostedTrees,
    "class_prior": baselines.ClassPrior,
    "always_hold": baselines.AlwaysHold,
}

# Label vocabulary the frozen models emit, mapped onto the shared decision vocabulary.
_LABEL_TO_ACTION = {
    "hold": BehavioralAction.hold,
    "increase": BehavioralAction.increase,
    "reduce": BehavioralAction.reduce,
    "exit": BehavioralAction.exit,
}


def encode_live(
    feature_rows: list[dict[str, float | int | None]],
    feature_set: str,
    ids: list[str],
) -> baselines.Design:
    """Encode live positions the same way `baselines.encode` encodes frozen episodes.

    Value plus missing-indicator per feature, nothing imputed — the convention is copied rather
    than imported because `encode` takes `EpisodeRow`, and a live position is not one. The label
    column is filled with a placeholder that no model reads at predict time.
    """
    names = baselines.FEATURE_SETS[feature_set]
    columns = [c for name in names for c in (name, f"{name}_missing")]

    matrix: list[list[float]] = []
    for features in feature_rows:
        encoded: list[float] = []
        for name in names:
            value = features.get(name)
            encoded.append(0.0 if value is None else float(value))
            encoded.append(1.0 if value is None else 0.0)
        matrix.append(encoded)

    return baselines.Design(
        columns=columns,
        rows=matrix,
        # Never read by `predict`; present because Design carries labels for the fit path.
        labels=["hold"] * len(matrix),
        episode_ids=ids,
    )


def _levels(returns: list[float]) -> list[float]:
    """Compound a return series into a price path starting at 1.0."""
    path = [1.0]
    for r in returns:
        path.append(path[-1] * (1.0 + r))
    return path


def _return_over(levels: list[float], periods: int) -> float | None:
    """Mirrors `features._return_over`: None rather than a partial window.

    A "four-quarter return" computed over two quarters is a different quantity wearing the same
    name, and the model cannot tell the difference.
    """
    if periods <= 0 or len(levels) <= periods or levels[-1 - periods] <= 0:
        return None
    return levels[-1] / levels[-1 - periods] - 1.0


def _drawdown(levels: list[float]) -> float | None:
    if len(levels) < 2:
        return None
    peak = max(levels)
    return levels[-1] / peak - 1.0 if peak > 0 else None


def live_features(portfolio: Portfolio) -> dict[str, dict[str, float | int | None]]:
    """Compute as much of `pit-features-v1` as this portfolio actually supports.

    Position and portfolio context come from the holdings. Price context comes from
    `Portfolio.price_series` when it is present and is `None` when it is not — never zero. A
    zero trailing return is the claim "this went nowhere", which is a statement about a stock
    rather than an admission that nobody has its history.

    **One honest mismatch.** The frozen model learned `trailing_return_1q` from prices *implied
    by successive 13F filings* — quarter-end value over shares. Here the same field is computed
    from a market return series. They measure the same idea and are not the same measurement: a
    filing-implied price is a quarterly snapshot of a reported book, a bar series is the market.
    The feature is close enough to be worth supplying and not close enough to be silent about.

    `quarters_held` stays `None` throughout. A portfolio has no acquisition dates, and inferring
    a holding age from a price series would be inventing one.
    """
    values: dict[str, float] = {}
    for holding in portfolio.holdings:
        symbol = holding.symbol.strip().upper()
        if not symbol:
            continue
        values[symbol] = values.get(symbol, 0.0) + holding.market_value

    total = sum(values.values())
    ranked = sorted(values, key=lambda s: values[s], reverse=True)

    weights = {s: (values[s] / total if total > 0 else None) for s in values}
    hhi = sum(w * w for w in weights.values() if w is not None) if total > 0 else None
    top5 = sum(sorted((w for w in weights.values() if w is not None), reverse=True)[:5]) or None

    series = {s.symbol.strip().upper(): s for s in portfolio.price_series}

    # The book's own return over four quarters, so a position's relative return nets out a
    # rising tide. Weighted by current value, which is the only weighting available.
    book_4q: float | None = None
    if total > 0:
        contributions = []
        for symbol, value in values.items():
            entry = series.get(symbol)
            if entry is None:
                continue
            per_quarter = max(1, entry.periods_per_year // 4)
            own = _return_over(_levels(entry.returns), per_quarter * 4)
            if own is not None:
                contributions.append((value / total) * own)
        # Only meaningful when most of the book is covered; a "portfolio return" derived from
        # one of five positions is not one.
        covered = sum(values[s] for s in values if s in series) / total
        if contributions and covered >= 0.5:
            book_4q = sum(contributions) / covered

    out: dict[str, dict[str, float | int | None]] = {}
    for symbol in values:
        entry = series.get(symbol)
        r1q = r4q = dd = rel = None
        if entry is not None and entry.returns:
            per_quarter = max(1, entry.periods_per_year // 4)
            levels = _levels(entry.returns)
            r1q = _return_over(levels, per_quarter)
            r4q = _return_over(levels, per_quarter * 4)
            dd = _drawdown(levels)
            if r4q is not None and book_4q is not None:
                rel = r4q - book_4q

        out[symbol] = {
            "weight": weights[symbol],
            "rank": ranked.index(symbol) + 1,
            # No acquisition dates exist on a portfolio. See the docstring.
            "quarters_held": None,
            "trailing_return_1q": r1q,
            "trailing_return_4q": r4q,
            "drawdown_from_peak": dd,
            "relative_return_4q": rel,
            "portfolio_positions": len(values),
            "top5_concentration": top5,
            "hhi": hhi,
        }
    return out


class QuantBehaviorProvider(BaseModel):
    """A refitted frozen baseline, applied to current holdings, with its limits attached."""

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    provider_id: str = "quant_behavior"
    display_name: str = "FinanceNuwa quant baseline (frozen config)"

    config_key: str = DEFAULT_CONFIG_KEY
    config: dict = Field(default_factory=dict)
    config_sha256: str = ""
    validation_macro_f1: float | None = None

    min_feature_coverage: float = Field(
        default=0.3,
        ge=0.0,
        le=1.0,
        description="Below this fraction of non-missing features, the provider abstains.",
    )

    _model: object = None

    @classmethod
    def load(
        cls,
        *,
        config_path: str | Path = DEFAULT_CONFIG_PATH,
        dataset_dir: str | Path = DEFAULT_DATASET_DIR,
        config_key: str = DEFAULT_CONFIG_KEY,
        min_feature_coverage: float = 0.3,
    ) -> QuantBehaviorProvider:
        """Refit the frozen config on the refinement split. No selection happens here.

        Refitting rather than loading pickled weights is what makes the result reproducible from
        the committed artifacts alone — and it keeps model selection where it belongs, in the
        frozen config, rather than letting a forward run quietly pick a better one.
        """
        root = _repo_root()
        configs = json.loads(_resolve(config_path, root).read_text())
        if config_key not in configs:
            raise KeyError(f"{config_key!r} is not in {config_path}; available: {sorted(configs)}")

        entry = configs[config_key]
        config = entry["config"]
        model_name = config["model"]
        if model_name not in MODEL_CLASSES:
            raise KeyError(f"unknown model {model_name!r} in frozen config {config_key!r}")

        train_rows = access.refinement_dataset(_resolve(dataset_dir, root))
        design = baselines.encode(train_rows, config["feature_set"])
        model = MODEL_CLASSES[model_name](**config.get("params", {})).fit(design)

        provider = cls(
            config_key=config_key,
            config=config,
            config_sha256=entry.get("config_sha256", ""),
            validation_macro_f1=entry.get("validation_macro_f1"),
            min_feature_coverage=min_feature_coverage,
        )
        provider._model = model
        return provider

    @property
    def feature_set(self) -> str:
        return self.config.get("feature_set", "position+price")

    def decide(self, profile: FinancialProfile, portfolio: Portfolio) -> InvestorView:
        if self._model is None:
            raise RuntimeError("QuantBehaviorProvider must be constructed via .load()")

        features = live_features(portfolio)
        symbols = sorted(features)
        if not symbols:
            return self._view([])

        names = baselines.FEATURE_SETS[self.feature_set]
        design = encode_live([features[s] for s in symbols], self.feature_set, symbols)
        prediction = self._model.predict(design)

        stances: list[InvestorStance] = []
        for symbol, label, probabilities in zip(
            symbols, prediction.predicted, prediction.probabilities, strict=True
        ):
            present = sum(1 for n in names if features[symbol].get(n) is not None)
            coverage = present / len(names)

            if coverage < self.min_feature_coverage:
                stances.append(
                    InvestorStance(
                        symbol=symbol,
                        abstain=True,
                        note=(
                            f"only {coverage:.0%} of the features this model was trained on are "
                            "available for this position; a label here would be extrapolation"
                        ),
                    )
                )
                continue

            action = _LABEL_TO_ACTION.get(label)
            if action is None:
                stances.append(
                    InvestorStance(
                        symbol=symbol,
                        abstain=True,
                        note=f"model emitted an unmapped label {label!r}",
                    )
                )
                continue

            stances.append(
                InvestorStance(
                    symbol=symbol,
                    action=action,
                    confidence=round(float(probabilities.get(label, 0.0)), 4),
                    reason_codes=[ReasonCode.other],
                    note=(
                        f"{self.config_key} on {coverage:.0%} feature coverage; "
                        f"validation macro F1 {self.validation_macro_f1:.3f} "
                        if self.validation_macro_f1 is not None
                        else f"{self.config_key} on {coverage:.0%} feature coverage"
                    ).strip(),
                )
            )

        return self._view(stances)

    def _view(self, stances: list[InvestorStance]) -> InvestorView:
        return InvestorView(
            provider_id=self.provider_id,
            display_name=self.display_name,
            stances=stances,
            # Statistical, not linguistic. A refitted gradient-boosted tree is not inference in
            # the sense that phrase is usually meant, and this flag keeps that distinction.
            is_language_model=False,
            # Thresholds are not this provider's business: it predicts behaviour, it does not
            # carry a policy. The engine runs on house numbers and says so.
            policy=PolicyProfile(),
            determinism_key=f"config={self.config_key}@{self.config_sha256[:12]}",
        )


def _resolve(path: str | Path, root: Path) -> Path:
    resolved = Path(path)
    return resolved if resolved.is_absolute() else root / resolved


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]
