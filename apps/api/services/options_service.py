"""ETF option workstation services over shared, dated local research data."""

from collections import Counter
from functools import lru_cache

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

from alphalab.dataio.catalog import DataCatalog
from alphalab.dataio.errors import MissingDataError
from alphalab.dataio.option_data import load_option_snapshot
from alphalab.dataio.quality import QUALITY_REPORT_VERSION, validate_dataset
from alphalab.dataio.runtime import OperationsStore, RuntimeStore
from alphalab.dataio.symbols import to_rq_symbol
from alphalab.options import analyze_chain
from alphalab.options.curves import DiscountCurve
from alphalab.options.portfolio import basket_stress, joint_monte_carlo
from alphalab.options.surface import volatility_surface

ETF_NAMES = {
    "510050.XSHG": "上证50ETF",
    "510300.XSHG": "沪深300ETF·沪市",
    "159919.XSHE": "沪深300ETF·深市",
    "510500.XSHG": "中证500ETF·沪市",
    "159922.XSHE": "中证500ETF·深市",
    "159915.XSHE": "创业板ETF",
    "159901.XSHE": "深证100ETF",
    "588000.XSHG": "科创50ETF·华夏",
    "588080.XSHG": "科创50ETF·易方达",
}


@lru_cache(maxsize=65536)
def _row_count(path, size, mtime):
    return pq.ParquetFile(path).metadata.num_rows


def universe(root=None):
    catalog = DataCatalog(root)
    found = {}
    revision = 0
    for path in catalog.files("rq.option_snapshots"):
        underlying = path.parent.name.removeprefix("underlying=")
        day = path.parent.parent.name.removeprefix("date=")
        stat = path.stat()
        revision = max(revision, stat.st_mtime_ns)
        value = found.setdefault(underlying, {"dates": [], "rows": 0})
        value["dates"].append(day)
        value["rows"] += _row_count(str(path), stat.st_size, stat.st_mtime_ns)
    rows = []
    for symbol in dict.fromkeys([*ETF_NAMES, *found]):
        value = found.get(symbol, {"dates": [], "rows": 0})
        dates = sorted(set(value["dates"]))
        rows.append(
            {
                "symbol": symbol,
                "name": ETF_NAMES.get(symbol, symbol),
                "dates": dates,
                "sessions": len(dates),
                "rows": value["rows"],
                "start": dates[0] if dates else None,
                "latest": dates[-1] if dates else None,
            }
        )
    for dataset in ("rq.option_rates", "rq.option_underlyings", "research.option_features", "research.option_predictions", "research.option_intraday_variance"):
        for path in catalog.files(dataset):
            revision = max(revision, path.stat().st_mtime_ns)
    jobs = OperationsStore(root).list_jobs(limit=8)
    return {
        "revision": str(revision),
        "underlyings": rows,
        "total_rows": sum(r["rows"] for r in rows),
        "rates": {
            key: value
            for key, value in catalog.status("rq.option_rates").items()
            if key in {"status", "date_start", "date_end", "rows"}
        },
        "jobs": [
            {key: job.get(key) for key in ("id", "status", "progress", "total", "message")}
            for job in jobs
            if job.get("request", {}).get("project_id", "").startswith("etf-options")
        ],
    }


def _dated_curve(day, root):
    try:
        frame = RuntimeStore(root).read("rq.option_rates")
    except MissingDataError as exc:
        raise MissingDataError(
            "Synchronize the sovereign discount curve before market-rate analysis"
        ) from exc
    return DiscountCurve.as_of(frame, day)


def realized_volatility(underlying, day, root=None):
    try:
        frame = RuntimeStore(root).read("rq.option_underlyings")
    except MissingDataError:
        return {"status": "missing_adjusted_history", "rv20": None, "rv60": None, "returns": []}
    dates = pd.to_datetime(frame.date)
    frame = frame.loc[frame.symbol.eq(underlying) & dates.le(pd.Timestamp(day))].sort_values("date")
    close = frame.drop_duplicates("date").set_index("date").adjusted_close.astype(float)
    returns = np.log(close / close.shift(1)).dropna()
    result = {
        "status": "ready" if len(returns) >= 20 else "insufficient_history",
        "observations": len(returns),
        "source": "RQ pre-adjusted ETF close; 252 sessions/year",
        "rv20": float(returns.tail(20).std(ddof=1) * np.sqrt(252)) if len(returns) >= 20 else None,
        "rv60": float(returns.tail(60).std(ddof=1) * np.sqrt(252)) if len(returns) >= 60 else None,
        "returns": [
            {"date": str(pd.Timestamp(d).date()), "return": float(v)}
            for d, v in returns.tail(90).items()
        ],
    }
    return result


