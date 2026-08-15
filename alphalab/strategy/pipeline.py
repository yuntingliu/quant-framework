"""Typed implementation boundaries for the four-stage strategy pipeline."""
from __future__ import annotations

import inspect
from dataclasses import asdict, dataclass, field
from typing import Any


PIPELINE_STAGE_NAMES = ("signal", "portfolio", "risk", "execution")
PIPELINE_IMPLEMENTATION_KINDS = ("configured", "python")
DEFAULT_STAGE_ENTRYPOINTS = {
    "signal": "generate_signal",
    "portfolio": "construct_portfolio",
    "risk": "apply_risk",
    "execution": "configure_execution",
}


@dataclass(frozen=True)
class PipelineStageSpec:
    """Implementation choice for one stable pipeline boundary."""

    kind: str = "configured"
    entrypoint: str = "generate_signal"
    timeout_seconds: float = 5.0

    def __post_init__(self) -> None:
        kind = str(self.kind).strip().lower()
        entrypoint = str(self.entrypoint).strip()
        timeout_seconds = float(self.timeout_seconds)
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "entrypoint", entrypoint)
        object.__setattr__(self, "timeout_seconds", timeout_seconds)
        if kind not in PIPELINE_IMPLEMENTATION_KINDS:
            raise ValueError(
                f"pipeline stage kind must be one of {PIPELINE_IMPLEMENTATION_KINDS}"
            )
        if not entrypoint.isidentifier() or entrypoint.startswith("_"):
            raise ValueError("pipeline stage entrypoint must be a public Python identifier")
        if not 0.1 <= timeout_seconds <= 30:
            raise ValueError("pipeline stage timeout_seconds must be between 0.1 and 30")


def _default_stage(name: str) -> PipelineStageSpec:
    return PipelineStageSpec(entrypoint=DEFAULT_STAGE_ENTRYPOINTS[name])


@dataclass(frozen=True)
class StrategyPipelineSpec:
    """Canonical signal-to-execution implementation graph."""

    signal: PipelineStageSpec = field(default_factory=lambda: _default_stage("signal"))
    portfolio: PipelineStageSpec = field(default_factory=lambda: _default_stage("portfolio"))
    risk: PipelineStageSpec = field(default_factory=lambda: _default_stage("risk"))
    execution: PipelineStageSpec = field(default_factory=lambda: _default_stage("execution"))

    @classmethod
    def from_dict(
        cls,
        raw: dict[str, Any] | None,
        *,
        legacy_implementation: dict[str, Any] | None = None,
    ) -> "StrategyPipelineSpec":
        """Read the canonical graph, with one narrow persisted-YAML migration.

        The former whole-strategy Python hook returned the constructed portfolio,
        so it maps precisely to the portfolio stage. Newly serialized configs
        never emit the legacy ``implementation`` field.
        """

        if raw is not None and legacy_implementation is not None:
            raise ValueError("strategy config cannot contain both pipeline and implementation")
        if raw is None:
            if not legacy_implementation:
                return cls()
            kind = str(legacy_implementation.get("kind", "configured"))
            if kind == "configured":
                return cls()
            if kind != "python":
                raise ValueError(
                    f"legacy implementation.kind must be one of {PIPELINE_IMPLEMENTATION_KINDS}"
                )
            portfolio = PipelineStageSpec(
                kind="python",
                entrypoint=str(legacy_implementation.get("entrypoint", "generate")),
                timeout_seconds=float(legacy_implementation.get("timeout_seconds", 5.0)),
            )
            return cls(portfolio=portfolio)

        values = dict(raw or {})
        unknown = sorted(set(values) - set(PIPELINE_STAGE_NAMES))
        if unknown:
            raise ValueError(f"unknown pipeline stages: {unknown}")
        stages: dict[str, PipelineStageSpec] = {}
        for name in PIPELINE_STAGE_NAMES:
            stage_raw = dict(values.get(name, {}))
            stage_raw.setdefault("entrypoint", DEFAULT_STAGE_ENTRYPOINTS[name])
            stages[name] = PipelineStageSpec(**stage_raw)
        return cls(**stages)

    def to_dict(self) -> dict[str, dict[str, Any]]:
        return {name: asdict(getattr(self, name)) for name in PIPELINE_STAGE_NAMES}

    @property
    def python_stages(self) -> tuple[str, ...]:
        return tuple(
            name for name in PIPELINE_STAGE_NAMES if getattr(self, name).kind == "python"
        )

    @property
    def uses_python(self) -> bool:
        return bool(self.python_stages)

    @property
    def implementation_summary(self) -> str:
        return "python" if self.uses_python else "configured"


_CONTRACTS = {
    "stock_selection": {
        "signal": {
            "input": "PIT eligible candidates, price histories, configured factor scores",
            "output": "{'scores': {symbol: finite_number}}",
            "hard_gate": "symbols must belong to the point-in-time eligible universe",
        },
        "portfolio": {
            "input": "signal scores, candidates, current weights and construction settings",
            "output": "{'weights': {symbol: non_negative_number}}",
            "hard_gate": "output is passed to the risk stage and core validation",
        },
        "risk": {
            "input": "proposed weights and configured concentration limits",
            "output": "{'weights': {symbol: non_negative_number}}",
            "hard_gate": "core rechecks eligibility, count, per-name cap and total exposure",
        },
        "execution": {
            "input": "target/current weights, signal and entry dates, configured assumptions",
            "output": "{'execution': {supported execution overrides}}",
            "hard_gate": "core applies price, volume, amount, participation, cash and cost gates",
        },
    },
    "market_timing": {
        "signal": {
            "input": "PIT monthly MKT history and configured timing signals",
            "output": "{'score': finite_number_between_0_and_1}",
            "hard_gate": "score must be finite and in [0, 1]",
        },
        "portfolio": {
            "input": "signal score and configured exposure mapping",
            "output": "{'market_exposure': finite_number}",
            "hard_gate": "output is passed to the risk stage and core validation",
        },
        "risk": {
            "input": "proposed exposure and configured exposure limits",
            "output": "{'market_exposure': finite_number}",
            "hard_gate": "core rechecks the configured minimum and maximum exposure",
        },
        "execution": {
            "input": "applied/previous exposure and configured turnover costs",
            "output": "{'execution': {'cost_bps': number, 'slippage_bps': number}}",
            "hard_gate": "core rejects negative or non-finite costs and preserves one-period lag",
        },
    },
}

