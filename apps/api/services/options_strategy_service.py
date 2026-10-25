"""Read named option-signal strategies from frozen canonical backtests."""

import ast
import math
import re
from functools import lru_cache

from alphalab import ResultStore
from alphalab.dataio.errors import MissingDataError
from alphalab.dataio.symbols import to_rq_symbol
from alphalab.options.contracts import ETF_LABELS
from apps.api.services import options_research_service, result_service, strategy_service


def _allocation(tree):
    """Recognize the generated allocation body; do not guess for edited portfolios."""
    expected = ast.parse(
        "chosen = list(signal.selected)\n"
        "weight = min(max_weight, 1/len(chosen)) if chosen else 0.0\n"
        "return PortfolioDecision(target_weights={s: weight for s in chosen}, state=state)"
    ).body
    for function in (n for n in tree.body if isinstance(n, ast.FunctionDef)):
        if [ast.dump(n) for n in function.body[-3:]] != [ast.dump(n) for n in expected]:
            continue
        for arg, value in zip(function.args.kwonlyargs, function.args.kw_defaults):
            if arg.arg == "max_weight" and value is not None:
                cap = ast.literal_eval(value)
                if isinstance(cap, (int, float)) and math.isfinite(cap) and cap > 0:
                    return {"allocation": "capped_equal_weight", "max_weight": cap}
    return {"allocation": "custom", "max_weight": None}


def frozen_rule(source):
    """Inspect literal saved rules without executing or reading today's draft."""
    try:
        tree = ast.parse(source)
        for function in (n for n in tree.body if isinstance(n, ast.FunctionDef)):
            for call in ast.walk(function):
                if (
                    not isinstance(call, ast.Call)
                    or not isinstance(call.func, ast.Attribute)
                    or call.func.attr != "combine_factors"
                ):
                    continue
                args = {
                    kw.arg: ast.literal_eval(kw.value)
                    for kw in call.keywords
                    if kw.arg in ("weights", "normalization")
                }
                weights = args.get("weights", {})
                if not weights or not all(
                    str(k).startswith("option_")
                    and isinstance(v, (int, float))
                    and math.isfinite(v)
                    for k, v in weights.items()
                ):
                    continue
                defaults = {
                    a.arg: ast.literal_eval(v)
                    for a, v in zip(function.args.kwonlyargs, function.args.kw_defaults)
                    if v is not None and a.arg == "top_n"
                }
                frequency = (
                    "weekly"
                    if any(isinstance(n, ast.Name) and n.id == "Weekly" for n in ast.walk(function))
                    else "monthly"
                    if any(
                        isinstance(n, ast.Name) and n.id == "Monthly" for n in ast.walk(function)
                    )
                    else "custom"
                )
                symbols = []
                for node in ast.walk(tree):
                    if isinstance(node, ast.Compare) and any(
                        isinstance(op, ast.In) for op in node.ops
                    ):
                        for value in node.comparators:
                            if isinstance(value, (ast.List, ast.Tuple)):
                                items = ast.literal_eval(value)
                                if items and all(
                                    isinstance(s, str) and re.fullmatch(r"\d{6}\.(SH|SZ)", s)
                                    for s in items
                                ):
                                    symbols = list(items)
                return {
                    "weights": weights,
                    "normalization": args.get("normalization"),
                    "top_n": defaults.get("top_n"),
                    "frequency": frequency,
                    "symbols": symbols,
                    "execution": "unknown",
                    **_allocation(tree),
                }
    except (SyntaxError, ValueError, TypeError):
        pass
    return None


def _family(weights):
    if len(weights) == 1 and next(iter(weights)).startswith("option_prediction_"):
        return "forecast", "样本外预测 ETF 轮动", "OOS forecast ETF rotation"
    if weights == {"option_smirk": -1}:
        return "smirk", "低 Smirk ETF 轮动", "Low-smirk ETF rotation"
    if weights == {"option_vrp_hf_30": 1}:
        return "vrp", "高方差风险溢价 ETF 轮动", "High-VRP ETF rotation"
    if weights == {"option_smirk": -1, "option_term_slope": 1, "option_vrp_hf_30": 1}:
        return "blend", "三指标 ETF 轮动", "Three-feature ETF rotation"
    return "custom", "自定义期权信号 ETF 轮动", "Custom option-signal ETF rotation"


def _card(record, rule):
    activations = {e.get("activation") for e in record.get("executions", [])}
    rule = {
        **rule,
        "execution": next(iter(activations)) if len(activations) == 1 else "mixed_or_unknown",
    }
    summary = result_service.build_backtest_summary(record)
    family, zh, en = _family(rule["weights"])
    research_id = record.get("settings", {}).get("option_research_id")
    return {
        "backtest_id": record["id"],
        "project_id": record["project_id"],
        "strategy_id": record["project_strategy_id"],
        "name_zh": zh,
        "name_en": en,
        "family": family,
        "rule": rule,
        "period": summary["period"],
        "metrics": summary["metrics"],
        "run_at": record.get("run_at"),
        "warnings": summary["warnings"],
        "execution_reliable": summary["execution_reliable"],
        "research_id": research_id,
        "revision": record.get("strategy_revision"),
        "source_sha256": record.get("strategy_source_sha256"),
        "benchmark_symbols": record.get("settings", {}).get("benchmark_symbols", []),
    }


