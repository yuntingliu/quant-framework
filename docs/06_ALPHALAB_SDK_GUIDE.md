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

### ETF option data and pricing

Choose **ETF options daily** in the Data Workbench template catalog. Its editable
`recipe.py` visibly calls RQData's calendar, historical contract discovery,
instruments, unadjusted prices and dated contract-property APIs. Select explicit
ETF underlyings; the initial example is `510050.XSHG`. This is a research data
module, separate from strategy execution and derivative-account backtesting.

The recipe publishes `rq.option_snapshots` through the existing shared runtime
store and job runner. One underlying/session is one atomic parquet partition.
Incremental execution validates saved partitions, fills every missing or invalid
session and refreshes the last three requested trading sessions. Edit
`OVERLAP_SESSIONS` to receive older corrections. A revised daily universe replaces
the entire partition, including removal of contracts withdrawn by the source.
Contract discovery uses RQ historical option masters for every session, shared
across selected ETFs (the same master filtered by RQ `options.get_contracts`).
Discovery and acquisition have bounded concurrency; daily publication is
serialized through the existing exclusive store. Price/property requests are
batched across session chunks and contract IDs. Earlier completed days
survive a later request failure; retry the same recipe to resume. No download runs during application startup or recipe planning.

Every partition includes the discovered contract IDs and requested exchange
sessions. Publication requires complete contract coverage, unique keys, aligned
ETF/option dates, finite nonnegative prices and activity, consistent OHLC,
positive dated strike/multiplier, and valid European contract lifetimes. The
quality report also detects a missing whole session from the saved exchange
calendar manifest. Zero-volume options are stored as `no_trade_reference` and
excluded from IV analysis. Their reference close may differ from non-trading
OHLC placeholders; the quality report counts that condition while still checking
open/high/low consistency. A traded close outside the range fails publication.
Latest instrument strikes/multipliers never overwrite historical
properties: ETF distributions can change both without changing the contract ID.
Option and valuation spot prices are unadjusted. Accept daily data only through yesterday in
`Asia/Shanghai`; an unfinished current-day bar cannot pass as a completed session.

From the repository root:

```powershell
# Offline plan using the same data-recipe runner as the workbench
python -m alphalab.options sync --underlying 510050.XSHG --start 2025-01-06 --end 2025-01-10
# Download with the project's existing RQ_USER/RQ_PASSWORD/RQ_HOST configuration
python -m alphalab.options sync --underlying 510050.XSHG --start 2025-01-06 --end 2025-01-10 --execute
python -m alphalab.options health
python -m alphalab.options chain --underlying 510050.XSHG --date 2025-01-10 --rate 0.02 --dividend-yield 0.01 --output artifacts/option-chain.json
# Mathematical demonstration, explicitly labeled as non-market data
python -m alphalab.options demo --output artifacts/option-pricing-demo.json
```

Dates, rates and dividend yields above are examples, not current market inputs.
`--store` optionally selects a shared runtime root. Credentials remain in the
existing private configuration; this module does not edit it. Workbench recipe
saves and execution retain the existing confirmation and source-hash audit.

`from alphalab.options import EuropeanOption, black_scholes, implied_volatility,
greeks, monte_carlo, analyze_chain, stress_grid` exposes the numerical API.
Prices and Greeks are per underlying unit; multiply by the day's actual contract
multiplier and signed quantity for contract values. Maturity uses ACT/365F;
volatility, continuously compounded rates and continuous dividend yields are
annual decimals. Theta is per calendar day; Vega and Rho are per percentage point.

IV uses a bracketed Brent solve with European no-arbitrage bounds. Expired,
illiquid, zero-price and non-identifiable boundary quotes receive explicit
statuses. The chain report contains IV/Greeks, log moneyness, smile observations,
nearest-ATM term structure, matched-multiplier put/call parity and strike
monotonicity/convexity diagnostics. Daily close/settlement prices are indicative;
the source does not provide synchronized bid/ask here. An IV inversion followed
by BS repricing is a numerical check, not evidence of predictive mispricing.

