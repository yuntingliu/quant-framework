"""Bounded dated surface valuation and immutable calibration artifacts."""

import hashlib
import json
import os
import re
import uuid
from math import log

import numpy as np

from alphalab.dataio.catalog import DataCatalog
from alphalab.dataio.errors import MissingDataError
from alphalab.options import EuropeanOption, black_scholes, surface_iv
from alphalab.options.calibration import calibrate_merton
from alphalab.options.ssvi import ssvi_iv
from apps.api.services import options_service


def _path(model_id, root=None):
    if not re.fullmatch("[0-9a-f]{20}", model_id):
        raise ValueError("Invalid calibration identifier")
    return DataCatalog(root).root / "research" / "option_models" / (model_id + ".json")


def read_calibration(model_id, root=None):
    path = _path(model_id, root)
    if not path.is_file():
        raise MissingDataError("Saved calibration is unavailable")
    return json.loads(path.read_text(encoding="utf-8"))


def calibrate(request, root=None):
    chain = options_service.analyze_snapshot(
        request["underlying"],
        request["day"],
        rate=request.get("rate"),
        price_field=request.get("price_field", "close"),
        include_surface=False,
        root=root,
    )
    source = json.dumps(
        {
            "version": "merton-calibration-v2-sensitivity",
            "rate": request.get("rate"),
            "quotes": chain["contracts"],
        },
        sort_keys=True,
        allow_nan=False,
    )
    model_id = hashlib.sha256(source.encode()).hexdigest()[:20]
    if _path(model_id, root).is_file():
        return read_calibration(model_id, root)
    report = calibrate_merton(chain)
    report.update(
        id=model_id,
        manual_rate=request.get("rate"),
        source_sha256=hashlib.sha256(source.encode()).hexdigest(),
    )
    path = _path(model_id, root)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix("." + uuid.uuid4().hex + ".tmp")
    temp.write_text(json.dumps(report, ensure_ascii=False, allow_nan=False), encoding="utf-8")
    os.replace(temp, path)
    return report


def surface_price(request, root=None):
    chain = options_service.analyze_snapshot(
        request["underlying"],
        request["day"],
        rate=request.get("rate"),
        price_field=request.get("price_field", "close"),
        root=root,
    )
    t = request["maturity_days"] / 365
    ready = sorted(
        (f for f in chain["forward_curve"] if f["status"] == "ready"), key=lambda f: f["expiry"]
    )
    import pandas as pd

    times = [(pd.Timestamp(f["expiry"]) - pd.Timestamp(chain["date"])).days / 365 for f in ready]
    if not times or not times[0] <= t <= times[-1]:
        raise ValueError("Surface query outside observed maturities")
    forward = float(np.exp(np.interp(t, times, np.log([f["forward"] for f in ready]))))
    k = log(request["strike"] / forward)
    model = request.get("model", "ssvi")
    surface = chain["surface"]["ssvi"] if model == "ssvi" else chain["surface"]
    vol = ssvi_iv(surface, t, k) if model == "ssvi" else surface_iv(surface, t, k)
    rate = request.get("rate")
    if rate is None:
        rate = options_service._dated_curve(chain["date"], root).rate(t)
    spot = chain["contracts"][0]["spot"]
    q = rate - log(forward / spot) / t
    option = EuropeanOption(spot, request["strike"], t, rate, vol, request["option_type"], q)
    return {
        "model": model,
        "date": chain["date"],
        "price_field": chain["price_field"],
        "strike": request["strike"],
        "maturity_days": request["maturity_days"],
        "forward": forward,
        "iv": vol,
        "price": black_scholes(option),
        "unit": "per_underlying_unit",
        "domain": "shared_observed_moneyness_and_maturities",
    }