def analyze_snapshot(
    underlying,
    day,
    *,
    rate=None,
    dividend_yield=None,
    carry_mode="put_call_forward",
    price_field="close",
    include_surface=True,
    root=None,
):
    underlying = to_rq_symbol(underlying)
    frame = load_option_snapshot(underlying, day, root=root)
    kwargs = {"rate": rate}
    curve = None
    if rate is None:
        curve = _dated_curve(day, root)
        rates = {
            str(pd.Timestamp(expiry).date()): curve.rate(
                max(0.0, (pd.Timestamp(expiry) - pd.Timestamp(day)).days / 365)
            )
            for expiry in frame.maturity_date.unique()
        }
        kwargs = {"rates_by_expiry": rates}
    chain = analyze_chain(
        frame,
        **kwargs,
        dividend_yield=dividend_yield,
        carry_mode=carry_mode,
        price_field=price_field,
    )
    chain["discount_curve"] = (
        curve.describe() if curve else {"source": "explicit_manual_rate", "rate": rate}
    )
    chain["name"] = ETF_NAMES.get(underlying, underlying)
    chain["source"] = "real_rqdata"
    chain["retrieved_at"] = str(frame.retrieved_at.max()) if "retrieved_at" in frame else None
    chain["status_counts"] = dict(Counter(r["status"] for r in chain["contracts"]))
    activity = frame.groupby("option_type")[["volume", "open_interest"]].sum()
    call_volume = float(activity.loc["C", "volume"]) if "C" in activity.index else 0.0
    put_volume = float(activity.loc["P", "volume"]) if "P" in activity.index else 0.0
    call_oi = float(activity.loc["C", "open_interest"]) if "C" in activity.index else 0.0
    put_oi = float(activity.loc["P", "open_interest"]) if "P" in activity.index else 0.0
    chain["activity"] = {
        "call_volume": call_volume,
        "put_volume": put_volume,
        "call_oi": call_oi,
        "put_oi": put_oi,
        "volume_pcr": put_volume / call_volume if call_volume else None,
        "oi_pcr": put_oi / call_oi if call_oi else None,
    }
    chain["realized_volatility"] = realized_volatility(underlying, day, root)
    from alphalab.options.features import option_features
    predictors = option_features(chain, pd.DataFrame(columns=["date", "adjusted_close"]))
    chain["constant_maturity"] = {"atm_iv_30": predictors["option_atm_iv_30"], "atm_iv_60": predictors["option_atm_iv_60"], "atm_iv_90": predictors["option_atm_iv_90"]}
    chain["atm_definition"] = "forward_atm_constant_maturity_total_variance"
    if include_surface:
        chain["surface"] = volatility_surface(chain)
        from alphalab.options.ssvi import ssvi_surface
        chain["surface"]["ssvi"] = ssvi_surface(chain["surface"])
    return chain


def _file_key(paths):
    return tuple((str(p), p.stat().st_size, p.stat().st_mtime_ns) for p in paths)


@lru_cache(maxsize=32)
def _history_cached(underlying, snapshots, rate_files, price_field, rate):
    rates = pd.concat([pq.ParquetFile(p).read().to_pandas() for p, _, _ in rate_files], ignore_index=True) if rate_files else pd.DataFrame()
    points = []
    for path, _, _ in snapshots:
        frame = pq.ParquetFile(path).read().to_pandas()
        day = pd.Timestamp(frame.date.iloc[0])
        settings = {"rate": rate}
        if rate is None:
            curve = DiscountCurve.as_of(rates, day)
            settings = {"rates_by_expiry": {str(pd.Timestamp(e).date()): curve.rate(max(0, (pd.Timestamp(e)-day).days/365)) for e in frame.maturity_date.unique()}}
        from alphalab.dataio.option_snapshots import validate_option_partitions
        from alphalab.options import constant_maturity_atm
        validate_option_partitions(frame)
        points.append(constant_maturity_atm(frame, **settings, price_field=price_field))
    return {"underlying": underlying, "points": points, "price_field": price_field, "atm_definition": "forward_atm_constant_30d", "rate": rate}


def history(underlying, *, end=None, limit=40, price_field="close", rate=None, root=None):
    if not 1 <= limit <= 90 or price_field not in {"close", "settlement"}:
        raise ValueError("Invalid history limit or price field")
    underlying = to_rq_symbol(underlying)
    catalog = DataCatalog(root)
    paths = sorted(p for p in catalog.files("rq.option_snapshots") if p.parent.name == f"underlying={underlying}" and (end is None or p.parent.parent.name <= f"date={end}"))[-limit:]
    return _history_cached(underlying, _file_key(paths), _file_key(catalog.files("rq.option_rates")) if rate is None else (), price_field, rate)


