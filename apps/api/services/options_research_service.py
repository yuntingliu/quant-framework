"""Persisted option experiments and canonical SDK project creation."""

import json
import os
import re
import uuid
from functools import lru_cache

import numpy as np
import pandas as pd

from alphalab.dataio.catalog import DataCatalog
from alphalab.dataio.errors import MissingDataError
from alphalab.dataio.runtime import RuntimeStore
from alphalab.dataio.symbols import to_framework_symbol, to_rq_symbol
from alphalab.options import literature_catalog, research_panel, walk_forward_research
from alphalab.options.literature import FEATURES
from apps.api.services import options_service


def _report_path(run_id, root=None):
    if not re.fullmatch(r"[0-9a-f]{20}", run_id):
        raise ValueError("Invalid option research identifier")
    return DataCatalog(root).root / "research" / "option_runs" / (run_id + ".json")


def read_research(run_id, root=None):
    path = _report_path(run_id, root)
    if not path.is_file():
        raise MissingDataError("Option research result is not available")
    return json.loads(path.read_text(encoding="utf-8"))


@lru_cache(maxsize=256)
def _summary(path, size, mtime):
    report = json.loads(path.read_text(encoding="utf-8"))
    return {key: report[key] for key in ("id", "scope", "config", "coverage", "rank_ic", "oos_r2_vs_controls")}


def research_catalog(root=None):
    catalog = DataCatalog(root)
    status = catalog.status("research.option_features")
    coverage = []
    if status["status"] == "ready":
        frame = RuntimeStore(root).read("research.option_features")
        for field, spec in FEATURES.items():
            coverage.append(
                {"field": field, "valid": int(frame[field].notna().sum()), "total": len(frame)}
            )
    runs = []
    directory = catalog.root / "research" / "option_runs"
    for path in sorted(directory.glob("*.json"), key=lambda p: p.stat().st_mtime_ns, reverse=True)[
        :100
    ]:
        stat = path.stat()
        runs.append(_summary(path, stat.st_size, stat.st_mtime_ns))
    return {
        **literature_catalog(),
        "data": {
            key: value
            for key, value in status.items()
            if key in ("status", "date_start", "date_end", "rows")
        },
        "coverage": coverage,
        "runs": runs,
    }


def feature_history(symbol, start, end, root=None):
    symbol = to_rq_symbol(symbol)
    if symbol not in options_service.ETF_NAMES or pd.Timestamp(start) > pd.Timestamp(end):
        raise ValueError("Invalid option feature history scope")
    frame = RuntimeStore(root).read("research.option_features")
    frame = (
        frame.loc[
            frame.symbol.eq(symbol)
            & pd.to_datetime(frame.date).between(pd.Timestamp(start), pd.Timestamp(end))
        ]
        .sort_values("date")
        .tail(2500)
    )
    columns = ["date", "symbol", *FEATURES, "issue", "eligible"]
    frame = frame[columns].copy()
    frame["date"] = pd.to_datetime(frame.date).dt.strftime("%Y-%m-%d")
    return {"points": frame.astype(object).where(pd.notna(frame), None).to_dict("records")}


def _research_inputs(request, root=None):
    symbols = list(dict.fromkeys(to_rq_symbol(v) for v in request["underlyings"]))
    if not set(symbols).issubset(options_service.ETF_NAMES):
        raise ValueError("Select supported ETF option underlyings")
    start, end = pd.Timestamp(request["start"]), pd.Timestamp(request["end"])
    if start >= end or (end - start).days > 3660:
        raise ValueError("Research requires an ordered interval of at most ten years")
    store = RuntimeStore(root)
    features = store.read("research.option_features")
    features = features.loc[
        features.symbol.isin(symbols) & pd.to_datetime(features.date).between(start, end)
    ].copy()
    from alphalab.options.features import FORMULA_VERSION
    if not features.empty and (not features.formula_version.eq(FORMULA_VERSION).all()):
        raise ValueError("Option feature version changed; rebuild the requested history before research")
    if features.empty:
        raise MissingDataError("Build dated option features before return research")
    underlying = store.read("rq.option_underlyings")
    bars = store.read("rq.bars")
    bars["symbol"] = bars.symbol.map(to_rq_symbol)
    # The full retained adjusted ETF history supplies one consistent adjustment
    # anchor. Apply its same-day factor to raw opens for executable labels.
    bars = bars[["date", "symbol", "raw_open"]].merge(
        underlying[["date", "symbol", "raw_close", "adjusted_close"]],
        on=["date", "symbol"],
        validate="one_to_one",
    )
    bars = bars.loc[bars.symbol.isin(symbols) & pd.to_datetime(bars.date).le(end)].copy()
    bars["open"] = bars.raw_open * bars.adjusted_close / bars.raw_close
    bars["close"] = bars.adjusted_close
    return features, bars, symbols, start, end


