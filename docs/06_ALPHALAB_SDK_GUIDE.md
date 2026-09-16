# AlphaLab SDK Guide

<!-- alphalab-sdk-version:1 -->

This is the canonical user guide for AlphaLab SDKs and the single content source for the workstation's documentation drawer. The stable contract version is `1`. `SDK_VERSION`, `VALIDATION_SDK_VERSION`, and report-descriptor `version` must match their respective sections.

<!-- alphalab-sdk-topic:overview -->
## SDK Overview

AlphaLab organizes research into five inspectable, persistable parts: data recipes, factors, strategies, validation, and reports.

```text
Data recipe -> Canonical research data -> Factors -> Strategy decisions -> Backtesting and validation -> Reports
```

### Core conventions

- Python source is the project's source of truth. Visual controls edit statically recognizable parameters in that source.
- Strategies and factors use `SDK_VERSION = 1`; validation uses `VALIDATION_SDK_VERSION = 1`; report descriptors use `version: 1`.
- `DATA_REQUIREMENTS` declares the datasets and fields needed before execution. Incomplete declarations are rejected during save or before execution.
- Historical reads end at the evaluation time. Factors cannot read future dates, and fundamentals must have been disclosed by that time.
- Editor Python is trusted local code, not a security sandbox. Saving performs static checks and probe runs; execution requires the user's authorization.
- Each formal run pins source revisions, hashes, data summaries, and parameters. Save changes before another run; previous runs are not rewritten.

### Recommended workflow

1. Create a project from the maintained default, including `recipe.py`, `strategies/<strategy_id>.py`, factors, and `validation.py`. Review the data plan, synchronize, and check coverage.
2. Copy a maintained factor template or generic factor skeleton in Factors. Save it, then run cross-sectional and historical checks.
3. Configure selection, weights, holding-period risk, and execution in Strategy, then inspect the preview.
4. Choose dates and validation parameters in Validation, save, and run a backtest.
5. Inspect conclusions, tables, charts, sources, and provenance in Report.

### Public interfaces

Import research interfaces from stable facades:

```python
from alphalab.sdk.v1 import (
    execution, factor, neutralize_factor_scores, optimize_portfolio, portfolio, signal,
)
from alphalab.data_sdk.v1 import data_recipe
from alphalab.validation_sdk import analysis
```

Do not import runner internals, repository implementations, or dashboard services into research code. Detailed contracts are in `docs/02_STRATEGY_SDK_V1_CONTRACT.md` and `docs/05_DATA_SDK_V1_CONTRACT.md`.

<!-- alphalab-sdk-topic:factor -->
## Factor SDK

A factor is a pure Python function returning cross-sectional values for the current universe at an evaluation time. Maintained templates contain runnable implementations and explanatory comments. Adding one creates an independent project source copy.

### Minimal factor

```python
@factor(id="momentum_20d", label="20-day momentum")
def momentum_20d(context, *, window: int = 20):
    """Return each symbol's cumulative return over window trading sessions."""
    # One extra close is needed to form exactly window return intervals.
    close = context.history("close", window=window + 1)
    if len(close.index) < window + 1:
        # Preserve the symbol index and mark insufficient history explicitly.
        return close.mean(axis=0) * float("nan")
    return close.iloc[-1] / close.iloc[0] - 1.0
```

### Registration and parameters

- The `id` in `@factor(id=..., label=...)` is unique within the project and is used by `context.factor(...)`.
- The first parameter must be `context`. User parameters are keyword-only with literal defaults.
- Ordinary `int`, `float`, `str`, and `bool` parameters are editable in the UI. Use `Annotated[..., Parameter(...)]` for bounds and step sizes.
- Dependencies must be static calls such as `context.factor("quality")`. Missing dependencies and cycles are checked.

### Available data

