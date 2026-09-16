"""Audit an annual-return CSV without claiming that table arithmetic is a backtest."""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from alphalab.analytics import audit_annual_return_table


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("annual_returns", type=Path)
    parser.add_argument("--benchmark", required=True)
    parser.add_argument("--input-unit", choices=("decimal", "percent"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    data = pd.read_csv(args.annual_returns, index_col=0)
    result = audit_annual_return_table(data / (100 if args.input_unit == "percent" else 1),
                                       benchmark=args.benchmark)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    result.to_csv(args.output)
    print(result.to_string())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
