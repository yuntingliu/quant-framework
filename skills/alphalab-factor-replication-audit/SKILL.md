---
name: alphalab-factor-replication-audit
description: Reproduce or audit published multifactor investment results from a lecture, slide, paper, or index factsheet in AlphaLab. Use when comparing annual returns, IC, factor premiums, or portfolio performance to a reference and determining whether the evidence is an exact replication or a transparent approximation.
---

# Factor replication audit

Resolve the exact page, table, period and return definition before selecting factors. Read the figure as well as extracted text: index names, footnotes and axis units often disappear in extraction.

## Separate three claims

- **Table arithmetic:** re-entering published annual returns can verify cumulative compounding, CAGR and annual benchmark win rate. It cannot independently verify the investments or reconstruct daily volatility and drawdown.
- **Empirical replication:** reproducing the source's factor/index methodology requires the same universe, point-in-time constituents and weights, original factor definitions, data vintages, rebalance rules, corporate actions, benchmark and costs. An unexplained column heading is not an index identifier.
- **Transparent approximation:** a documented local proxy is useful, but name its different universe, definitions and period. Do not present a new test with different assumptions as numerical agreement with the publication.

## Arithmetic tool

In an AlphaLab checkout with the audit command:

```powershell
python -m scripts.audit_factor_slides <annual.csv> --benchmark <column> --input-unit percent --output artifacts/replication/arithmetic.csv
```

First column contains consecutive full calendar years; remaining columns are returns. Use `decimal` for 0.10 = 10%, `percent` for 10 = 10%. The command does not backtest stocks. Report rounding tolerance from published precision. Count actual years instead of copying the source's annualization denominator.

## Research protocol

Map each column to an explicit formula or verified index code; list unresolved choices. Inspect historical coverage, delisted stocks, actual announcement/availability timestamps and revision archives. Do not use today's financial statements or membership to populate past dates.

Use the canonical SDK v1 and public facades for new runs. Keep input snapshots, configurations, commits and hashes in ignored artifacts. Existing old-engine outputs may serve as historical evidence, but label them as prior results until the current engine has independently rerun the same experiment.

Keep raw Rank IC, neutralized regression coefficients, gross long-short returns and investable net portfolio returns in separate tables. IC sign is conditional on factor direction and universe; regression coefficients are not directly tradable P&L. Correct for overlapping forward returns and multiple testing when searching factors.

Compare the same dates before comparing numbers. Report annual gaps, cumulative/CAGR gaps and methodology gaps. If quality, value or another proxy fails to reproduce a claimed effect, retain the failure; do not tune parameters on the comparison period to force agreement. A different sample's positive factor return does not establish replication.

Deliver a compact matched / approximate / unavailable table, executable commands, and the specific missing data or methodology needed to close each gap.