- `context.history(field, window=..., symbols=...)` returns a matrix ending at the evaluation time, indexed by date with symbol columns.
- `context.fundamental(field)` returns the latest cross-section visible at that time.
- `context.factor(id, **parameters)` reuses another registered factor in the project.
- `context.combine_factors(weights=..., normalization=..., parameters=...)` combines factors using `raw`, `rank`, or `zscore` normalization.
- `neutralize_factor_scores(values, exposures)` residualizes using exposures visible at the same evaluation time and returns sample, coefficient, and fit diagnostics.

Declare the required fields in the module:

```python
DATA_REQUIREMENTS = {
    "bars": ["close", "volume", "amount"],
    "fundamentals": ["roe", "bp"],
}
```

### Return values and missing data

Return a one-dimensional `pandas.Series` indexed by symbol. Observed values must convert to finite floats. Keep `NaN` for symbols that cannot be evaluated; do not replace them with zero. Insufficient history, zero denominators, and missing inputs should produce explicit missing values so coverage and filtering remain informative.

### Evaluation sequence

Market search accepts names, partial codes, and full codes. It supports both `000001.SZ` and RQData's `000001.XSHE`; Shanghai `.XSHG` and Beijing `.XBEI` normalize to `.SH` and `.BJ`. The UI and research data use normalized codes. Invalid formats produce an unsupported-code error. For a valid code without local bars, check the symbol or synchronize it in Data.

Save first. Use the cross-sectional check to inspect ranking and coverage, then historical distribution to inspect availability over time. Research evidence uses forward returns beginning at the next session's open and reports Rank IC, ICIR, Newey-West t statistics, bootstrap intervals, 1/3/6-period decay, quantile returns, and a 70/30 chronological holdout. Missing prices are excluded pairwise rather than filled with zero.

Factor direction belongs to the strategy interpretation. Momentum and profitability commonly favor high values; volatility, leverage, and short-term reversal templates may favor low values.

<!-- alphalab-sdk-topic:strategy -->
## Strategy SDK

Select the intended named strategy before editing or running it. Strategies share project factors and the data recipe, with independent strategy source and run history.

A strategy combines the universe, factors, rebalance signals, target holdings, holding-period events, and execution assumptions in one event-driven flow. Visual settings update the canonical Python source.

### Execution order

```text
@universe -> @factor -> @signal -> @portfolio -> @on_event -> @execution
                                                        -> @execution_data_fill (when an order is attempted)
```

- `@universe` returns `UniverseResult` and defines the current research scope.
- `@signal` runs on a `Daily`, `Weekly`, or `Monthly` schedule and returns `SignalResult`.
- `@portfolio` maps selected symbols to `PortfolioDecision.target_weights`.
- `@on_event` reads and updates persistent `state` on open, close, decision, fill, or rejection events.
- `@execution` returns `ExecutionPolicy` with activation time, fees, slippage, capacity, and fallback candidates.
- `@execution_data_fill` receives and returns a market-state `DataFrame`. Its implementation is editable in `strategies/<strategy_id>.py`.

### Main return objects

```python
return SignalResult(selected=selected, scores=scores, state=state)

return PortfolioDecision(
    target_weights={symbol: weight for symbol in selected},
    state=state,
    reason="monthly_rebalance",
)

return ExecutionPolicy(
    activation="next_session_open",
    commission_rate=0.00025,
    slippage_rate=0.00020,
    max_participation_rate=0.10,
)
```

`SignalResult.selected` determines candidate order; `scores` retains explanatory values. `PortfolioDecision` describes targets, and `ExecutionPolicy` controls fill attempts under market constraints.

The SDK also provides explicit portfolio optimization:

```python
history = context.history("close", window=121, symbols=signal.selected)
returns = history.pct_change().dropna()
optimized = optimize_portfolio(
    returns,
    method="risk_parity",  # equal_weight / minimum_variance / hrp / max_sharpe
    max_weight=0.20,
    current_weights={holding.symbol: holding.weight for holding in context.portfolio.positions},
    max_turnover=0.30,
)
return PortfolioDecision(
    target_weights=optimized.weights,
    diagnostics=dict(optimized.diagnostics),
    state=state,
)
```