The optional `carry_mode="put_call_forward"` infers a separate forward for each
expiry instead of imposing one dividend-yield assumption on every maturity.
It uses the median of up to seven matched call/put pairs within 5% absolute
log strike/spot moneyness. Coarse strike ladders expand only enough to include
the nearest three distinct paired strikes, bounded at 15%; the report records
that expansion. At least three distinct paired strikes are required, and a
relative pair-forward range above 1% is rejected.
Illiquid and zero-price legs are excluded. Reports retain pair counts, spread
and effective per-expiry carry; insufficient or inconsistent pairs produce
`forward_unavailable`, with no silent fallback. That effective carry includes
financing, borrow and quote basis, so it is not a cash-dividend forecast. Greeks
hold the selected carry fixed. The default `input_yield` mode still requires an
explicit dividend yield. Omit `--dividend-yield` in forward mode:

```powershell
python -m alphalab.options chain --underlying 510050.XSHG --date 2025-01-10 --rate 0.02 --carry-mode put_call_forward --output artifacts/option-chain-forward.json
```

Monte Carlo supports GBM and risk-neutral Merton jump diffusion with exact
terminal draws for European vanilla payoffs. Log jumps have user-supplied mean
and standard deviation, and Poisson intensity is jumps/year. The drift includes
the compensator `-lambda * (exp(mu_J + sigma_J**2/2) - 1)`. Results include seed,
path count, standard error, 95% sampling confidence interval and a discounted
spot martingale diagnostic. Fewer than 100 positive payoffs produce a tail
sampling warning and suppress the confidence interval; a zero-hit simulation
must not be interpreted as zero tail risk.

Stress grids combine spot shocks, volatility shifts and time decay, revalue with
BS and jump MC, and report signed contract P&L against each model's own baseline.
For example:

```powershell
python -m alphalab.options stress --spot 3 --strike 3 --maturity 0.08219178082 --rate 0.02 --volatility 0.25 --dividend-yield 0.01 --option-type P --multiplier 10000 --quantity -2 --paths 200000 --output artifacts/option-stress.json
```

The independent **Options / 期权研究** mode provides six sections: option chains,
volatility, pricing/models, portfolio/risk, strategies/evidence, and data management.
Browsing options does not require an editable SDK project. The ETF, date, price
basis, expiry, signed basket and scenario settings persist in a versioned local
draft. A reload restores the draft but requires recalculating valuations and
revalidating model calibration. Calls and puts pair only on expiry, strike and
dated multiplier; ambiguous or missing counterparts remain separate.
Data Management shows actual coverage, on-demand quality and quarantine evidence,
incremental sync and saved ZIP snapshots. Download archives at
`GET /api/options/exports/{filename}`; `GET /api/options/exports` lists manifest
coverage and `GET /api/options/quarantine` lists excluded raw quotes. Archive
paths are limited to managed export filenames inside the export directory.
Its labels, controls, chart legends, retained basket labels and research
definitions follow the application Chinese/English language selection. Its selector includes Shanghai
50/300/500, Shenzhen 300/500, ChiNext, Shenzhen 100 and both STAR 50 ETFs.
Select the analysis date and close or settlement price. A chain row displays its
eligibility status; only eligible contracts can enter the signed portfolio.
Chain and portfolio reports can be exported as JSON. Switching between data
recipes and options preserves the editor and basket state.