def quality(root=None):
    catalog, operations = DataCatalog(root), OperationsStore(root)

    def checked(dataset):
        status = catalog.status(dataset)
        saved = operations.latest_quality(dataset)
        if saved:
            prior = saved["report"]
            metrics = prior.get("metrics", {})
            files = catalog.files(dataset)
            latest_change = max((p.stat().st_mtime_ns for p in files), default=0)
            checked_ns = pd.Timestamp(saved["checked_at"]).value
            # Reuse only a full-scope check newer than every retained partition.
            # File rewrites, removed partitions, and unresolved gaps invalidate it.
            if (prior.get("validation_version") == QUALITY_REPORT_VERSION
                    and status["status"] == "ready" and prior["rows"] == status["rows"]
                    and prior["files"] == status["files"] and checked_ns >= latest_change
                    and metrics.get("start_date") == status["date_start"]
                    and metrics.get("end_date") == status["date_end"]
                    and not metrics.get("missing_sessions") and not metrics.get("missing_keys")
                    and metrics.get("scope_symbols") is None):
                return {**prior, "checked_at": saved["checked_at"]}
        return validate_dataset(dataset, root, fail_on_gap=True)

    report = {
        "snapshots": checked("rq.option_snapshots"),
        "rates": checked("rq.option_rates"),
        "underlyings": checked("rq.option_underlyings"),
    }
    for key, dataset in (("features", "research.option_features"), ("intraday", "rq.option_etf_5m"), ("realized", "research.option_intraday_variance")):
        if DataCatalog(root).files(dataset):
            report[key] = checked(dataset)
    return report


def portfolio(request, root=None):
    reports, legs, sizes = {}, [], {}
    for item in request["legs"]:
        symbol = to_rq_symbol(item["underlying"])
        key = (symbol, str(item["contract"]))
        sizes[key] = sizes.get(key, 0.0) + item["quantity"]
    for (symbol, contract), quantity in sizes.items():
        if quantity == 0:
            continue
        if symbol not in reports:
            reports[symbol] = analyze_snapshot(
                symbol, request["day"], rate=request.get("rate"), price_field=request.get("price_field", "close"), include_surface=False, root=root
            )
        row = next(
            (r for r in reports[symbol]["contracts"] if r["order_book_id"] == contract), None
        )
        if row is None or row["status"] != "eligible":
            raise ValueError("A selected contract has no eligible IV on the selected date")
        legs.append({**row, "underlying": symbol, "quantity": quantity})
    result = basket_stress(
        legs,
        days_forward=request.get("days_forward", 0),
        spot_shocks=request.get(
            "spot_shocks", [-0.4, -0.3, -0.2, -0.1, -0.05, 0, 0.05, 0.1, 0.2, 0.3, 0.4]
        ),
        volatility_shifts=request.get("volatility_shifts", [0, 0.1, 0.3]),
    )
    result["valuation_context"] = {"day": request["day"], "price_field": request.get("price_field", "close"), "rate": request.get("rate")}
    if request.get("include_mc", True):
        assets = {}
        for symbol, report in reports.items():
            rows = [r for r in report["contracts"] if r["status"] == "eligible"]
            reference = min(
                rows,
                key=lambda r: (
                    abs(r["maturity"] - 30 / 365),
                    abs(np.log(r["strike"] / r["forward"])),
                ),
            )
            assets[symbol] = {
                "spot": reference["spot"],
                "volatility": reference["iv"],
                "forwards": [
                    {
                        "maturity": (pd.Timestamp(f["expiry"]) - pd.Timestamp(request["day"])).days
                        / 365,
                        "forward": f["forward"],
                    }
                    for f in report["forward_curve"]
                    if f["status"] == "ready"
                ],
            }
        settings = {
            k: request[k]
            for k in (
                "paths",
                "seed",
                "correlation",
                "jump_intensity",
                "jump_mean",
                "jump_volatility",
                "days_forward",
                "sampling",
                "importance_tilt",
            )
            if k in request
        }
        if request.get("calibration_id"):
            from apps.api.services.options_model_service import read_calibration
            calibration = read_calibration(request["calibration_id"], root)
            if (not calibration["usable"] or set(assets) != {calibration["underlying"]} or calibration["date"] != str(request["day"]) or calibration["price_field"] != request.get("price_field", "close") or calibration["manual_rate"] != request.get("rate")):
                raise ValueError("Calibration diagnostics or valuation context do not match this single-ETF basket")
            params = calibration["parameters"]
            assets[calibration["underlying"]]["volatility"] = params["diffusion_volatility"]
            settings.update({key: params[key] for key in ("jump_intensity", "jump_mean", "jump_volatility")})
            result["calibration_id"] = calibration["id"]
        base = joint_monte_carlo(legs, assets, **settings)
        crash = joint_monte_carlo(legs, assets, **settings, spot_shock=-0.3, volatility_shift=0.2)
        result["monte_carlo"] = {
            "base": base,
            "crash": crash,
            "model_pnl": crash["value"] - base["value"],
            "underlying_models": assets,
        }
    return result


def contract_identities(underlying, day, root=None):
    from alphalab.options.contracts import contract_names
    underlying = to_rq_symbol(underlying)
    frame = load_option_snapshot(underlying, day, root=root)
    return {"contracts": [{"underlying":underlying,"order_book_id":str(r.order_book_id),**contract_names(underlying,str(pd.Timestamp(r.maturity_date).date()),r.option_type,float(r.strike_price),float(r.contract_multiplier))} for r in frame.itertuples()]}