`max_sharpe` requires explicit annualized `expected_returns`. Insufficient samples, infeasible constraints, or nonconvergence raise `PortfolioOptimizationError` without an equal-weight fallback. The default project remains equal-weight until `@portfolio` is explicitly changed.

Missing-state handling is editable project Python. A simplified example is:

```python
@execution_data_fill(id="fill_missing_market_state")
def fill_missing_market_state(
    context,
    rows,
    *,
    main_board_limit_rate=0.10,
    star_market_limit_rate=0.20,
    chinext_limit_rate=0.20,
    etf_limit_rate=0.10,
    ipo_unlimited_sessions=5,
):
    filled = rows.copy()
    symbols = list(filled["symbol"])
    previous_close = context.history("raw_close", window=1, symbols=symbols).iloc[-1]
    missing_up = filled["limit_up"].isna()
    missing_down = filled["limit_down"].isna()
    filled.loc[missing_up, "limit_up"] = filled.loc[missing_up, "symbol"].map(
        (previous_close * (1.0 + main_board_limit_rate)).round(2)
    )
    filled.loc[missing_down, "limit_down"] = filled.loc[missing_down, "symbol"].map(
        (previous_close * (1.0 - main_board_limit_rate)).round(2)
    )
    return filled
```

The maintained default additionally reads previous-session `is_st`, instrument type, and listing date to distinguish main-board, ST, ChiNext, STAR, Beijing, ETF, and initial-listing cases. Parameters are exposed in Strategy. An initial session without price limits is represented by the pair `limit_up == limit_down == 0`; one-sided zero is rejected. Nonpositive provider values are first treated as missing.

Suspension state defaults to the most recent known value within 120 historical sessions. If the entire window is missing, an editable template switch can fill it as not suspended.

Existing projects are not silently rewritten. Explicit migration updates missing default-universe and market-state-fill components as a new version, retaining historical versions and runs.

### Timing and tradability

- A close-generated signal cannot normally fill at the same close. Official templates use `next_session_open`.
- `context.universe` contains all instruments valid at runtime and does not imply stock-type filtering. New projects copy `sdk-v1-default`, whose `research_universe` filters `context.instruments()` for `asset_type == "CS"`. For an explicitly requested fixed, index, sector, or ETF scope, the Agent replaces that registered function before the initial save.
- Strict execution does not empty the signal universe in advance because market state is missing. The project's `@execution_data_fill` runs when an actual order reaches execution, followed by suspension and price-limit checks.
- The fill function may fill only missing `is_suspended`, `limit_up`, and `limit_down` values. It cannot overwrite known values or prices. Context history ends before the fill date. Ordinary price-limit estimates use prior-session unadjusted `raw_close`; open/close constraints compare current unadjusted `raw_open`/`raw_close`. Remaining missing values reject the order.
- Untradable holdings remain held; a confirmed delisting date permits a zero-value write-off.
- Participation and market impact depend on turnover amount. Inspect execution audits and warnings when data is missing; an unfilled order is not a zero-return trade.

Run summaries and terminal task results expose warnings, research validity, attempted and successful fills, filled-state counts, and missing-state rejections. Signal summaries retain counts, coverage, turnover, and rank IC to the next rebalance; full symbol scores remain inside the backtest process. Robustness annualization follows the frequency of return dates rather than the rebalance schedule.

### State

`state` is a serializable dictionary carried across events. Return updated profit locks, peak returns, or cooldown state through the return object. Do not keep backtest state in module globals. Holdings come from `context.portfolio`; do not maintain a second holdings ledger in `state`.

### Save and preview

Saving checks decorators, parameters, dependencies, data requirements, and minimal probe execution. Preview shows scores, selected symbols, target weights, event results, and execution policy. Formal backtests use saved revisions.

### Default equal weights and cash

The monthly momentum signal's `top_n` limits selected symbols. The equal-weight portfolio's separate `max_weight` caps each target weight; it defaults to `0.10`:

```python
weight = min(max_weight, 1.0 / len(selected)) if selected else 0.0
```