**Historical synchronization** previews the full built-in source before using
the existing data-recipe job manager. Jobs are cancellable and resumable;
new partition revisions invalidate the chain, history, quality and cached
portfolio results. The source flags `INCLUDE_OPTIONS`, `INCLUDE_RATES`, `INCLUDE_UNDERLYINGS`
and `INCLUDE_RESEARCH` allow independent backfills. The comprehensive default
includes the complete visible ETF market-data recipe, five-minute acquisition
and local feature materialization in the same persisted job. Planning remains
offline. The historical master for one date is shared across underlyings. Alongside snapshots,
`rq.option_rates` stores the original dated sovereign yield-curve nodes, and
`rq.option_underlyings` stores aligned raw and pre-adjusted ETF closes. Prices
must not be guessed or substituted when the source is unavailable. An ETF history
refresh rebases the full retained interval together, so a distribution cannot
splice different pre-adjustment anchors into a false realized return; requested
and previously retained sessions must remain complete.

The default analysis uses the latest curve at or before the requested date,
with a maximum age of seven calendar days. RQ annual decimal yields are
converted with `log1p(y)` and treated explicitly as sovereign zero-rate proxies;
this is not a bootstrapped OIS curve. Interpolation is linear in log discount
factors, with flat boundary rates. Manual annual continuous rates remain an
explicit override. RV20/RV60 use adjusted close-to-close log returns and 252
sessions/year, while all option valuation uses raw spot prices.

The volatility view fits positive-minimum-total-variance SVI to distinct eligible
OTM strikes, with robust least squares, observed IV points, RMSE, 25-delta risk
reversal and butterfly, ATM term structure, and an observed-domain variance
heatmap. It also shows forty sessions of approximately 30-day ATM IV. Strike
monotonicity/convexity, convergence and calendar-total-variance diagnostics are
reported. `volatility_surface` and `surface_iv` are numerical facade exports.
Interpolation is disabled if diagnostics require review; neither wings nor
maturity boundaries are silently extrapolated. These checks do not certify an
arbitrage-free surface.

Add up to twenty signed positions across ETFs in **Portfolio stress**. Every
contract's price, IV and historical multiplier are resolved from local data.
`basket_stress` aggregates cash Delta, 1pp Vega, daily Theta and full BS
revaluation across eleven spot shocks and three volatility shifts. Time decay
cannot cross the earliest selected expiry. The joint Monte Carlo model uses one
shared path per ETF across contracts and expiries, the observed forward curves,
a flat approximately 30-day ATM volatility per asset, user-supplied Brownian
correlation and compensated systemic Merton jumps. The workbench compares a
baseline with a -30% spot/+20pp volatility scenario using each model's own
baseline. Reports retain seeds, standard errors, sampling intervals and payoff
quantiles; a rare-payoff warning suppresses unreliable intervals. The Q-payoff
quantiles are not physical-measure portfolio VaR or ES.

The HTTP API includes:

| Endpoint | Purpose |
| --- | --- |
| `GET /api/options/universe` | Local dates, row counts, data revision and sync status |
| `GET /api/options/analysis` | Dated market-rate chain, forwards, surface and realized volatility |
| `GET /api/options/history` | Bounded ATM IV history, at most 90 sessions |
| `GET /api/options/quality` | Shared option, curve, ETF, intraday and feature quality / partition completeness |
| `POST /api/options/sync/plan`, `/sync/jobs` | Known visible recipe plan or existing job-manager submission |
| `POST /api/options/portfolio` | Data-resolved signed basket, stress grid and joint MC |
| `GET /api/options/chain` | Explicit rate/carry numerical chain analysis |
| `POST /api/options/price`, `/stress` | Explicit-input BS/IV/MC and single-contract scenarios |

Portfolio MC is bounded to 250,000 paths; standalone numerical MC is bounded to
500,000 paths per scenario. Operational recipe routes own acquisition; pricing
and analysis endpoints do not start downloads.

### Quote inversion diagnostics and controlled evidence

An IV failure is conditional on the selected rate, carry and reference-price
basis. The chain exposes the model lower/upper bounds, the signed distance from
the lower bound, close/settlement references, and an explicit diagnostic code.
A daily reference below a fitted-forward bound does not by itself establish a
source-data error or executable arbitrage. Prices are not clipped into a valid
interval. Inspect the contract and recheck the alternative basis; IV, Greeks and
surface inclusion remain unavailable when inversion fails.