def run_research(request, root=None, *, prepared=None, common_fields=None):
    features, bars, symbols, start, end = prepared if prepared is not None else _research_inputs(request, root)
    if common_fields:
        features = features.replace([np.inf, -np.inf], np.nan).dropna(subset=common_fields).copy()
    store = RuntimeStore(root)
    from alphalab.options.features import FORMULA_VERSION
    settings = {
        key: request[key]
        for key in ("fields", "horizon", "min_train", "refit_every", "ridge", "embargo", "controls")
    }
    settings["holdout_start"] = request.get("holdout_start")
    panel = research_panel(features, bars, horizon=settings["horizon"])
    report = walk_forward_research(panel, **settings)
    report["feature_formula_version"] = FORMULA_VERSION
    report["scope"] = {"start": str(start.date()), "end": str(end.date()), "underlyings": symbols}
    report["literature"] = literature_catalog()["papers"]
    path = _report_path(report["id"], root)
    if path.is_file():
        return read_research(report["id"], root)
    predictions = pd.DataFrame(report["predictions"])
    field = "option_prediction_" + report["id"]
    materialized = predictions[["date", "symbol", "prediction", "training_label_end"]].rename(
        columns={"prediction": "value"}
    )
    materialized["field"] = field
    materialized["run_id"] = report["id"]
    if (
        not pd.to_datetime(materialized.training_label_end)
        .lt(pd.to_datetime(materialized.date))
        .all()
    ):
        raise ValueError("Forecast publication violated the training cutoff")
    store.write("research.option_predictions", materialized)
    report["prediction_field"] = field
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix("." + uuid.uuid4().hex + ".tmp")
    temporary.write_text(json.dumps(report, ensure_ascii=False, allow_nan=False), encoding="utf-8")
    os.replace(temporary, path)
    return report


