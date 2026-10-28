"""Quality-gated daily option chains and full-revaluation stress scenarios."""

from dataclasses import replace
from math import exp, isfinite, log

import pandas as pd

from .contracts import contract_names
from .pricing import (
    EuropeanOption,
    black_scholes,
    greeks,
    implied_volatility,
    monte_carlo,
    price_bounds,
)
from .quality import NUMERIC, validate_snapshot


def _put_call_forwards(frame, day, rate_for_expiry, price_field, min_volume, min_open_interest):
    """Infer expiry forwards from liquid near-ATM pairs; carry includes quote basis."""
    result = {}
    if "quote_quarantine" in frame:
        frame = frame.loc[frame.quote_quarantine.isna()]
    active = frame.loc[
        frame.volume.gt(0)
        & frame.volume.ge(min_volume)
        & frame.open_interest.ge(min_open_interest)
        & frame[price_field].gt(0)
    ]
    for expiry, group in active.groupby("maturity_date"):
        t = (pd.Timestamp(expiry) - day).days / 365
        if t <= 0:
            continue
        rate = rate_for_expiry(str(pd.Timestamp(expiry).date()))
        spot = float(group.spot.iloc[0])
        candidates = []
        for (strike, multiplier), pair in group.groupby(["strike_price", "contract_multiplier"]):
            distance = abs(log(strike / spot))
            if len(pair) != 2 or set(pair.option_type) != {"C", "P"} or distance > 0.15:
                continue
            call = float(pair.loc[pair.option_type.eq("C"), price_field].iloc[0])
            put = float(pair.loc[pair.option_type.eq("P"), price_field].iloc[0])
            forward = float(strike + exp(rate * t) * (call - put))
            if forward > 0 and isfinite(forward):
                candidates.append((distance, float(strike), float(multiplier), forward))
        candidates.sort()
        near = [value for value in candidates if value[0] <= 0.05]
        expanded = len({value[1] for value in near}) < 3
        # Coarse strike ladders can have only 1-2 pairs inside 5%. Expand only
        # enough to obtain three unique nearby strikes, never beyond 15% log K/S.
        selected_candidates = candidates if expanded else near
        chosen, used_strikes = [], set()
        for value in selected_candidates:
            if value[1] not in used_strikes:
                chosen.append(value)
                used_strikes.add(value[1])
            if len(chosen) == (3 if expanded else 7):
                break
        key = str(pd.Timestamp(expiry).date())
        if len(chosen) < 3:
            result[key] = {"expiry": key, "status": "insufficient_pairs", "pairs": len(chosen)}
            continue
        forwards = [value[3] for value in chosen]
        forward = float(pd.Series(forwards).median())
        spread = (max(forwards) - min(forwards)) / forward
        result[key] = {
            "expiry": key,
            "status": "ready" if spread <= 0.01 else "inconsistent_pairs",
            "pairs": len(chosen),
            "forward": forward,
            "effective_dividend_yield": rate - log(forward / spot) / t,
            "relative_pair_range": spread,
            "expanded_pair_window": expanded,
            "max_abs_log_moneyness": max(value[0] for value in chosen),
        }
    return result