**Strategies and evidence > Controlled evidence** compares ATM IV, RR25,
butterfly, smirk, term slope, truncated risk-neutral skewness and high-frequency
VRP, individually and jointly, at 1/5/20-session horizons. Every model in a
horizon uses the same complete-case cohort, RV20/momentum controls, ETF
intercepts and purged training schedule. The reserved-period start is explicit;
models remain frozen within it. The headline distinguishes positive OOS R² from
reserved-period confirmation and descriptive statistical support. Loss
improvement is baseline squared error minus augmented squared error, aggregated
by date before horizon-lag HAC inference. Normal-reference p-values receive a
Bonferroni correction across the full family. These historical, dependent-ETF
experiments remain descriptive evidence, not prospective validation.

The matched rotation panel saves two canonical SDK projects: controls-only
forecasts and option-augmented forecasts from the same frozen study. Both use
weekly final-close decisions, equal top-three weights, next-open fills and the
same execution/cost settings. Background jobs retain their pinned revisions;
failed jobs can resume without replacing successful runs. Full OOS and
reserved-period NAV segments are compared on common valid dates. The reserved
segment continues entering holdings instead of resetting capital. Raw runs with
different periods remain available for provenance, below the aligned comparison.

Use `GET /api/options/research/suites` to list controlled studies and
`POST /api/options/research/suites/run` to run the fixed family.
`GET /api/options/research/suites/{id}` returns a frozen manifest;
`GET/POST /api/options/research/suites/{id}/rotation` reads or starts matched
rotations. Starting rotations requires `confirm_python_execution=true`; pure
numerical forecast studies do not execute user-authored source or contact RQ.

### Literature predictors and return research

Open **Literature & prediction** to inspect cited definitions, data coverage,
historical features, purged out-of-sample results, and editable SDK project
previews. The module registers nineteen option features in the existing Factor
Workbench's **Options** category. Each catalog entry includes Chinese/English
names, units, formula, source reference, close-time availability and a hypothesis
status; a suggested direction is not evidence of local predictive skill.

The feature families are:

| Family | Saved fields / meaning |
| --- | --- |
| Level and term structure | `option_atm_iv_30/60/90`, `option_term_slope` |
| Delta smile | `option_rr25_30`, `option_bf25_30`; spot BS Delta at fixed effective carry |
| Literature smirk | `option_smirk`, `option_smirk_volume`; explicit moneyness and maturity selection |
| Finite-strike replication | `option_mf_variance_30`, `option_q_skew_30`, `option_q_kurt_30` |
| Variance premium | `option_vrp_daily_30`, `option_vrp_hf_30`; separately labeled daily and five-minute realized-variance inputs |
| Realized variance/controls/activity | `option_rv5m_1`, `option_rv5m_20`, `option_rv20`, `option_momentum20`, `option_oi_pcr`, `option_volume_pcr` |

Constant-maturity IV interpolates total variance between observed maturities;
25-delta call and put legs are interpolated separately. Maturity extrapolation is
refused. The smirk selection follows the cited paper's daily moneyness rules,
with the European ETF IV/liquidity conventions disclosed as an adaptation.
Risk-neutral log moments use static payoff-replication integrals over the
reported observed strike bounds. Missing wings are not invented. Their central
moments are finite-strike proxies, distinct from RR25 and from physical return
skewness; the fourth moment is non-excess kurtosis. None of these daily ETF
calculations is an exact reproduction of a US individual-stock study or an
official VIX index.

`rq.option_etf_5m` retains original unadjusted open/close bars with timestamps.
Each saved trading day requires the complete 48-bar Chinese exchange grid.
`research.option_intraday_variance` stores the sum of squared five-minute log
returns and an overnight contribution adjusted with the retained daily ETF
history, preventing distributions from creating false overnight jumps. Its first
observation can lack a prior adjusted close and remains missing, not zero.
`option_rv5m_20` requires twenty consecutive complete dated observations and
annualizes their mean realized variance using 252 sessions. The high-frequency
VRP variant subtracts that trailing annual variance from finite-strike implied
variance. It never subtracts a subsequently realized future variance as a signal.

