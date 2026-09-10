---
name: alphalab-technical-evidence-review
description: Produce or audit an evidence-based daily technical review for a named stock, ETF, or index using OHLCV, candlestick structure, benchmark regime, and sector/theme context. Use when the user wants a technical-analysis report, an audit of quoted indicators, or testable technical screening rules in AlphaLab.
---

# Technical evidence review

Use technical structure to organize observations and conditional decisions. Distinguish measured facts, interpretations, and untested predictions.

## Establish the observation

- Resolve ticker, exchange, currency, session date/time, frequency, price adjustment, volume units, and benchmark identity. A past-date review uses only information available then; later outcomes belong in a separate validation.
- Prefer an existing local, dated data snapshot. Inspect freshness/coverage before requesting more data. For mixed stocks, select one symbol before calculating. Supply exchange session dates and consistent OHLC adjustments; never combine adjusted close with raw high/low.
- Verify quoted values against the source. Do not confuse shares with currency turnover, daily change with an N-session return, the stock's return with the index's, or true range with smoothed ATR. Do not silently repair conflicting vendor observations by averaging them.

## Compute before interpreting

In an AlphaLab checkout with `scripts/technical_review.py`, run from that checkout:

```powershell
python -m scripts.technical_review <bars.csv> --symbol <ticker> --market <exchange> --currency <ISO-code> --price-basis <basis> --volume-unit shares --source <source-reference> --as-of YYYY-MM-DD --output artifacts/technical-review
```

Input fields: `date,open,high,low,close,volume`. CSV or parquet is accepted. Dates are exchange-session dates without a timezone component. Add `--benchmark <csv> --benchmark-symbol <name> --benchmark-source <reference> --benchmark-price-basis <basis>` for benchmark comparisons; benchmark needs `date,close`, in the asset's currency. Record conversions explicitly. If the checkout lacks this command, compute with the same disclosed conventions and do not imply the tool ran.

Read `evidence.json` including warnings and recent observations, not just the last row. The calculator returns unavailable long averages and does not verify the exchange calendar. Prefer at least 300 historical sessions; disclose limited initialization for recursive indicators. Vendor RSI/MACD/ATR can differ by seed/smoothing. Compare identical definitions before calling a mismatch an error. OBV level depends on the input start and is not institutional holdings or net cash flow.

## Form the report

1. Lead with the observed structure and its strongest contrary evidence. Describe trend, momentum, volatility, volume and candle location with actual numbers and dates. Several correlated oscillators are not independent confirmations.
2. Add benchmark trend/relative returns and sector or theme breadth when dated data exists. Broad-market sentiment, historical theme membership and hotness require their own sources. Mark unavailable context as unknown; never substitute a story for missing data.
3. Label support/resistance as candidate zones with a derivation (prior swing, rolling high/low, MA or volume profile). Do not claim dense volume-at-price from ordinary daily volume. Distinguish confirmed pivots from pivots requiring future bars.
4. Give conditional upside/neutral/downside scenarios, observable triggers and invalidation. Avoid precise horizons, targets or probability unless backed by a specified historical test. A long lower wick can suggest rejection of lower prices; it does not prove accumulation or a washout.
5. Attach source dates, units, conventions and the input fingerprint; list the few missing inputs that materially constrain the conclusion.

## Turn it into research when requested

The SDK catalog includes `lower_shadow_recovery`, `three_white_soldiers` (explicit proxy), and `volume_confirmed_breakout`. Their editable definitions are research starting points. Use them as candidate filters alongside valuation/quality and dated regime/theme features. Do not turn their binary scores into predicted returns without evaluation.

Use the existing SDK v1 factor/signal/portfolio/execution path, with close-time signals activated next session. Model A-share T+1, lot sizes, halts, price limits and costs where applicable. Do not silently rewrite a user's project or add another backtester.

For ML, use chronological walk-forward splits and purge overlapping labels. Fit preprocessing, feature selection and calibration inside training windows; reserve an untouched test period and report incremental benefit against simple rules after turnover and costs. Treat daily re-evaluation as a maximum opportunity, not a requirement to trade every day. Chart patterns and Elliott-wave labels must have causal, reproducible definitions before training.
