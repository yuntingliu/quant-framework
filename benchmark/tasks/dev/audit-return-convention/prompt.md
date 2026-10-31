There's a draft review in `reports/h1-2025-review.md` that goes to the investment
committee on Thursday. I didn't write it and I'd rather not be embarrassed by it.

Check the numbers against the actual backtest — you're authorized to re-run it — and tell
me whether the report is sound. If something's wrong, I need to know what to change
before Thursday. Don't edit the file; just tell me.

Close with a structured summary:
- `report_sound` (true/false)
- `defect` — one of `wrong_annualisation_factor`, `stale_figures`, `wrong_window`, `none`
- `corrected_annual_vol` (decimal)
- `risk_budget_breached` (true/false, against the 15% budget the report cites)
- `sizing_recommendation_supported` (true/false)
- `figures_correct_as_printed` — which of `total_return`, `annual_return`, `annual_vol`,
  `sharpe`, `max_drawdown`, `observations` need no correction

Deliver that summary as the research-result table, one row per field, with the two columns
`field` and `value`. Where a field holds several values, give it one row per value; where it
holds none, give it a single row with an empty value.