`research.option_features` has one row per date/ETF with source fingerprints,
formula version, availability and per-expiry diagnostics. Its visible local
recipe rebuilds changed inputs and preserves explicit NaNs; invalid observations
replace previously valid values rather than carrying stale signals. The Data
SDK's `context.iter_partitions(dataset, start=..., end=..., symbols=...)` prunes
known daily option partitions before reading them. This is a recipe API, not a
provider handle exposed to a strategy.
`context.publish(..., wait_seconds=60)` lets the feature recipe wait at most one
minute for an existing backtest's dataset read lock. The default remains an
immediate lock check; the wait never permits writes during a frozen backtest.
An unchanged local feature build returns `published_feature_rows = 0` successfully.

Historical option availability starts at the earliest contract listing in RQ's
complete instrument master for each ETF. Pre-listing dates are excluded from
that ETF's required-session manifest; a missing post-listing master or contract
still fails synchronization. The exact inclusive option listing/delisting rule
used by RQ's installed `all_instruments(date=...)` is applied to the selected ETF
subset of its complete master. Network reads are bounded and concurrent, and
publication remains exclusive.
The store reconciles its option inventory once per writer and updates partition
counts incrementally instead of rescanning every retained file after each day.

An inconsistent traded daily close is never accepted merely by relaxing its
OHLC check. The recipe requests the same contract's complete 48 five-minute
bars, reconciles total volume and checks traded closes and the 15:00 reference
against daily price bounds. It also requires the following dated contract
multiplier and strike adjustment to explain the original close's scale within
tick-rounding bounds. Only then can the same-day 15:00 reference replace the
inconsistent close. RQ minute opens can carry a preceding reference; a closing
reference is not necessarily the last trade or an executable fill. Following
contract properties are retrospective data-quality evidence, not model inputs.
`source_close`, `close_recovery`, and the complete timestamped `close_evidence`
are retained and revalidated on publication and quality review. Missing or
inconsistent evidence still fails the job. Quality reports count reconstructed
closes and retain a warning; the original vendor reference remains auditable.
The workbench reuses a saved full-scope quality report only when its row/file
counts and date bounds match and its check is newer than every source partition.
Rewrites, removals and unresolved gaps invalidate that reuse. An initial full
scan of a long history can take several minutes.

The History sync button runs a persisted Python recipe through the existing
background worker; an LLM/data agent is not required for acquisition or quality
decisions. Options reuse complete saved sessions and refresh the configured
overlap. ETF and intraday coverage exclude dates before each ETF's listing.
Pre-adjusted canonical market bars refresh the selected ETF's entire retained
interval, including warm-up dates. Retained realized variance and previously
materialized option features are checked again after source corrections;
unchanged feature fingerprints are reused. Actual feature calculation failures
fail quality review, while expected warm-up or unobserved-wing NaNs remain
explicit missing values. Full sync checks derived datasets even when a feature
batch is unchanged. Quality-cache reuse also requires the current validation
version. A task's numeric publication counts appear after completion; they are
not an estimated download percentage. Daily scheduling and alert delivery are
separate operational choices and are not enabled by opening this workbench.

Saved predictors enter the existing research-factor provider:

```python
@factor(id="smirk_hypothesis", label="Smirk hypothesis")
def smirk_hypothesis(context):
    """Read the current eligible daily ETF smirk without stale-day carry."""
    return context.daily_factor("option_smirk")
```

Declare `DATA_REQUIREMENTS = {"daily_factors": ["option_smirk"]}` together with
the normal market/instrument requirements. The SDK enforces exact-session
availability for `option_` fields: close events may read that session; open events
may read only the preceding strategy session. Missing dates remain NaN. This
rule also covers experiment-scoped forecasts. No strategy directly opens a
parquet file or contacts RQ.

