"""Generate technical evidence from local CSV/parquet; run as python -m scripts.technical_review."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from alphalab.analytics import TechnicalMetadata, render_technical_evidence, technical_evidence


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bars", type=Path)
    for name in ("symbol", "market", "currency", "price-basis", "volume-unit", "source", "as-of"):
        parser.add_argument(f"--{name}", required=True)
    parser.add_argument("--benchmark", type=Path)
    parser.add_argument("--benchmark-symbol")
    parser.add_argument("--benchmark-source")
    parser.add_argument("--benchmark-price-basis")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    def read(path: Path) -> pd.DataFrame:
        return pd.read_parquet(path) if path.suffix.lower() == ".parquet" else pd.read_csv(path)

    metadata = TechnicalMetadata(args.symbol, args.market, args.currency, args.price_basis,
                                 args.volume_unit, args.source)
    benchmark_metadata = None
    if args.benchmark is not None:
        if not all((args.benchmark_symbol, args.benchmark_source, args.benchmark_price_basis)):
            parser.error("Benchmark requires --benchmark-symbol, --benchmark-source, --benchmark-price-basis")
        benchmark_metadata = TechnicalMetadata(args.benchmark_symbol, args.market, args.currency,
                                               args.benchmark_price_basis, "not_used", args.benchmark_source)
    report = technical_evidence(read(args.bars), as_of=args.as_of, metadata=metadata,
                                benchmark=read(args.benchmark) if args.benchmark else None,
                                benchmark_metadata=benchmark_metadata)
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "evidence.json").write_text(json.dumps(report, ensure_ascii=False, indent=2,
                                                       allow_nan=False), encoding="utf-8")
    (args.output / "evidence.md").write_text(render_technical_evidence(report), encoding="utf-8")
    print(f"{report['metadata']['symbol']}: {report['observations']} bars through {report['observed_as_of']}; {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