def strategies():
    """Latest completed run per strategy; no fixed project IDs or result numbers."""
    store = ResultStore()
    try:
        entries = store.list_backtests(limit=500).to_dict("records")
        seen, result = set(), []
        projects = {p["id"] for p in strategy_service.list_projects()}
        for entry in entries:
            key = (
                entry.get("strategy_project_id") or entry["strategy_id"],
                entry.get("project_strategy_id") or "main",
            )
            if key in seen:
                continue
            seen.add(key)
            raw = store.get_backtest_record(entry["id"])
            rule = frozen_rule((raw or {}).get("strategy_source") or "")
            if rule is None:
                continue
            record = result_service.get_backtest(entry["id"])
            if record is None or record.get("profile") != "runtime":
                continue
            card = _card(record, rule)
            card["project_exists"] = card["project_id"] in projects
            result.append(card)
            if len(result) == 12:
                break
        return {"strategies": result}
    finally:
        store.close()


def _compound(values):
    return math.prod(1 + float(v) for v in values) - 1


@lru_cache(maxsize=32)
def strategy_result(backtest_id):
    record = result_service.get_backtest(backtest_id)
    if record is None or record.get("profile") != "runtime":
        raise MissingDataError("Saved option strategy backtest is unavailable")
    rule = frozen_rule(record.get("strategy_source") or "")
    if rule is None:
        raise ValueError("This run has no supported frozen option-signal rule")
    card = _card(record, rule)
    nav = peak = 1.0
    benchmark_nav = 1.0
    benchmark_started = benchmark_broken = False
    curve, yearly, common = [], {}, []
    for row in record["returns"]:
        value, benchmark = row.get("value"), row.get("benchmark")
        if value is None or not math.isfinite(value):
            raise ValueError("Stored strategy returns contain a missing observation")
        nav *= 1 + value
        peak = max(peak, nav)
        valid_benchmark = benchmark is not None and math.isfinite(benchmark)
        if valid_benchmark:
            common.append(row)
            if not benchmark_broken:
                benchmark_nav *= 1 + benchmark
                benchmark_started = True
        elif benchmark_started:
            benchmark_broken = True
        curve.append(
            {
                "date": row["date"],
                "nav": nav,
                "benchmark_nav": benchmark_nav
                if valid_benchmark and not benchmark_broken
                else None,
                "drawdown": nav / peak - 1,
            }
        )
        yearly.setdefault(row["date"][:4], []).append(row)
    annual = [
        {
            "year": year,
            "start": rows[0]["date"],
            "end": rows[-1]["date"],
            "return": _compound(r["value"] for r in rows),
            "sessions": len(rows),
        }
        for year, rows in yearly.items()
    ]
    common_benchmark = _compound(r["benchmark"] for r in common) if common else None
    common_strategy = _compound(r["value"] for r in common) if common else None
    executions = record.get("executions", [])
    weights = record.get("weights", [])
    last_date = max((r["date"] for r in weights), default=None)
    positions = []
    for row in weights:
        if row["date"] == last_date and row["weight"] > 0:
            zh, en = ETF_LABELS.get(to_rq_symbol(row["symbol"]), (row["symbol"], row["symbol"]))
            positions.append({**row, "name_zh": zh, "name_en": en})
    forecast = None
    if card["research_id"]:
        try:
            report = options_research_service.read_research(card["research_id"])
            forecast = {
                k: report[k]
                for k in (
                    "id",
                    "config",
                    "scope",
                    "coverage",
                    "rank_ic",
                    "oos_r2_vs_controls",
                    "warnings",
                )
            }
        except MissingDataError:
            pass
    return {
        **card,
        "curve": curve,
        "annual": annual,
        "positions": positions,
        "positions_date": last_date,
        "common_period": {
            "start": common[0]["date"] if common else None,
            "end": common[-1]["date"] if common else None,
            "sessions": len(common),
            "total_sessions": len(curve),
            "strategy_return": common_strategy,
            "benchmark_return": common_benchmark,
            "relative_return": (1 + common_strategy) / (1 + common_benchmark) - 1
            if common and common_benchmark > -1
            else None,
        },
        "trading": {
            "rebalances": len(executions),
            "successful_trades": sum(int(e.get("successful_trade_count", 0)) for e in executions),
            "mean_turnover": sum(float(e.get("turnover", 0)) for e in executions) / len(executions)
            if executions
            else None,
            "sum_period_cost_rates": sum(float(e.get("total_cost", 0)) for e in executions),
        },
        "recent_executions": [
            {
                k: e.get(k)
                for k in (
                    "decision_date",
                    "entry_date",
                    "turnover",
                    "total_cost",
                    "successful_trade_count",
                )
            }
            for e in executions[-8:]
        ],
        "forecast": forecast,
    }