Return research uses close-t predictors and an executable label from adjusted
open at t+1 to adjusted close at t+h, where h is 1, 5 or 20 trading sessions. Each
expanding-window fit uses only training labels completed before its test block,
plus the selected embargo. Means, scales, ETF intercepts and coefficients are
estimated on training rows only. A fixed ridge penalty and refit interval are
chosen before the reported test; no best test direction is selected. The default
baseline uses the same ETF intercepts plus RV20 and 20-session momentum. A
control selected as a predictor stays in the baseline and does not receive false
incremental R-squared credit.

Reports contain out-of-sample daily cross-sectional Rank IC with horizon-lag
HAC inference, incremental prediction R-squared, predictor group returns,
coverage, fold cutoffs, and latest forecasts. Training OLS coefficients use
within-date clustered scores and Newey-West aggregation across dates; they are
explicitly not significance tests of post-selected out-of-sample predictions.
Unknown tail returns remain unscored forecasts. Overlapping group returns are
not a tradable NAV. Short tests, large IC/R-squared, correlated ETFs, fixed-universe
selection, vendor historical revisions and repeated experiment selection have
explicit warnings. Building the pipeline does not establish successful alpha.

**Preview feature strategy** creates a signed rank blend of selected indicators;
**Preview OOS forecast strategy** uses the saved experiment's unique forecast
field. Inspect the replacement universe, signal, allocation and factor bodies,
then create an editable project through the canonical project API and source
probes. Data acquisition uses the complete visible ETF recipe. The usual Factor,
Strategy and Validation workbenches own subsequent editing and backtests.
Weekly/monthly close decisions, next-session open fills, costs, suspensions,
limits, cash and NAV remain in the single existing SDK engine. Each forecast
experiment uses a distinct `option_prediction_<run_id>` field, and backtest data
locks include the derived datasets; a new experiment cannot redirect an older
project to another forecast vector.

The pure numerical facade also exports `literature_catalog`, `option_features`,
`strip_moments`, `research_panel`, and `walk_forward_research`. HTTP additions:

| Endpoint | Purpose |
| --- | --- |
| `GET /api/options/research/catalog` | Cited bilingual definitions, coverage and saved experiment summaries |
| `GET /api/options/features` | Bounded dated feature history |
| `POST /api/options/features/plan`, `/features/jobs` | Offline local-feature source preview / existing job-manager submission |
| `POST /api/options/research/run` | Bounded purged walk-forward study and dated forecasts |
| `GET /api/options/research/runs/{id}` | Reproducible saved research JSON |
| `GET /api/options/research/strategies` | Latest completed option-signal strategy runs, with named rules and net performance |
| `GET /api/options/research/strategies/{id}` | Frozen rules, NAV/drawdown, matching-date benchmark comparison, annual returns, holdings and executions |
| `POST /api/options/research/projects/plan`, `/projects` | Reviewable canonical project specification / confirmed project creation |

The bilingual Literature & prediction view opens with **Strategies & results**.
It compares completed SDK backtests rather than research-model configuration.
The supported examples are low-smirk rotation (negative smirk weight), high-VRP
rotation (positive high-frequency VRP weight), the three-feature rank blend
(-smirk + term slope + high-frequency VRP), and experiment-scoped OOS forecast
rotation. These examples rank ETFs at the weekly close, select the top three,
and retain cash when eligible positions are insufficient. They allocate ETFs;
they do not simulate executing option contracts. Paper definitions and signs
are hypotheses adapted to Chinese ETF data, not evidence that these rules work.

Results come from the canonical saved event-engine outputs. The displayed rule
is inspected from the run's frozen source, not today's editable project draft.
Actual execution timing, costs and fidelity warnings remain visible. Benchmark
comparisons compound only dates where both returns exist, disclose their sample
count, and never replace a missing benchmark return with zero. A benchmark NAV
line stops at an internal gap rather than silently bridging it. Cost-rate sums
use each period's capital and are not a cumulative fee ratio on initial capital.
Forecast strategies can begin later than raw-feature strategies; compare aligned
dates before interpreting relative performance. Positive ETF strategy returns
alone do not establish incremental predictive value.