def project_spec(request, root=None):
    """Create reviewable replacement functions for the single canonical engine."""
    if request.get("prediction_source") == "controls" and not request.get("research_id"):
        raise ValueError("Control rotation requires a saved forecast study")
    if request.get("research_id"):
        report = read_research(request["research_id"], root)
        prediction_field = ("option_prediction_controls_" + report["id"]
                            if request.get("prediction_source") == "controls" else report["prediction_field"])
        weights = {prediction_field: 1.0}
        symbols = report["scope"]["underlyings"]
        start, end = report["folds"][0]["test_start"], report["scope"]["end"]
    else:
        weights = request["weights"]
        if (
            not weights
            or len(weights) > 8
            or not set(weights).issubset(FEATURES)
            or any(not np.isfinite(v) or v == 0 or abs(v) > 100 for v in weights.values())
        ):
            raise ValueError("Select 1–8 registered option predictors with finite nonzero weights")
        symbols = list(dict.fromkeys(to_rq_symbol(v) for v in request["underlyings"]))
        if not symbols or not set(symbols).issubset(options_service.ETF_NAMES):
            raise ValueError("Select supported ETF option underlyings")
        start, end = request["start"], request["end"]
    canonical = [to_framework_symbol(s) for s in symbols]
    factors = [
        {
            "template_id": field if field in FEATURES else report["config"]["fields"][0],
            "factor_id": field,
            "label": FEATURES[field]["en"] if field in FEATURES else "Purged OOS forecast",
            "body": f'return context.daily_factor("{field}")',
        }
        for field in weights
    ]
    # A fixed signed raw blend preserves user-specified directions. No sign is
    # selected using test performance; forecasts already have return units.
    normalization = "raw" if request.get("research_id") else "rank"
    top_n = request["top_n"]
    schedule = (
        "Weekly.last_trading_day"
        if request["frequency"] == "weekly"
        else "Monthly.last_trading_day"
    )
    replacements = [
        {
            "entrypoint_id": "research_universe",
            "function_source": f'''@universe(id="research_universe", label="Option ETF universe")
def research_universe(context):
    """Use only the configured, currently eligible ETF instruments."""
    # context.universe is already bounded by historical listing intervals.
    return UniverseResult(symbols=[s for s in context.universe if s in {canonical!r}])
''',
        },
        {
            "entrypoint_id": "monthly_momentum",
            "function_source": f'''@signal(id="monthly_momentum", label="Option research signal", schedule={schedule}(at="close"))
def monthly_momentum(context, state, *, top_n: int = {top_n}):
    """Rank dated predictors after the close; unavailable values are excluded."""
    # Signed factor weights are explicit hypotheses, not ex-post fitted signs.
    scores = context.combine_factors(weights={weights!r}, normalization="{normalization}").dropna()
    selected = list(scores.nlargest(top_n).index)
    return SignalResult(selected=selected, scores=scores, state=state)
''',
        },
        {
            "entrypoint_id": "equal_weight",
            "function_source": f'''@portfolio(id="equal_weight", label="Option research ETF allocation")
def equal_weight(context, signal, state, *, max_weight: float = {1 / top_n!r}):
    """Allocate equal long-only ETF weights; missing candidates leave cash."""
    # Pricing and execution remain owned by the standard SDK engine.
    chosen = list(signal.selected)
    weight = min(max_weight, 1/len(chosen)) if chosen else 0.0
    return PortfolioDecision(target_weights={{s: weight for s in chosen}}, state=state)
''',
        },
    ]
    # Weekly is imported inside the registered function's decorator expression
    # through the default strategy prelude (the SDK exposes Weekly).
    return {
        "project_id": request["project_id"],
        "name": request["name"],
        "description": "Option literature hypothesis / purged OOS research; validate local ETF evidence and costs.",
        "profile": "runtime",
        "factors": factors,
        "function_replacements": replacements,
        "data_requirements": {"daily_factors": list(weights)},
        "recipe_parameters": {"start": str(start), "end": str(end), "symbols": canonical},
        "settings": {
            "benchmark_symbols": canonical[:1],
            "option_research_id": request.get("research_id"),
            "option_prediction_source": request.get("prediction_source", "model"),
            "option_controlled_suite_id": request.get("controlled_suite_id"),
            "lookback_days": 260,
        },
    }


def create_research_project(request):
    from apps.api.services import data_sync_service, strategy_service

    spec = project_spec(request)
    if request.get("prediction_source") == "controls" and request.get("research_id"):
        report = read_research(request["research_id"])
        frame = pd.DataFrame(report["predictions"])[["date", "symbol", "baseline_prediction", "training_label_end"]]
        frame = frame.rename(columns={"baseline_prediction": "value"})
        if not pd.to_datetime(frame.training_label_end).lt(pd.to_datetime(frame.date)).all():
            raise ValueError("Control forecast publication violated the training cutoff")
        frame["field"] = "option_prediction_controls_" + report["id"]
        frame["run_id"] = report["id"]
        RuntimeStore().write("research.option_predictions", frame)
    project = strategy_service.create_project(spec)
    try:
        # Preserve canonical source save/inspection and compensate a failed recipe
        # write by removing only this newly created project.
        draft = data_sync_service.apply_recipe_template(
            project["id"], "rq.etf_options", expected_source_sha256=None
        )
        data_sync_service.update_recipe_parameters(
            project["id"],
            **spec["recipe_parameters"],
            expected_source_sha256=draft["source_sha256"],
        )
    except Exception:
        strategy_service.delete_project(project["id"])
        raise
    return {"project": project, "spec": spec}
