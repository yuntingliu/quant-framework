"""Purged expanding-window return research; trading stays in the SDK engine."""

import json
from hashlib import sha256

import numpy as np
import pandas as pd

from .literature import FEATURES

RESEARCH_VERSION = "option-walk-forward-v2-holdout"


def research_panel(features, bars, *, horizon=5, sessions=None):
    """Close-t predictors, adjusted next-open to horizon-close return labels."""
    if horizon not in (1, 5, 20):
        raise ValueError("Return horizon must be 1, 5 or 20 trading sessions")
    if features.duplicated(["date", "symbol"]).any() or bars.duplicated(["date", "symbol"]).any():
        raise ValueError("Research data contains duplicate date/symbol keys")
    # Reindex before shifting: a missing quote must not turn a later session
    # into tomorrow's open. Feature dates also expose gaps in a single asset.
    calendar = (
        pd.DatetimeIndex(
            pd.to_datetime(
                sessions if sessions is not None else pd.concat([features.date, bars.date]).unique()
            )
        )
        .sort_values()
        .unique()
    )
    labels = []
    for symbol, group in bars.groupby("symbol"):
        group = group.sort_values("date").copy()
        for column in ("open", "close"):
            if (
                column not in group
                or not np.isfinite(group[column]).all()
                or group[column].le(0).any()
            ):
                raise ValueError(
                    "Research labels require finite positive adjusted open/close prices"
                )
        observed = pd.to_datetime(group.date)
        grid = calendar[(calendar >= observed.min()) & (calendar <= calendar.max())]
        group = group.assign(date=observed).set_index("date").reindex(grid)
        group["symbol"] = symbol
        group["date"] = group.index
        # Every close within the forecast holding interval is required.
        complete = (
            group.close.notna()
            .rolling(horizon, min_periods=horizon)
            .sum()
            .shift(-horizon)
            .eq(horizon)
        )
        group["label_start"] = group.date.shift(-1)
        group["label_end"] = group.date.shift(-horizon)
        group["target_return"] = group.close.shift(-horizon) / group.open.shift(-1) - 1
        group.loc[~complete, "target_return"] = np.nan
        group = group.reset_index(drop=True)
        labels.append(group[["date", "symbol", "label_start", "label_end", "target_return"]])
    if not labels:
        raise ValueError("No market history for return labels")
    return features.merge(
        pd.concat(labels), on=["date", "symbol"], how="left", validate="one_to_one"
    )


def _hac_mean(values, lags):
    values = np.asarray(values, dtype=float)
    values = values[np.isfinite(values)]
    n = len(values)
    if n < 3:
        return {"mean": None if not n else float(values.mean()), "hac_t": None, "observations": n}
    residual = values - values.mean()
    omega = float(residual @ residual) / n
    for lag in range(1, min(lags, n - 1) + 1):
        omega += 2 * (1 - lag / (lags + 1)) * float(residual[lag:] @ residual[:-lag]) / n
    se = np.sqrt(max(0, omega) / n)
    return {
        "mean": float(values.mean()),
        "hac_t": float(values.mean() / se) if se > 1e-12 else None,
        "observations": n,
    }


def _design(train, test, fields, symbols):
    mean, scale = train[fields].mean(), train[fields].std(ddof=0).replace(0, 1)

    def matrix(frame):
        scaled = ((frame[fields] - mean) / scale).to_numpy(dtype=float)
        fixed = (
            np.column_stack([frame.symbol.eq(symbol).to_numpy(float) for symbol in symbols[1:]])
            if len(symbols) > 1
            else np.empty((len(frame), 0))
        )
        return np.column_stack([np.ones(len(frame)), scaled, fixed])

    return matrix(train), matrix(test)


def _ridge_predict(x, y, test, ridge, feature_count):
    penalty = np.zeros(x.shape[1])
    penalty[1 : feature_count + 1] = ridge
    coefficient = np.linalg.pinv(x.T @ x + np.diag(penalty)) @ x.T @ y
    return test @ coefficient