**Forecast tests & literature** loads saved horizon-specific evidence, including
the control-model OOS R-squared, rank IC, HAC inference, overlapping group-return
estimates and training-label cutoffs. A nonpositive OOS R-squared is explicitly
identified as no improvement over controls. **Custom research** retains feature
construction, model settings, full evidence export and editable SDK project
creation, with feature/weight settings collapsed initially.

Option-chain and basket rows display a readable Chinese/English contract name
alongside the code. The name uses the ETF, expiry, call/put, **dated** strike and
adjusted multiplier. This is a reconstructed description, not a claim to be the
exchange's official short name. Historical snapshots need no redownload to gain
this label. Future RQ ingestion also preserves the optional current vendor
master name and trading code; the current master name cannot override dated
contract terms in historical labels.

Numerical experiments are persisted as reproducible JSON research artifacts in
the ignored runtime store. The view exports the complete JSON and a bilingual
Markdown interpretation with citations. These artifacts are research diagnostics;
AlphaLab's global published Report Workbench continues to use Conexus Documents.

Jump parameters are conditional scenario assumptions, not market-calibrated
crash forecasts. Sampling intervals exclude model/calibration uncertainty;
P&L excludes financing, distributions received, costs and liquidity impacts.
Continuous dividend yield approximates discrete ETF distributions. Minute/tick
quote synchronization, bootstrapped discount curves and discrete cash-dividend
models, arbitrage-certified surface calibration, Heston/Bates calibration,
importance sampling, American/exotic
payoffs, margin and physical-measure portfolio VaR/ES remain separate extensions.
Source-data acceptance should include ordinary sessions, expiry, an actual ETF
distribution adjustment, interrupted recovery and provider corrections.