| Selected symbols | Per-symbol cap | Each target weight | Target cash |
| --- | --- | --- | --- |
| 10 | 10% | 10% | 0% |
| 8 | 10% | 10% | 20% |
| 8 | 12.5% | 12.5% | 0% |

For eight equal-weight symbols with no target cash, set the cap to `0.125`, apply settings, and preview again. This is a parameter example: changing selection count does not automatically change the cap. Actual cash also reflects fees, suspensions, price limits, and fill capacity.

<!-- alphalab-sdk-topic:data -->
## Data Recipe SDK

A data recipe is a complete editable Python synchronization program. Built-in templates call `rq.*` directly and use `context` to plan, synchronize incrementally, normalize, and publish. The backend does not substitute logic based on the template name.

### Structure

```python
from alphalab.data_sdk.v1 import data_recipe, rq


@data_recipe(id="research_data", label="Research data", template="rq.a_share_research")
def research_data(context, *, start: str, end: str, symbols=None):
    """Declare a plan or publish data into the canonical research store."""
    if context.mode == "plan":
        context.expect("rq.bars", "rq.get_price", start=start, end=end)
        return

    raw = rq.get_price(symbols, start_date=start, end_date=end)
    # A real recipe must normalize provider output with the SDK's normalize_* helpers.
    context.publish("rq.bars", raw)
```

### Plan and run

- In `context.mode == "plan"`, use `context.expect(...)` without provider requests or database writes.
- Run mode can call provider APIs and publish with `context.publish(dataset, frame)`.
- `context.sync_batches(...)` plans incremental requests from watermarks, listing dates, batch sizes, date chunks, and overlaps.
- `context.watermark(...)` reads progress. Only `force=True` restarts the specified range.
- `context.require_coverage(..., fail_on_gap=True)` checks date and symbol coverage before completion.

### Data boundaries

Normalization handles symbols, dates, field names, and units. Preserve research prices alongside unadjusted `raw_open`, `raw_high`, `raw_low`, and `raw_close`. Align suspension/ST data by symbol and date. Financial statements must retain disclosure information for point-in-time fundamentals. Never backfill historical dates using financial values known only at the backtest end.

### Templates and custom copies

Switching a built-in template replaces editor source. Save modifications as a custom template. `template=...` identifies its origin to the workstation and does not dispatch execution. Consult the RQData documentation linked from Data for provider parameters.

### Dates and local coverage

The synchronization end date can be today to extend beyond the last local date. Saved recipes retain their end date until edited. The connection test reports the provider's latest session but downloads no bars. Date changes update `recipe.py`; Run and Sync extends local coverage. Factor checks and backtests remain bounded by synchronized data.

<!-- alphalab-sdk-topic:validation -->
## Validation SDK

Validation receives completed backtest data and produces JSON-serializable metrics and attribution. Its editable source belongs to the project and is versioned.

### Select and run a project

Select the project in Validation and Backtest configuration. This selection is shared across workbenches; its `validation.py` and historical runs are loaded. Save code and parameters before switching. Unsaved changes require discard confirmation. Selecting historical results does not reset the current validation source or dates.

A backtest uses the selected project's saved strategy and validation source. Editing source does not change old results; run again to evaluate a change.

### Analysis entrypoints

```python
from alphalab.validation_sdk import ValidationContext, analysis

VALIDATION_SDK_VERSION = 1


@analysis(id="performance", label="Returns and drawdowns")
def performance(context: ValidationContext, *, periods_per_year: int = 252) -> dict:
    """Compute the metrics displayed for the strategy's period returns."""
    returns = context.returns.dropna().astype(float)
    # Outputs must contain only JSON-compatible scalars, lists, and dictionaries.
    return {"n_periods": int(len(returns))}
```

`context` supplies strategy and benchmark returns, positions, fills, factor returns, settings, and read-only `diagnostics` from the same frozen run. Validation cannot alter fills or accounting. The maintained module registers:

- `performance`: total and annualized returns, annualized volatility, Sharpe, and maximum drawdown.
- `research_quality`: editable gates returning `passed`, `reasons`, `warnings`, thresholds, and evidence.
- `alpha_beta`: monthly CAPM, multifactor regression, Newey-West errors, correlations, and warnings.
- `risk`: historical VaR/CVaR, downside volatility, drawdown duration, concentration, effective holdings, and turnover.

Quality diagnostics include signal IC, coverage, execution fidelity, and `evidence_status` (`pass/fail/insufficient`). By default, signal evidence is explanatory; `passed` and `status` follow the project's gates. Enabling `require_signal_evidence=True` makes insufficient or failing evidence a quality failure. Passing does not establish profitability.

`context.diagnostics` provides copies of frozen execution checks and compact signal evidence. `context.executions` contains actual fills, target deviations, and rejections for each rebalance. Mutating these copies cannot change the engine or historical results.

### Execution and research validity

`execution_reliable` records whether execution-data checks passed, not absolute provider accuracy or profitability. `research_valid` comes from the frozen `research_quality` function. Deviations caused by ordinary suspensions or price limits are warnings by default.

For strict target tracking, enable `require_target_tracking=True` or `require_successful_exits=True` in `validation.py` and adjust thresholds and consecutive counts. Counts refer to rebalance observations, not daily holdings.

If an older project lacks `research_quality`, a new run returns `research_valid=null`. The user or Agent can explicitly add it while preserving other custom validation source. Existing frozen results retain their original assessment.

Default `strategies/<strategy_id>.py` attempts execution once at the next open. Failed buys leave cash; failed sells retain holdings until a new explicit decision. `fallback_candidates=()` means no alternatives; it can be replaced with an ordered list determined in advance. Cross-session retries require new targets from `@on_event`. The engine does not automatically retry or redistribute cash.

### Parameters and outputs

Validation parameters are keyword-only with literal defaults that the UI can project back into Python. Outputs cannot contain `DataFrame`, `Series`, NumPy scalars, NaN, or infinity. Convert them to ordinary `dict`, `list`, `int`, finite `float`, `str`, `bool`, or `None`.

### Interpretation

Retain warnings for small samples, poor coverage, rank-deficient regressions, and benchmark gaps. Insufficient tail-risk samples return `None` and `insufficient`; zero does not mean unknown. Historical Validation reads frozen named outputs instead of rerunning current source.

A research candidate in robustness analysis is not a live-trading commitment. Evaluate holdouts, cost sensitivity, turnover, concentration, and multiple-testing adjustments together.

<!-- alphalab-sdk-topic:report -->
## Report Result Protocol

The Report Workbench accepts versioned research results: Markdown documents with optional tables, charts, sources, and provenance.

### Minimal report

```json
{
  "version": 1,
  "reportId": "momentum-study-20260901",
  "kind": "document",
  "title": "Momentum factor study",
  "markdown": "# Findings\n\nDescribe the findings and limitations here.",
  "sources": []
}
```

`reportId` must be stable and unique. A result with the same ID replaces the previous workspace version. Markdown is sanitized; do not depend on scripts or unsafe HTML.

### Tables and charts

Each `table.columns` entry needs a unique `key`, display `label`, and format: `text | number | percent | date | datetime`. Each row supplies every column with a string, finite number, boolean, or `null`. The workbench supports filtering, sorting, and CSV export.

Each chart needs a unique `id`, `type`, `title`, `xKey`, `series`, and `rows`. Types are `line`, `bar`, `area`, `scatter`, and `pie`; series formats are `number` and `percent`. A pie chart has exactly one numeric series.

### Sources and provenance

- `sources` records data, websites, or internal documents; describe methodology in the report itself.
- `profile` is the current `runtime` data environment.
- `backtestId`, `runId`, `artifactId`, and `provenance` trace findings to runs, source, and data.
- State dates, benchmark, costs, missing-data handling, limitations, and limits to generalization.

Use tables and charts for interactive data and Markdown for conclusions, evidence, methods, and risks. All should come from the same result set.

### Technical evidence and lecture audits