def analyze_chain(
    frame: pd.DataFrame,
    *,
    rate: float | None = None,
    rates_by_expiry: dict[str, float] | None = None,
    dividend_yield: float | None = None,
    carry_mode="input_yield",
    price_field="close",
    min_volume=1,
    min_open_interest=1,
    parity_tolerance=0.005,
) -> dict:
    """Daily indicative IV, smile and term structure. No executable midquote claim.

    Rates are explicit continuously compounded annual inputs. input_yield uses
    the supplied dividend yield; put_call_forward infers carry for each expiry.
    Effective implied carry includes basis and is not a cash-dividend forecast.
    """
    if (rate is None) == (rates_by_expiry is None):
        raise ValueError("Provide one finite rate or an expiry discount curve")
    if rate is not None and not isfinite(rate):
        raise ValueError("An explicit finite rate is required")
    if carry_mode not in {"input_yield", "put_call_forward"}:
        raise ValueError("Unknown carry mode")
    if carry_mode == "input_yield" and (dividend_yield is None or not isfinite(dividend_yield)):
        raise ValueError("An explicit finite dividend yield is required in input_yield mode")
    if carry_mode == "put_call_forward" and dividend_yield is not None:
        raise ValueError("Omit dividend_yield when inferring the put/call forward")
    if price_field not in ("close", "settlement"):
        raise ValueError("price_field must be close or settlement")
    if min_volume < 0 or min_open_interest < 0 or parity_tolerance < 0:
        raise ValueError("Liquidity thresholds and parity tolerance must be nonnegative")
    if frame.empty:
        raise ValueError("Empty option chain")
    day = pd.Timestamp(frame.iloc[0].date)
    underlying = str(frame.iloc[0].underlying)
    validate_snapshot(frame, frame.order_book_id.tolist(), day, underlying)
    frame = frame.copy()
    frame[NUMERIC] = frame[NUMERIC].apply(pd.to_numeric)

    def rate_for_expiry(expiry):
        value = rate if rates_by_expiry is None else rates_by_expiry.get(expiry)
        if value is None or not isfinite(value):
            raise ValueError(f"Missing finite discount rate for expiry {expiry}")
        return float(value)

    forwards = (
        _put_call_forwards(frame, day, rate_for_expiry, price_field, min_volume, min_open_interest)
        if carry_mode == "put_call_forward"
        else {}
    )
    rows = []
    for item in frame.to_dict("records"):
        t = (pd.Timestamp(item["maturity_date"]) - day).days / 365
        row = {
            "order_book_id": str(item["order_book_id"]),
            "expiry": str(pd.Timestamp(item["maturity_date"]).date()),
            "option_type": item["option_type"],
            "strike": float(item["strike_price"]),
            "multiplier": float(item["contract_multiplier"]),
            "market_price": float(item[price_field]),
            "reference_prices": {key: float(item[key]) for key in ("close", "settlement")},
            "spot": float(item["spot"]),
            "volume": float(item["volume"]),
            "open_interest": float(item["open_interest"]),
            "quote_status": "traded" if item["volume"] > 0 else "no_trade_reference",
            "maturity": t,
            "iv": None,
            "status": "eligible",
            "close_recovery": item.get("close_recovery")
            if pd.notna(item.get("close_recovery"))
            else None,
            "source_close": float(item["source_close"])
            if pd.notna(item.get("source_close"))
            else None,
        }
        row["rate"] = rate_for_expiry(row["expiry"])
        row.update(
            contract_names(
                underlying, row["expiry"], row["option_type"], row["strike"], row["multiplier"]
            )
        )
        row["vendor_name"] = item.get("vendor_name") if pd.notna(item.get("vendor_name")) else None
        if pd.notna(item.get("quote_quarantine")):
            row["status"] = "source_quote_invalid"
            row["quote_status"] = "quarantined_source_ohlc"
            row["reason"] = "Unresolved source OHLC; excluded from valuation"
        elif t <= 0:
            row["status"] = "expired_or_expiry_day"
        elif (
            item["volume"] <= 0
            or item["volume"] < min_volume
            or item["open_interest"] < min_open_interest
        ):
            row["status"] = "illiquid"
        elif item[price_field] <= 0:
            row["status"] = "zero_price"
        elif (
            carry_mode == "put_call_forward"
            and forwards.get(row["expiry"], {}).get("status") != "ready"
        ):
            row["status"] = "forward_unavailable"
        else:
            q = (
                forwards[row["expiry"]]["effective_dividend_yield"]
                if carry_mode == "put_call_forward"
                else dividend_yield
            )
            row["effective_dividend_yield"] = q
            row["forward"] = item["spot"] * exp((row["rate"] - q) * t)
            option = EuropeanOption(
                item["spot"],
                item["strike_price"],
                t,
                row["rate"],
                0.2,
                item["option_type"],
                q,
            )
            lower, upper = price_bounds(option)
            row["iv_diagnostic"] = {
                "lower_bound": lower,
                "upper_bound": upper,
                "distance_from_lower": row["market_price"] - lower,
                "carry_mode": carry_mode,
                "code": "within_model_bounds",
            }
            try:
                row["iv"] = implied_volatility(item[price_field], option)
                priced = replace(option, volatility=row["iv"])
                row.update(greeks(priced))
                row["bs_price"] = black_scholes(priced)
                row["iv_repricing_error"] = row["bs_price"] - item[price_field]
                row["log_moneyness"] = log(item["strike_price"] / item["spot"])
            except ValueError as exc:
                row["status"] = "invalid_iv"
                row["reason"] = str(exc)
                row["iv_diagnostic"]["code"] = (
                    "below_model_lower" if row["market_price"] < lower - 1e-10
                    else "at_or_above_model_upper" if row["market_price"] >= upper
                    else "at_zero_volatility_boundary" if row["market_price"] <= lower + 1e-10
                    else "solver_limit"
                )
        rows.append(row)

    # Use identical adjusted multipliers as well as strikes to pair contracts.
    parity = []
    eligible = pd.DataFrame([r for r in rows if r["status"] == "eligible"])
    term = []
    static_flags = []
    if not eligible.empty:
        for (expiry, strike, multiplier), group in eligible.groupby(
            ["expiry", "strike", "multiplier"]
        ):
            if len(group) == 2 and set(group.option_type) == {"C", "P"}:
                call = group[group.option_type == "C"].iloc[0]
                put = group[group.option_type == "P"].iloc[0]
                residual = (
                    call.market_price
                    - put.market_price
                    - call.spot * exp(-call.effective_dividend_yield * call.maturity)
                    + strike * exp(-call.rate * call.maturity)
                )
                parity.append(
                    {
                        "expiry": expiry,
                        "strike": float(strike),
                        "multiplier": float(multiplier),
                        "residual": float(residual),
                        "flag": bool(abs(residual) > parity_tolerance),
                    }
                )
        for expiry, group in eligible.groupby("expiry"):
            atm = group.loc[group.log_moneyness.abs().idxmin()]
            term.append(
                {
                    "expiry": expiry,
                    "maturity": float(atm.maturity),
                    "atm_nearest_iv": float(atm.iv),
                    "atm_contract": str(atm.order_book_id),
                    "atm_log_moneyness": float(atm.log_moneyness),
                    "eligible": len(group),
                }
            )
        # Close prices can be asynchronous. Flags are diagnostics, not tradable arbitrage.
        for (expiry, kind, multiplier), group in eligible.groupby(
            ["expiry", "option_type", "multiplier"]
        ):
            group = group.sort_values("strike")
            if group.strike.duplicated().any():
                continue
            strikes = group.strike.to_numpy()
            prices = group.market_price.to_numpy()
            slopes = (prices[1:] - prices[:-1]) / (strikes[1:] - strikes[:-1])
            monotone = bool((slopes > 1e-8).any()) if kind == "C" else bool((slopes < -1e-8).any())
            convex = bool(((slopes[1:] - slopes[:-1]) < -1e-8).any())
            if monotone or convex:
                static_flags.append(
                    {
                        "expiry": expiry,
                        "option_type": kind,
                        "multiplier": float(multiplier),
                        "monotonicity_flag": monotone,
                        "convexity_flag": convex,
                    }
                )
    return {
        "underlying": underlying,
        "date": str(day.date()),
        "frequency": "1d",
        "price_field": price_field,
        "rate": rate,
        "rates_by_expiry": rates_by_expiry,
        "dividend_yield": dividend_yield,
        "carry_mode": carry_mode,
        "forward_curve": list(forwards.values()),
        "day_count": "ACT/365F",
        "greek_units": "per_unit; theta/day; vega,rho/1pct",
        "quote_quality": "daily_indicative; bid/ask and synchronized ticks unavailable",
        "assumptions": [
            "European exercise",
            "flat continuous rate; Greeks hold the selected per-expiry carry fixed",
            (
                "put/call effective carry includes quote basis; it is not a cash dividend forecast"
                if carry_mode == "put_call_forward"
                else "user-supplied continuous dividend yield"
            ),
            "IV repricing is an inversion check, not a predictive valuation",
        ],
        "contracts": rows,
        "term_structure": term,
        "put_call_parity": parity,
        "static_arbitrage_flags": static_flags,
        "eligible": sum(r["status"] == "eligible" for r in rows),
        "total": len(rows),
    }


