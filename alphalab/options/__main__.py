"""Run from the repository root: python -m alphalab.options --help."""

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

from .analysis import analyze_chain, stress_grid
from .pricing import EuropeanOption, black_scholes, greeks, monte_carlo


def main():
    parser = argparse.ArgumentParser(description="Optional ETF option data and pricing extension")
    sub = parser.add_subparsers(dest="command", required=True)
    sync = sub.add_parser(
        "sync", help="Offline recipe plan by default; --execute fetches daily data"
    )
    sync.add_argument("--underlying", required=True)
    sync.add_argument("--start", required=True)
    sync.add_argument("--end", required=True)
    sync.add_argument("--overlap", type=int, default=3)
    sync.add_argument("--execute", action="store_true")
    sync.add_argument("--store", help="Shared runtime root; defaults to ALPHALAB_RUNTIME_DIR")
    health = sub.add_parser("health")
    health.add_argument("--store")
    chain = sub.add_parser("chain")
    chain.add_argument("--underlying", required=True)
    chain.add_argument("--date", required=True)
    chain.add_argument("--rate", type=float, required=True)
    chain.add_argument("--dividend-yield", type=float)
    chain.add_argument(
        "--carry-mode", choices=["input_yield", "put_call_forward"], default="input_yield"
    )
    chain.add_argument("--price-field", choices=["close", "settlement"], default="close")
    chain.add_argument("--store")
    stress = sub.add_parser("stress")
    for name in ("spot", "strike", "maturity", "rate", "volatility", "dividend-yield"):
        stress.add_argument("--" + name, type=float, required=True)
    stress.add_argument("--option-type", choices=["C", "P"], default="P")
    stress.add_argument("--quantity", type=float, default=1)
    stress.add_argument("--multiplier", type=float, required=True)
    stress.add_argument("--days-forward", type=float, default=0)
    stress.add_argument("--jump-intensity", type=float, default=1)
    stress.add_argument("--jump-mean", type=float, default=-0.15)
    stress.add_argument("--jump-volatility", type=float, default=0.15)
    stress.add_argument("--paths", type=int, default=100000)
    stress.add_argument("--seed", type=int, default=42)
    demo = sub.add_parser("demo", help="Pure mathematical example; no market data or vendor calls")
    demo.add_argument("--paths", type=int, default=100000)
    for item in (sync, health, chain, stress, demo):
        item.add_argument("--output", help="Write JSON result to this file")
    args = parser.parse_args()
    if args.command == "sync":
        from alphalab.dataio.recipes import execute_data_recipe, render_builtin_recipe

        if args.overlap < 0:
            raise ValueError("overlap must be nonnegative")
        source = render_builtin_recipe(
            "rq.etf_options", start=args.start, end=args.end, symbols=(args.underlying,)
        )
        source = source.replace("OVERLAP_SESSIONS = 3", f"OVERLAP_SESSIONS = {args.overlap}")
        result = execute_data_recipe(
            source, mode="run" if args.execute else "plan", root=args.store
        )
    elif args.command == "health":
        from alphalab.dataio.catalog import DataCatalog
        from alphalab.dataio.quality import validate_dataset

        result = {
            "catalog": DataCatalog(args.store).status("rq.option_snapshots"),
            "quality": validate_dataset("rq.option_snapshots", args.store, fail_on_gap=True),
        }
        result["status"] = result["quality"]["status"]
    elif args.command == "chain":
        from alphalab.dataio.option_data import load_option_snapshot

        frame = load_option_snapshot(args.underlying, args.date, root=args.store)
        result = analyze_chain(
            frame,
            rate=args.rate,
            dividend_yield=args.dividend_yield,
            price_field=args.price_field,
            carry_mode=args.carry_mode,
        )
    elif args.command == "stress":
        option = EuropeanOption(
            args.spot,
            args.strike,
            args.maturity,
            args.rate,
            args.volatility,
            args.option_type,
            args.dividend_yield,
        )
        result = stress_grid(
            option,
            quantity=args.quantity,
            multiplier=args.multiplier,
            days_forward=args.days_forward,
            paths=args.paths,
            seed=args.seed,
            jump_intensity=args.jump_intensity,
            jump_mean=args.jump_mean,
            jump_volatility=args.jump_volatility,
        )
    else:
        option = EuropeanOption(3, 3, 30 / 365, 0.02, 0.25, "P", 0.01)
        result = {
            "data_kind": "mathematical_example_not_market_data",
            "inputs": asdict(option),
            "bs_price": black_scholes(option),
            "greeks": greeks(option),
            "gbm": monte_carlo(option, paths=args.paths),
            "stress": stress_grid(option, paths=args.paths),
        }
    rendered = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False)
    if args.output:
        path = Path(args.output)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(rendered + "\n", encoding="utf-8")
    else:
        print(rendered)
    return 1 if result.get("status") == "failed" else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        # Provider tracebacks can include connection URLs; suppress them on the CLI.
        print(
            f"ETF options command failed ({type(exc).__name__}); check configuration, "
            "input dates and data permissions. Use the Python API for local diagnostics.",
            file=sys.stderr,
        )
        sys.exit(1)