`alphalab.analytics` provides `TechnicalMetadata`, `technical_evidence`, `render_technical_evidence`, and `audit_annual_return_table` for evidence and result audits. These do not generate orders or replace SDK backtests.

Technical evidence uses data through `as_of` and requires a consistent OHLC adjustment basis. Unknown indicators return `None` without historical imputation. Single-day TR and Wilder ATR(14) are separate; Bollinger(20,2)'s middle band and MA20 use the same series.

Export local CSV/parquet evidence or audit tables with:

```powershell
python -m scripts.technical_review bars.csv --symbol STOCK --market SSE --currency CNY --price-basis unadjusted --volume-unit shares --source local-snapshot --as-of 2026-09-04 --output artifacts/technical-review
python -m scripts.audit_factor_slides annual.csv --benchmark benchmark --input-unit percent --output artifacts/replication/audit.csv
```

Technical input contains `date,open,high,low,close,volume` using exchange daily dates. The caller checks session continuity. A benchmark requires a file, name, source, and price basis. Comparisons use matching endpoints without forward-filling missing benchmark prices.

Annual audit input starts with consecutive complete years and contains one return series per column. Recalculating a published annual table checks arithmetic; it does not establish independent stock-level replication.

Editable templates `lower_shadow_recovery`, `three_white_soldiers`, and `volume_confirmed_breakout` appear in the factor catalog. They return 0/1 patterns and NaN for missing inputs. The first two use explicit shadow/body ratios. Breakouts compare against the preceding 20-session high and mean volume. These are candidate filters without return-optimized defaults or established win rates. Unadjusted prices can create false patterns across corporate actions; verify the research price basis.

`skills/alphalab-technical-evidence-review` describes technical reviews; `skills/alphalab-factor-replication-audit` covers lecture, paper, and index comparisons. Copy a skill folder into a user's Codex skills directory to install it elsewhere; this repository retains the versioned source.

### Stock-level quality-portfolio replication

`python -m scripts.research_quality_replication` freezes existing local RQ caches and runs fixed comparisons through canonical SDK project saves and the current event engine.

```powershell
python -m scripts.research_quality_replication prepare --data-root C:/path/to/legacy/data --snapshot artifacts/quality/snapshot --start 2018-12-28 --end 2020-12-31
python -m scripts.research_quality_replication run --snapshot artifacts/quality/snapshot --output artifacts/quality/runs --start 2018-12-28 --end 2020-12-31
```

Inputs include raw daily bars, statements, instruments, daily factors, historical sectors, and index constituents under `rq`, plus historical adjustment factors and coverage manifests under `sector_rotation/rq`. Outputs retain daily returns, actual weights, fills, source, and input fingerprints. Include the preceding year's last session so the first month-end signal can activate at the next year's open.

Financial processing uses original disclosures, visibility no earlier than the next day, and exact-quarter TTM matching. Four observations are not assumed to be four consecutive quarters. `roe` uses average positive equity from the current and year-earlier periods; `roe_latest_equity` retains closing-equity ROE for comparison. `roa` uses corresponding average total assets with TTM profit attributable to parent shareholders, making it an explicit research proxy.

Missing history, late-disclosed dependencies, negative equity, and adjusted-only quarters are excluded from valid quality observations. These rules apply to regenerated fundamentals; old caches require explicit rebuilding.

The `quality_profitability` template equally combines cross-sectional ranks of ROE, ROA, and low leverage, excluding missing or infinite inputs. Define the point-in-time universe before ranking. It has no profitability-stability component and is not equivalent to MSCI, CSI, or the lecture's unspecified quality index. Out-of-sample timing or machine-learning benefits have not been established.

The experiment selects the top 50 at month-end, executes at the next session's open, and separates equal weighting from total-market-cap weighting. SDK fractional weights omit a 100-share-lot, minimum-commission, and stamp-duty account ledger; capacity notional is not actual initial cash. Do not describe results as realizable returns for a CNY 200,000 account.

Historical constituent caches are monthly. Saved `execution_data_fill` rules estimate missing price limits. Disclose both limitations: passing strict execution checks does not turn estimates into observed provider data.
