"""Check local SSE 50 ETF option history before writing a backtest."""

import argparse
from datetime import date, timedelta
import json
from pathlib import Path

from .options_homework.history import load_history, load_signal_inputs


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", default="2026-10-19")
    parser.add_argument("--end", default="2026-10-23")
    parser.add_argument("--data-root", type=Path, help="AlphaLab runtime directory")
    args = parser.parse_args()

    try:
        spot, chains = load_history(args.start, args.end, args.data_root)
        prior = date.fromisoformat(args.start) - timedelta(days=7)
        signal_inputs = load_signal_inputs(prior.isoformat(), args.end, args.data_root).reindex(spot.index)
    except (OSError, ValueError) as exc:
        parser.error(f"Could not read the local options store: {exc}")

    result = {
        "first_session": spot.index[0].date().isoformat(),
        "last_session": spot.index[-1].date().isoformat(),
        "sessions": len(spot),
        "chains": len(chains),
        "missing_chain_dates": [day.date().isoformat() for day in spot.index if day not in chains],
        "sessions_with_lagged_10y_yield": int(signal_inputs.lagged_yield_10y.notna().sum()),
        "last_chain_contracts": len(chains[max(chains)]) if chains else 0,
    }
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