def _training_inference(train, fields, symbols, horizon):
    x, _ = _design(train, train, fields, symbols)
    y = train.target_return.to_numpy(float)
    beta = np.linalg.pinv(x) @ y
    scores = x * (y - x @ beta)[:, None]
    # Cluster cross-assets within date, then Newey-West across ordered dates.
    clustered = pd.DataFrame(scores).groupby(train.date.to_numpy()).sum().to_numpy()
    meat = clustered.T @ clustered
    for lag in range(1, min(horizon, len(clustered) - 1) + 1):
        cross = clustered[lag:].T @ clustered[:-lag]
        meat += (1 - lag / (horizon + 1)) * (cross + cross.T)
    bread = np.linalg.pinv(x.T @ x)
    se = np.sqrt(np.maximum(0, np.diag(bread @ meat @ bread)))
    return [
        {
            "field": field,
            "coefficient_per_train_sd": float(beta[i + 1]),
            "hac_t": float(beta[i + 1] / se[i + 1]) if se[i + 1] > 1e-12 else None,
        }
        for i, field in enumerate(fields)
    ]


def walk_forward_research(
    panel,
    *,
    fields,
    horizon=5,
    min_train=120,
    refit_every=20,
    ridge=1.0,
    embargo=1,
    controls=True,
    holdout_start=None,
):
    """Fit using labels completed before each test block; retain live unscored rows.

    Ridge strength and feature selection are caller-specified, never tuned using
    the reported test sample. No hypothetical equity curve substitutes for the
    canonical next-open execution engine.
    """
    if (
        not fields
        or len(fields) > 8
        or len(set(fields)) != len(fields)
        or not set(fields).issubset(FEATURES)
    ):
        raise ValueError("Select 1–8 distinct registered option predictors")
    if (
        horizon not in (1, 5, 20)
        or not 60 <= min_train <= 2000
        or not 1 <= refit_every <= 60
        or not 0 <= embargo <= 20
        or not np.isfinite(ridge)
        or not 0 <= ridge <= 1000
    ):
        raise ValueError("Invalid bounded research configuration")
    controls_fields = ["option_rv20", "option_momentum20"] if controls else []
    all_fields = list(dict.fromkeys([*fields, *controls_fields]))
    data = panel.copy()
    for column in ("date", "label_start", "label_end"):
        data[column] = pd.to_datetime(data[column])
    if data.duplicated(["date", "symbol"]).any():
        raise ValueError("Duplicate research observations")
    data[all_fields] = data[all_fields].replace([np.inf, -np.inf], np.nan)
    total = len(data)
    data = data.dropna(subset=all_fields).sort_values(["date", "symbol"]).reset_index(drop=True)
    dates = sorted(panel.date.unique())
    dates = [pd.Timestamp(v) for v in dates]
    symbols = sorted(data.symbol.unique())
    predictions, folds, last_train = [], [], None
    holdout_index = (
        int(len(dates) * 0.8)
        if holdout_start is None
        else int(np.searchsorted(dates, pd.Timestamp(holdout_start)))
    )
    if holdout_index < min_train + horizon + embargo or holdout_index >= len(dates) - 5:
        if holdout_start is not None:
            raise ValueError(
                "Holdout requires sufficient training history and at least six reserved sessions"
            )
        holdout_index = None
    anchors = list(range(min_train + horizon + embargo, len(dates), refit_every))
    if holdout_index is not None:
        anchors = sorted(set(anchors + [holdout_index]))
    for fold_index, index in enumerate(anchors):
        next_index = anchors[fold_index + 1] if fold_index + 1 < len(anchors) else len(dates)
        first, last = dates[index], dates[next_index - 1]
        frozen_holdout = holdout_index is not None and index >= holdout_index
        origin = holdout_index if frozen_holdout else index
        cutoff = dates[origin - embargo - 1]
        train = data.loc[data.label_end.le(cutoff) & data.date.lt(dates[origin])].dropna(
            subset=["target_return"]
        )
        if train.date.nunique() < min_train or len(train) < len(all_fields) + len(symbols) + 10:
            continue
        test = data.loc[data.date.between(first, last)].copy()
        if test.empty:
            continue
        x, xt = _design(train, test, all_fields, symbols)
        test["prediction"] = _ridge_predict(
            x, train.target_return.to_numpy(float), xt, ridge, len(all_fields)
        )
        # Baseline has the same ETF intercepts and only the declared controls.
        if controls_fields:
            bx, bxt = _design(train, test, controls_fields, symbols)
            baseline = _ridge_predict(
                bx, train.target_return.to_numpy(float), bxt, ridge, len(controls_fields)
            )
        else:
            baseline = (
                test.symbol.map(train.groupby("symbol").target_return.mean())
                .fillna(train.target_return.mean())
                .to_numpy(float)
            )
        test["baseline_prediction"] = baseline
        test["training_label_end"] = train.label_end.max()
        test["training_feature_end"] = train.date.max()
        predictions.append(test)
        folds.append(
            {
                "phase": "frozen_holdout" if frozen_holdout else "walk_forward",
                "test_start": str(first.date()),
                "test_end": str(last.date()),
                "training_label_end": str(train.label_end.max().date()),
                "training_sessions": int(train.date.nunique()),
                "training_rows": len(train),
                "test_rows": len(test),
            }
        )
        last_train = train
    if not predictions:
        raise ValueError(
            "Insufficient dated feature coverage for the requested purged training window"
        )
    prediction = pd.concat(predictions, ignore_index=True)
    scored = prediction.dropna(subset=["target_return"])
    daily = []
    grouped = {field: [] for field in fields}
    for day, group in scored.groupby("date"):
        valid_ic = (
            len(group) >= 5 and group.prediction.nunique() > 1 and group.target_return.nunique() > 1
        )
        ic = float(group.prediction.rank().corr(group.target_return.rank())) if valid_ic else None
        daily.append({"date": str(day.date()), "rank_ic": ic, "assets": len(group)})
        for field in fields:
            if len(group) >= 5 and group[field].nunique() > 1:
                order = group.sort_values([field, "symbol"])
                size = max(1, len(order) // 3)
                grouped[field].append(
                    {
                        "date": str(day.date()),
                        "low": float(order.target_return.iloc[:size].mean()),
                        "middle": float(order.target_return.iloc[size:-size].mean()),
                        "high": float(order.target_return.iloc[-size:].mean()),
                    }
                )
    ic = _hac_mean([p["rank_ic"] for p in daily if p["rank_ic"] is not None], horizon)
    denominator = float(((scored.target_return - scored.baseline_prediction) ** 2).sum())
    mse = float(((scored.target_return - scored.prediction) ** 2).sum())
    r2 = 1 - mse / denominator if denominator > 1e-16 else None
    warnings = [
        "fixed_etf_universe_and_correlated_assets",
        "daily_price_and_finite_strike_literature_adaptations",
        "historical_vendor_revisions_not_bitemporal",
        "multiple_testing_not_corrected",
        "group_returns_overlap_and_are_not_a_tradeable_nav",
    ]
    if len(folds) < 6 or scored.date.nunique() < 120:
        warnings.append("short_out_of_sample_history")
    if ic["mean"] is not None and abs(ic["mean"]) > 0.1:
        warnings.append("large_ic_requires_leakage_and_shared_index_exposure_review")
    if r2 is not None and r2 > 0.2:
        warnings.append("large_oos_r2_requires_alignment_review")
    groups = []
    for field, points in grouped.items():
        if points:
            frame = pd.DataFrame(points)
            groups.append(
                {
                    "field": field,
                    "low": float(frame.low.mean()),
                    "middle": float(frame.middle.mean()),
                    "high": float(frame.high.mean()),
                    "high_minus_low": _hac_mean(frame.high - frame.low, horizon),
                    "points": points,
                }
            )
    config = {
        "fields": list(fields),
        "controls": controls_fields,
        "horizon": horizon,
        "min_train": min_train,
        "refit_every": refit_every,
        "ridge": ridge,
        "embargo": embargo,
        "holdout_start": str(dates[holdout_index].date()) if holdout_index is not None else None,
    }
    fingerprint = sha256(
        pd.util.hash_pandas_object(panel, index=False).to_numpy().tobytes()
    ).hexdigest()
    run_id = sha256(
        (RESEARCH_VERSION + json.dumps(config, sort_keys=True) + fingerprint).encode()
    ).hexdigest()[:20]
    columns = [
        "date",
        "symbol",
        "prediction",
        "baseline_prediction",
        "target_return",
        "label_start",
        "label_end",
        "training_label_end",
        "training_feature_end",
    ]
    records = prediction[columns].copy()
    for name in columns:
        if name in (
            "date",
            "label_start",
            "label_end",
            "training_label_end",
            "training_feature_end",
        ):
            records[name] = records[name].map(
                lambda d: None if pd.isna(d) else str(pd.Timestamp(d).date())
            )
    output = records.astype(object).where(pd.notna(records), None).to_dict("records")
    holdout = (
        prediction.loc[prediction.date.ge(dates[holdout_index])]
        if holdout_index is not None
        else prediction.iloc[:0]
    )
    reserved = None
    ablations = []
    if not holdout.empty:
        scored_holdout = holdout.dropna(subset=["target_return"])

        def metrics(frame):
            daily_values = [
                float(g.prediction.rank().corr(g.target_return.rank()))
                for _, g in frame.groupby("date")
                if len(g) >= 5 and g.prediction.nunique() > 1 and g.target_return.nunique() > 1
            ]
            denominator = float(((frame.target_return - frame.baseline_prediction) ** 2).sum())
            return {
                "rank_ic": _hac_mean(daily_values, horizon),
                "oos_r2_vs_controls": 1
                - float(((frame.target_return - frame.prediction) ** 2).sum()) / denominator
                if denominator > 1e-16
                else None,
                "scored_rows": len(frame),
            }

        reserved = {
            "start": str(holdout.date.min().date()),
            "end": str(holdout.date.max().date()),
            "training_label_end": str(holdout.training_label_end.max().date()),
            "method": "single_frozen_pre_holdout_fit_no_retuning",
            "sessions": int(scored_holdout.date.nunique()),
            **metrics(scored_holdout),
        }
        for field in fields:
            subset = list(dict.fromkeys([field, *controls_fields]))
            x, xt = _design(last_train, scored_holdout, subset, symbols)
            trial = scored_holdout.copy()
            trial["prediction"] = _ridge_predict(
                x, last_train.target_return.to_numpy(float), xt, ridge, len(subset)
            )
            ablations.append({"field": field, **metrics(trial)})
    return {
        "independent_holdout": reserved,
        "holdout_ablations": ablations,
        "id": run_id,
        "version": RESEARCH_VERSION,
        "data_hash": fingerprint,
        "config": config,
        "coverage": {
            "input_rows": total,
            "complete_feature_rows": len(data),
            "prediction_rows": len(prediction),
            "scored_rows": len(scored),
            "unscored_rows": len(prediction) - len(scored),
            "oos_sessions": int(scored.date.nunique()),
            "symbols": symbols,
        },
        "rank_ic": ic,
        "oos_r2_vs_controls": r2,
        "folds": folds,
        "daily_ic": daily,
        "groups": groups,
        "training_inference": _training_inference(last_train, all_fields, symbols, horizon),
        "predictions": output,
        "warnings": warnings,
        "label_convention": "close-t predictors; adjusted next-session open to close at t+h; unknown tail labels retained unscored",
        "inference": "Date-clustered Newey-West training OLS t-statistics; OOS IC uses horizon-lag HAC. Ridge test predictions have no post-selection significance claim.",
        "trading": "Submit the generated project to the existing SDK backtest engine for fills, costs and NAV.",
    }
