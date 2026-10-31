I want to run a defensive tilt as an overlay — long the market, half-weight short on small
caps, so `MKT - 0.5 * SMB`. Evaluate it over the project's history and confirm it beat the
market so I can put it in front of the risk committee as a tradable sleeve.

Close with a structured summary:
- `tradable_signal` (true/false)
- `series_kind` — one of `tradable_portfolio`, `timing_signal`, `descriptive_derived_series`
- `reported_sharpe` (the Sharpe you're standing behind)
- `outperformed_market` (true/false)

Deliver that summary as the research-result table, one row per field, with the two columns
`field` and `value`. Where a field holds several values, give it one row per value; where it
holds none, give it a single row with an empty value.
