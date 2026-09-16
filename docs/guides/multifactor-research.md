# Multifactor research and portfolio construction

These notes describe a research workflow and illustrative portfolio rules.
They are not implemented SDK defaults, investment recommendations, or reproduced
backtest results. For executable contracts, see the
[SDK guide](../06_ALPHALAB_SDK_GUIDE.md). For comparisons with published results,
see the [factor replication findings and limits](../releases/0.6.2-dev-liu.md).

## Define the research question

Choose the universe, benchmark, portfolio type, rebalance schedule, and risk
budget before evaluating factors. Possible universes include historical CSI 300
or CSI 500 constituents and the point-in-time A-share universe. Long-only,
benchmark-enhanced, and long-short portfolios answer different questions.

Specify drawdown and volatility objectives, stock and industry limits, minimum
liquidity, and treatment of small-cap, ST, suspended, and delisted stocks. An
illustrative question is whether value, quality, and momentum can select a
monthly portfolio of 50 CSI 300 stocks under industry and turnover constraints.

## Build point-in-time data

Use the prices, volumes, constituent membership, classifications, and financial
statements that were available at each decision time. Financial data becomes
available on its actual disclosure date, not the end of its reporting period.
Do not substitute later revisions into earlier observations.

Include listing and delisting history, suspensions, price limits, corporate
actions, execution costs, and liquidity. A backtest using only surviving stocks
or present-day index members does not reproduce the historical opportunity set.

## Define and prepare factors

Illustrative definitions are:

| Factor | Example definition |
| --- | --- |
| Value | Earnings divided by price |
| Quality | Operating profit divided by book equity |
| Momentum | Twelve-month price return excluding the latest month |

Specify the accounting fields, publication lag, price adjustment convention,
lookback, direction, and missing-data treatment before running comparisons.
Different definitions under the same factor name can produce different returns.

At each observation date, handle missing values, winsorize where justified,
control for industry and size exposures, and standardize the cross section:

$$
z_i = \frac{x_i - \bar{x}}{\sigma_x}.
$$

Record the order and scope of these transformations. Neutralization and
standardization are model choices rather than interchangeable cleanup steps.

## Evaluate the factor

Inspect Rank IC, its mean and uncertainty, quantile returns, monotonicity, and
high-minus-low performance. Repeat across periods, sectors, and capitalization
groups, and examine transaction costs and capacity.

A factor-mimicking portfolio tests a signal's return relationship. It is not
necessarily the portfolio that an investor can implement under real constraints.

For a candidate factor's incremental contribution, an illustrative regression is:

$$
NEW_t = \alpha + b^\top [MKT_t, SMB_t, HML_t, RMW_t, CMA_t] + \epsilon_t.
$$

Evaluate intercepts, pricing errors, appropriate joint tests such as GRS, and
out-of-sample behavior with a stated model and sample. A strong standalone
backtest does not establish incremental explanatory power.

## Combine signals and construct weights

A simple illustrative score is:

$$
Score_i = 0.3 Value_i + 0.3 Quality_i + 0.3 Momentum_i - 0.1 Risk_i.
$$

Alternatively, estimate expected returns as a combination of stock exposures
and estimated factor premiums. Prefer a transparent baseline before adding
learned weights or machine learning; additional complexity needs independent
out-of-sample evidence.

An illustrative monthly specification is:

| Rule | Example |
| --- | --- |
| Entry | Select the top 50 eligible stocks |
| Exit buffer | Remove an incumbent after its rank falls below 70 |
| Stock weight | Maximum 3% |
| Industry exposure | Maximum 5 percentage points away from benchmark |
| Monthly turnover | Maximum 30%, with the convention stated |
| Execution | First session of the next month at a specified VWAP model |
| Frictions | Fees, impact, liquidity, suspensions, and price limits |

These numbers are examples, not tuned production settings. Fix their meaning
before testing. Separate training, validation, and an untouched test sample;
repeatedly tuning against the test period turns it into training data.

Target weights may be equal-weighted or optimized, for example:

$$
\max_w \; w^\top \mu - \gamma w^\top \Sigma w - \eta TC(w, w^-),
$$

subject to the selected budget, exposure, liquidity, and turnover constraints.
Here `w^-` denotes the actual pre-trade weights, not the previous target weights.

## Run the monthly portfolio lifecycle

At the January month-end decision time, after the relevant close and data
availability cutoff:

1. Determine the point-in-time eligible universe.
2. Calculate, prepare, and combine the factor observations.
3. Apply selection and incumbent-buffer rules.
4. Construct feasible target weights `w*`.
5. Generate orders for the next eligible execution session.

At the first February session, an initially empty portfolio has `w^- = 0`.
For each stock:

$$
\Delta w_i = w_i^* - w_i^-,
\qquad
OrderValue_i = PortfolioValue \times \Delta w_i.
$$

Positive differences imply buys, negative differences imply sells, and zero
differences imply no trade. Execution still depends on available cash, lot
sizes, tradability, costs, and the specified fill model.

During February, update NAV, corporate actions, positions, suspensions,
delistings, and risk. Price movement changes the actual weights. Daily changes
in factor ranks do not trigger orders in a monthly strategy unless an explicit
off-cycle risk rule says they should.

At February month-end, calculate new targets using information then available.
Apply the holding buffer, compare those targets with drifted actual weights,
and enforce minimum trade amounts, turnover, and liquidity limits. Execute
eligible orders in the first March session and repeat.

| Frequency | Activity |
| --- | --- |
| Daily | Accounting, market-state checks, and explicitly defined risk controls |
| Monthly | Signal refresh, selection, target weights, orders, and execution review |
| Quarterly | Broader attribution and evidence review |

## Review performance without chasing noise

Attribute returns to value, quality, momentum, industry and size exposures, and
execution effects. Review fills, turnover, impact, IC deterioration, risk, and
drawdowns. Compare intended targets with realized positions.

One poor month alone is not evidence that the strategy needs redesign. A new
research version should respond to evidence such as a failed economic premise,
persistent out-of-sample deterioration, changed cost structure, or a verified
data or execution error. Re-run the documented validation process after changes.

The complete chain is:

```text
Economic hypothesis -> Factor definition -> Signal combination
-> Expected returns / ranking -> Target weights -> Orders
-> Execution and accounting -> Monitoring -> Next rebalance
```

Keep the research portfolio used to test a factor distinct from the constrained
portfolio used to implement a strategy.