def pipeline_manifest(
    strategy_type: str,
    pipeline: StrategyPipelineSpec,
) -> dict[str, Any]:
    """Return an inspectable, ordered contract manifest for UI and snapshots."""

    if strategy_type not in _CONTRACTS:
        raise ValueError(f"unsupported strategy_type: {strategy_type}")
    stages = []
    for index, name in enumerate(PIPELINE_STAGE_NAMES, start=1):
        spec = getattr(pipeline, name)
        stages.append(
            {
                "order": index,
                "name": name,
                "kind": spec.kind,
                "entrypoint": spec.entrypoint,
                "runtime_function": (
                    spec.entrypoint
                    if spec.kind == "python"
                    else (
                        f"configured_{'stock' if strategy_type == 'stock_selection' else 'timing'}_{name}"
                    )
                ),
                "timeout_seconds": spec.timeout_seconds,
                "contract": dict(_CONTRACTS[strategy_type][name]),
                "source": (
                    _configured_stage_source(strategy_type, name)
                    if spec.kind == "configured"
                    else (
                        f"def {spec.entrypoint}(context):\n"
                        "    # Exact implementation is stored in the persisted Python module below.\n"
                        "    ..."
                    )
                ),
            }
        )
    return {
        "strategy_type": strategy_type,
        "order": list(PIPELINE_STAGE_NAMES),
        "python_stages": list(pipeline.python_stages),
        "stages": stages,
        "composed_source": _composed_pipeline_source(strategy_type, pipeline),
        "invariants": [
            "all research inputs are bounded by the decision date",
            "custom risk output is revalidated by the core",
            "custom execution settings cannot bypass market-data and cash constraints",
            "signals take effect no earlier than the next return or trading period",
        ],
    }


def _configured_stage_source(strategy_type: str, name: str) -> str:
    from alphalab.strategy import stages

    prefix = "stock" if strategy_type == "stock_selection" else "timing"
    function = getattr(stages, f"configured_{prefix}_{name}")
    return inspect.getsource(function).strip()


def _composed_pipeline_source(
    strategy_type: str,
    pipeline: StrategyPipelineSpec,
) -> str:
    prefix = "stock" if strategy_type == "stock_selection" else "timing"
    entrypoints = {
        name: (
            getattr(pipeline, name).entrypoint
            if getattr(pipeline, name).kind == "python"
            else f"configured_{prefix}_{name}"
        )
        for name in PIPELINE_STAGE_NAMES
    }
    if strategy_type == "stock_selection":
        return (
            "def run_pipeline(context):\n"
            f"    signal = {entrypoints['signal']}(context)\n"
            f"    portfolio = {entrypoints['portfolio']}({{**context, 'signal_scores': signal['scores']}})\n"
            f"    risk = {entrypoints['risk']}({{**context, 'proposed_weights': portfolio['weights']}})\n"
            "    core_validate_weights(risk['weights'])\n"
            f"    execution = {entrypoints['execution']}({{**context, 'target_weights': risk['weights']}})\n"
            "    return core_execute_next_session(risk['weights'], execution['execution'])"
        )
    return (
        "def run_pipeline(context):\n"
        f"    signal = {entrypoints['signal']}(context)\n"
        f"    portfolio = {entrypoints['portfolio']}({{**context, 'signal_score': signal['score']}})\n"
        f"    risk = {entrypoints['risk']}({{**context, 'proposed_exposure': portfolio['market_exposure']}})\n"
        "    core_validate_exposure(risk['market_exposure'])\n"
        f"    execution = {entrypoints['execution']}({{**context, 'applied_exposure': risk['market_exposure']}})\n"
        "    return core_apply_next_period(risk['market_exposure'], execution['execution'])"
    )


def validate_pipeline_python_source(
    pipeline: StrategyPipelineSpec,
    source: str,
) -> dict[str, Any]:
    """Validate the shared source module against every enabled Python stage."""

    from alphalab.strategy.python_runtime import validate_python_source

    if not pipeline.uses_python:
        raise ValueError("pipeline has no Python stages")
    checks = [
        validate_python_source(source, getattr(pipeline, name).entrypoint)
        for name in pipeline.python_stages
    ]
    first = checks[0]
    return {
        "valid": True,
        "bytes": first["bytes"],
        "sha256": first["sha256"],
        "trusted_local_code": True,
        "stages": [
            {
                "name": name,
                "entrypoint": getattr(pipeline, name).entrypoint,
            }
            for name in pipeline.python_stages
        ],
    }


__all__ = [
    "DEFAULT_STAGE_ENTRYPOINTS",
    "PIPELINE_IMPLEMENTATION_KINDS",
    "PIPELINE_STAGE_NAMES",
    "PipelineStageSpec",
    "StrategyPipelineSpec",
    "pipeline_manifest",
    "validate_pipeline_python_source",
]