def stress_grid(
    option: EuropeanOption,
    *,
    spot_shocks=(-0.3, -0.1, 0.0, 0.1),
    volatility_shifts=(0.0, 0.2),
    days_forward=0,
    multiplier=10000.0,
    quantity=1.0,
    paths=100_000,
    seed=42,
    jump_intensity=1.0,
    jump_mean=-0.15,
    jump_volatility=0.15,
) -> dict:
    if not all(isfinite(x) for x in (days_forward, multiplier, quantity)):
        raise ValueError("Stress settings must be finite")
    if days_forward < 0 or multiplier <= 0:
        raise ValueError("Nonnegative horizon and positive multiplier required")
    if days_forward > option.maturity * 365 + 1e-10:
        raise ValueError("Stress horizon cannot extend beyond option expiry")
    if not spot_shocks or not volatility_shifts or len(spot_shocks) * len(volatility_shifts) > 100:
        raise ValueError("Provide between 1 and 100 scenarios")
    base = black_scholes(option)
    baseline_mc = monte_carlo(
        option,
        paths=paths,
        seed=seed,
        jump_intensity=jump_intensity,
        jump_mean=jump_mean,
        jump_volatility=jump_volatility,
    )
    rows = []
    for shock in spot_shocks:
        for vol_shift in volatility_shifts:
            if not isfinite(shock) or not isfinite(vol_shift) or shock <= -1:
                raise ValueError("Finite shocks required; spot shock must exceed -100%")
            stressed = replace(
                option,
                spot=option.spot * (1 + shock),
                volatility=option.volatility + vol_shift,
                maturity=max(0.0, option.maturity - days_forward / 365),
            )
            bs = black_scholes(stressed)
            mc = monte_carlo(
                stressed,
                paths=paths,
                seed=seed,
                jump_intensity=jump_intensity,
                jump_mean=jump_mean,
                jump_volatility=jump_volatility,
            )
            rows.append(
                {
                    "spot_shock": shock,
                    "volatility_shift": vol_shift,
                    "spot": stressed.spot,
                    "volatility": stressed.volatility,
                    "maturity": stressed.maturity,
                    "bs_price": bs,
                    "mc": mc,
                    "bs_contract_pnl": (bs - base) * multiplier * quantity,
                    "mc_contract_pnl": (mc["price"] - baseline_mc["price"]) * multiplier * quantity,
                }
            )
    return {
        "base_bs_price": base,
        "base_mc": baseline_mc,
        "quantity": quantity,
        "multiplier": multiplier,
        "days_forward": days_forward,
        "measure": "Q",
        "calibration": "user_supplied_scenario_parameters",
        "interpretation": "Conditional model values; not physical crash probabilities or VaR. "
        "P&L is model-to-model, excludes financing, dividends received and trading costs. "
        "ci95 measures sampling error only; rare-payoff warnings invalidate naive confidence.",
        "scenarios": rows,
    }


