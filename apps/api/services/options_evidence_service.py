"""Predeclared option-signal comparisons on one complete-case research cohort."""

import json
import os
import re
import uuid
from hashlib import sha256

import numpy as np
import pandas as pd
from scipy.special import ndtr

from alphalab.dataio.catalog import DataCatalog
from alphalab.dataio.errors import MissingDataError
from alphalab.options.literature import FEATURES
from alphalab.options.research import _hac_mean
from apps.api.services import options_research_service as research

DEFAULT_FIELDS = [
    "option_atm_iv_30",
    "option_rr25_30",
    "option_bf25_30",
    "option_smirk",
    "option_term_slope",
    "option_q_skew_30",
    "option_vrp_hf_30",
]
VERSION = "controlled-option-evidence-v2"


def _directory(root=None):
    return DataCatalog(root).root / "research" / "option_suites"


def read_suite(suite_id, root=None):
    if not re.fullmatch(r"[0-9a-f]{20}", suite_id):
        raise ValueError("Invalid option evidence suite identifier")
    path = _directory(root) / f"{suite_id}.json"
    if not path.is_file():
        raise MissingDataError("Run the controlled option evidence study first")
    return json.loads(path.read_text(encoding="utf-8"))


def suites(root=None):
    rows = []
    for path in sorted(
        _directory(root).glob("*.json"), key=lambda p: p.stat().st_mtime_ns, reverse=True
    )[:20]:
        report = read_suite(path.stem, root)
        if report.get("version") != VERSION:
            continue
        rows.append(
            {key: report[key] for key in ("id", "scope", "config", "created_at", "models", "tests")}
        )
    return {"suites": rows}


def loss_test(predictions, horizon):
    """Equal-date, cross-asset mean paired squared-error difference with HAC SE.

    Positive loss improvement favors the option model over controls. Reported
    normal-reference p-values are descriptive asymptotic diagnostics, not alpha.
    """
    data = pd.DataFrame(predictions).dropna(
        subset=["target_return", "prediction", "baseline_prediction"]
    )
    improvement = (data.target_return - data.baseline_prediction) ** 2 - (
        data.target_return - data.prediction
    ) ** 2
    dated = improvement.groupby(data.date).mean().sort_index()
    result = _hac_mean(dated.to_numpy(), horizon)
    t = result["hac_t"]
    return {
        **result,
        "p_two_sided_normal": float(2 * ndtr(-abs(t))) if t is not None else None,
        "weighting": "equal_date_mean_cross_asset_squared_error_difference",
    }


def run_suite(request, root=None):
    fields = request["fields"]
    if (
        not 1 <= len(fields) <= 8
        or len(set(fields)) != len(fields)
        or not set(fields).issubset(FEATURES)
    ):
        raise ValueError("Select 1–8 distinct registered option predictors")
    if not request.get("controls", True):
        raise ValueError("Controlled evidence requires RV20 and momentum controls")
    if not request.get("holdout_start"):
        raise ValueError("Controlled evidence requires an explicit reserved-period start")
    prepared = research._research_inputs(request, root)
    features = prepared[0]
    common_fields = list(dict.fromkeys([*fields, "option_rv20", "option_momentum20"]))
    finite = features[common_fields].replace([np.inf, -np.inf], np.nan)
    common = finite.notna().all(axis=1)
    if not common.any():
        raise MissingDataError("No common complete observations for the selected predictors")
    dates = sorted(pd.to_datetime(features.loc[common, "date"]).unique())
    reserved = int(np.searchsorted(dates, np.datetime64(request["holdout_start"])))
    if reserved < request["min_train"] + 20 + request["embargo"] + 1 or len(dates) - reserved < 6:
        raise ValueError("Holdout requires sufficient common training history and six reserved sessions")
    inputs = (
        pd.util.hash_pandas_object(prepared[0], index=False).to_numpy().tobytes()
        + pd.util.hash_pandas_object(prepared[1], index=False).to_numpy().tobytes()
    )
    suite_id = sha256(
        VERSION.encode() + json.dumps(request, sort_keys=True).encode() + inputs
    ).hexdigest()[:20]
    path = _directory(root) / f"{suite_id}.json"
    if path.is_file():
        return read_suite(suite_id, root)
    variants = [(field, [field]) for field in fields]
    if len(fields) > 1:
        variants.append(("joint", fields))
    rows = []
    for horizon in (1, 5, 20):
        cohort = None
        for variant, selected in variants:
            report = research.run_research(
                {**request, "fields": selected, "horizon": horizon},
                root,
                prepared=prepared,
                common_fields=common_fields,
            )
            keys = [
                (p["date"], p["symbol"], p["target_return"], p["baseline_prediction"])
                for p in report["predictions"]
            ]
            if cohort is None:
                cohort = keys
            elif keys != cohort:
                raise ValueError(
                    "Controlled evidence models do not share the same observations and baseline"
                )
            rows.append(
                {
                    "variant": variant,
                    "fields": selected,
                    "horizon": horizon,
                    "run_id": report["id"],
                    "rank_ic": report["rank_ic"],
                    "oos_r2_vs_controls": report["oos_r2_vs_controls"],
                    "coverage": report["coverage"],
                    "independent_holdout": report["independent_holdout"],
                    "loss_test": loss_test(report["predictions"], horizon),
                }
            )
    for row in rows:
        p = row["loss_test"]["p_two_sided_normal"]
        row["loss_test"]["p_bonferroni"] = min(1.0, p * len(rows)) if p is not None else None
        holdout = row["independent_holdout"]
        row["verdict"] = (
            "no_incremental_oos_gain"
            if row["oos_r2_vs_controls"] is None or row["oos_r2_vs_controls"] <= 0
            else "holdout_not_confirmed"
            if not holdout
            or holdout["oos_r2_vs_controls"] is None
            or holdout["oos_r2_vs_controls"] <= 0
            else "weak_statistical_support"
            if row["loss_test"]["p_bonferroni"] is None
            or row["loss_test"]["p_bonferroni"] > 0.05
            or row["loss_test"]["mean"] <= 0
            else "descriptive_candidate_requires_validation"
        )
    result = {
        "id": suite_id,
        "version": VERSION,
        "created_at": pd.Timestamp.now(tz="Asia/Shanghai").isoformat(),
        "scope": {"start": request["start"], "end": request["end"], "underlyings": prepared[2]},
        "config": {
            key: request.get(key)
            for key in ("fields", "min_train", "refit_every", "ridge", "embargo", "holdout_start")
        },
        "models": len(variants),
        "tests": len(rows),
        "common_rows": int(common.sum()),
        "input_rows": len(features),
        "feature_coverage": [
            {"field": field, "valid": int(finite[field].notna().sum()), "total": len(features)}
            for field in fields
        ],
        "rows": rows,
        "method": "Same complete-case date/ETF cohort within each horizon, same baseline and training schedule; no tuning by test returns.",
        "limitations": [
            "historical_fixed_split_not_prospective_blind_test",
            "correlated_etfs_and_finite_strike_proxies",
            "normal_reference_hac_loss_test_with_family_bonferroni_is_descriptive",
            "no_option_contract_execution_in_etf_prediction_tests",
        ],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f".{uuid.uuid4().hex}.tmp")
    temporary.write_text(json.dumps(result, ensure_ascii=False, allow_nan=False), encoding="utf-8")
    os.replace(temporary, path)
    return result