Official references: [RQ option APIs](https://www.ricequant.com/doc/rqdata/python/options-mod),
[RQ common APIs](https://www.ricequant.com/doc/rqdata/python/generic-api), and
[Merton model conventions](https://www.mathworks.com/help/finance/merton.html).

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


### Option model and research verification

All workstation valuation requests carry the selected date and close/settlement
price basis. Portfolio requests include `price_field`; history also accepts
`price_field` and an optional manual `rate`. Changing the basis invalidates the
old basket result. `GET /api/options/names` returns dated contract descriptions
without requiring a curve or running IV fits, so distribution-adjusted strikes
and multipliers are refreshed when the analysis date changes.

The `option-research-v3-forward-atm` feature version uses log(K/F)=0 everywhere.
Constant-maturity IV interpolates total variance and never extrapolates an
unobserved tenor. Historical IV uses the identical definition, solves only the
necessary forward-ATM brackets and caches by source file version, basis and
rate. Economic input fingerprints exclude retrieval timestamps and discovery
manifests; provenance is retained separately. Daily realized-return predictors
fingerprint their actual 21-close lookback instead of all earlier history.

Compact dataset quality gates check ETF/session keys against source grids,
respecting observed listing intervals and requested scope. A missing date is
not excused by matching global start/end dates. Return labels reindex the
exchange/session grid before shifting; an absent open or intermediate close
makes that label unscored rather than moving the horizon to a later quote.

The Volatility view exposes both diagnostic raw SVI and power-law SSVI with
gamma=1/2. SSVI fits monotone ATM total variance and bounds eta to satisfy the
sufficient static-variance butterfly conditions for all positive theta. It
retains market residuals. A failed tenor rejects queries touching that tenor;
other validated tenor intervals remain available and are explicitly marked as
partial coverage. `POST /api/options/surface/price` values European options
inside the shared observed strike/maturity domain. These are model values,
not executable bid/ask quotes. Method: [Gatheral and Jacquier](https://arxiv.org/abs/1204.0646).

`POST /api/options/models/calibrate` fits a deterministic Q Merton Poisson-mixture
price model to daily OTM quotes, using multiple initial points and holding out
every fourth ordered strike. Reports retain train/holdout vega-scaled errors,
Jacobian conditioning, dated quotes and parameter sensitivity scenarios.
`GET /api/options/models/calibrations/{id}` reads the immutable artifact. Applying
one to portfolio stress requires its exact ETF, date, basis and manual-rate
context and successful diagnostics. Calibrated diffusion volatility replaces
the ATM proxy only for that matching single-ETF basket. Sensitivity scenarios
are not parameter confidence intervals or forecasts of real-world jump risk.

Joint Monte Carlo reports nonzero-payoff counts, effective payoff sample sizes
and errors for every leg under both baseline and stress scenarios. Any
insufficient leg suppresses the portfolio sampling interval. The optional
`sampling="importance"` uses a defensive 50/50 mixture of ordinary and Gaussian-
tilted shared paths, exact bounded likelihood weights, and expiry-specific
likelihoods for settled legs. `importance_tilt` controls the diffusion tilt;
rare jump events can still require more paths. Weighted payoff quantiles,
effective path counts and likelihood means are retained for diagnostics.

New walk-forward experiments reserve the last 20% of sessions by default. The
entire reserved block uses one pre-holdout training cutoff; means, scales,
coefficients and control-model fits are frozen. An explicit `holdout_start` is
also supported by the research endpoint. Reports show holdout IC, HAC inference,
control-model R-squared and single-feature ablations. This fixed historical
split is not a prospective blind test, and repeated manual experiments remain
subject to multiple testing. Earlier experiments and frozen runs are retained.

`GET /api/options/research/comparison` aligns all selected saved runs to shared
valid dates, includes ETF-pool and one-ETF-per-index benchmarks, and reports
average index weights plus descriptive monthly CAPM attribution. The canonical
benchmark helper supports `label_period_end=True`; this view uses close-to-close
returns labeled on the end session to match daily strategy marks. Its market
proxy is the ETF pool with a zero risk-free assumption; benchmarks are gross
and strategies net of recorded costs. The original saved benchmark is displayed
separately under its frozen convention. Beta and intercept are not a claim of
incremental option-signal alpha.

Strategy descriptions accept only verified literal universe, selection and
allocation bodies; unfamiliar edits are labeled custom. Completed backtests and
research publish the existing workspace refresh event, and the manual refresh
button invalidates the option-query family. Model errors, empty-data states and
rare-payoff fallbacks participate in the same Chinese/English localization.

Historical RQ no-trade rows may retain a reference open outside the reference
high/low, just as reference closes can differ. Quality report version 6 preserves
those raw values and counts `zero_volume_open_outside_range` with an explicit
warning. All zero-volume rows remain excluded from IV, even with `min_volume=0`.
Traded closes must still lie inside the range. An inconsistent traded open is
rejected unless `open_audit` retains the original open and a complete same-day
48-bar evidence grid exactly reconciles daily high, low, close and volume.
The unresolved raw open is never replaced or certified as an executable opening
fill; `audited_reference_opens` is counted and warned. Persisted evidence is
revalidated, including its original-open binding. Unmarked reversed high/low,
negative or nonfinite data, missing contracts and missing sessions are rejected.

Unreconciled traded OHLC is retained only with explicit `quote_quarantine` and
`quote_quarantine_reason` fields. This certifies record coverage, not price
usability: those contracts receive `source_quote_invalid` and are excluded from
forward estimation, IV (including the fast ATM history), surfaces, calibration
and portfolio valuation. A quarantine flag on an otherwise consistent quote or
unknown quarantine method is rejected. The raw prices and available intraday
evidence are retained; no synthetic OHLC values are introduced. Quality reports
count and warn on quarantined quotes. Missing contracts and sessions still fail.