def constant_maturity_atm(frame, *, rates_by_expiry=None, rate=None, price_field="close", days=30):
    """Same forward-ATM definition as research, solving only the required brackets.

    Missing/invalid nearest quotes expand the search; no wing or tenor
    extrapolation is permitted. The count describes the inspected ATM quotes.
    """
    from .features import _at_maturity, _interp
    from .quality import validate_snapshot

    day = pd.Timestamp(frame.date.iloc[0])
    validate_snapshot(frame, frame.order_book_id.tolist(), day, str(frame.underlying.iloc[0]))

    def dated_rate(expiry):
        value = rate if rate is not None else (rates_by_expiry or {}).get(expiry)
        if value is None or not isfinite(value):
            raise ValueError("Missing finite discount rate")
        return value

    forwards = _put_call_forwards(frame, day, dated_rate, price_field, 1, 1)
    nodes = []
    inspected = 0
    for expiry, group in frame.groupby("maturity_date"):
        key = str(pd.Timestamp(expiry).date())
        node = forwards.get(key, {})
        if node.get("status") != "ready":
            continue
        t = (pd.Timestamp(expiry) - day).days / 365
        forward = node["forward"]
        q = node["effective_dividend_yield"]
        r = dated_rate(key)
        if "quote_quarantine" in group:
            group = group.loc[group.quote_quarantine.isna()]
        group = group.loc[group.volume.gt(0) & group.open_interest.ge(1) & group[price_field].gt(0)]
        strikes = sorted(group.strike_price.unique(), key=lambda k: abs(log(k / forward)))
        values = []
        atm = None
        for strike in strikes:
            for row in group.loc[group.strike_price.eq(strike)].to_dict("records"):
                try:
                    option = EuropeanOption(
                        float(row["spot"]), float(strike), t, r, 0.2, row["option_type"], q
                    )
                    vol = implied_volatility(float(row[price_field]), option)
                    values.append({"k": log(strike / forward), "iv": vol})
                    inspected += 1
                except ValueError:
                    continue
            atm = _interp(values, "k", 0.0)
            if atm is not None:
                break
        nodes.append({"maturity": t, "variance": atm**2 if atm is not None else None})
    variance = _at_maturity(nodes, days, "variance", variance=True)
    return {
        "date": str(day.date()),
        "atm_iv": None if variance is None else variance**0.5,
        "eligible": inspected,
        "eligibility_scope": "inspected_atm_bracket_quotes",
    }
