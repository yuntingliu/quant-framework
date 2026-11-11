# PS2 Version A — Build and Backtest an Options Strategy

Work individually. Use the [AlphaLab Lab 3 starter](https://github.com/yuntingliu/quant-framework/tree/option/examples/course/lab3).

**Submit online at [submission.acetoken.net](https://submission.acetoken.net) by Wednesday, November 25, 2026, at 1:00 PM (China Standard Time).**

## 1. Build and audit a short-strangle position report

The SSE 50 ETF closes at CNY 2.869 on 23 October 2026. Both options expire on 23 December 2026. Use 10,000 ETF units per contract and the rate, yield and days/365 convention in `strangle.json`.

| Leg | Strike (CNY) | Contracts | Premium (CNY per ETF unit) | Implied volatility |
| --- | ---: | ---: | ---: | ---: |
| Put | 2.65 | −10 | 0.0177 | 17.7845% |
| Call | 3.20 | −10 | 0.0058 | 18.5437% |

**(a) Payoff and Greeks.** Implement `expiry_pnl` and `portfolio_greeks`. For signed quantity $n_i$, multiplier $M_i$, and entry premium $p_i$, add opening cash flow $-\sum_i n_iM_ip_i$ to the signed exercise payoffs. Plot expiry P&L and verify both break-even prices. Use `alphalab.options` for per-unit Greeks and multiply by $n_iM_i$. Report net delta in ETF units, gamma in ETF units per CNY, vega in CNY per vol point and theta in CNY per day.

**(b) Stress.** Implement `stress_grid` for the fixture's spot shocks (−20% to +20%) and IV shifts (0, 5 and 10 points), adding the shift to each leg’s own IV. Keep time, rate and yield fixed. Plot the signed change in model value alongside the local estimate
$$\Delta\,dS+\tfrac12\Gamma(dS)^2+\nu_{\mathrm{pp}}(100\,d\sigma).$$
Use $d\sigma=0.05$ for five IV points. Identify the largest approximation error in CNY.

**(c) Connect and verify.** Use the linked lab guide to register the three functions with `ReportRequest`. Show tool discovery, successful invocation, and rejection of a zero quantity. Run the checker and show that your own test catches a sign or multiplier error.

**(d) Compare sizes.** Generate Markdown and JSON for ten pairs locally and for two through the API. Explain the delta offset and the signs of gamma and vega. Compare both sizes with the CNY 10,000 budget under a 20% spot drop at unchanged IV and under the worst grid scenario. What evidence would you need before trading?

## 2. Design and backtest an options strategy

Use actual dated SSE 50 ETF option contracts ($510050.\mathrm{XSHG}$). Choose a strategy and explain what market conditions should favor it. Run the same strategy with and without an entry filter. Keep the contract rules, execution, costs and capital the same. For this backtest, choose a structure such as an iron condor, a bull put spread or a long straddle.

**(a) Freeze the rule.** Use 2020–2024 for development. Reset capital and rerun from the first 2025 session through October 23, 2026 as a fixed historical holdout. Start each period with CNY 100,000 and earn zero on idle cash. Allow at most one structure at a time. Write down your hypothesis, contract rules, entry filter and thresholds before opening the holdout.

**(b) Specify each leg.** Define the option type, long or short quantity, strike target, acceptable expiries and liquidity rule. For the common monthly test, decide at the close of the first ETF session of each month when flat. Consider listed contracts with positive volume and settlement and a multiplier of 10,000 at selection. Use the earliest expiry 25–55 calendar days away that supports the full structure and is on or before the period end. Break strike ties by ascending contract ID.

**(c) Example.** An iron condor sells one put near $0.95S_t$ and one call near $1.05S_t$, both out of the money. It buys one put near $0.90S_t$ and one call near $1.10S_t$. Require the same expiry and wing strikes strictly outside their short strikes. The wings cost premium and limit expiry loss under standard, equal-multiplier contract terms.

**(d) Trade and mark.** Fill the selected structure at the next session's open proxy. Reverse the signed quantities at the fifth scheduled ETF trading session before expiry. If that open is unusable, retry at subsequent opens before expiry. Count exchange sessions using the ETF calendar so holidays do not shorten the exit window. An open is usable when the bar has a positive open and reports positive daily volume. Use the volume only as a retrospective bar-quality check for execution. Cancel an unfilled entry and log delayed exits. If a held mark is missing or an exit remains unresolved by expiry, flag the run as incomplete. Use the multiplier and strike recorded on each date for the same held contract ID, including any adjustment. SSE 50 ETF options normally deliver ETF units on exercise. The required rule closes before expiry. See [SSE 50 ETF contract specifications](https://english.sse.com.cn/markets/derivatives/overview/50etf/).

**(e) Charge costs.** The base case uses two adverse ticks per leg per fill and CNY 2 per contract per fill. One tick is CNY 0.0001 per ETF unit. Record every fill at the positive opening price. Debit slippage in CNY as a separate cost on every buy and sell. On a low-priced option, this cost can exceed sale proceeds. Compare with zero costs and with four adverse ticks and the same fee. The opening print is an execution proxy. Neither auction participation nor simultaneous fills across legs can be verified with daily bars.

**(f) Track cash, equity, and margin.** Credit sales, debit purchases, and subtract fees. Equity equals cash plus the sum of signed settlement values across all open legs, scaled by dated multipliers. Start with the CNY 100,000 cash benchmark. Estimate margin by summing short-leg requirements using the [SSE initial margin formulas](https://english.sse.com.cn/markets/derivatives/overview/50etf/). As a classroom proxy, use the day's settlement and ETF close, with no spread offset. Skip an entry if signal-day equity is below this margin estimate. Specify how you check that any long-leg premium can be funded. If end-of-day equity falls below the estimate during a trade, attempt to close at the next usable open. Report any breaches and the maximum margin-to-equity ratio.

## 3. Define the entry filter

Use at least one indicator from each row. State the sign, lookback, threshold, timestamp and expected effect on your chosen strategy. You can use the following rule as a starting point for an iron condor.

| Input | Example rule | Rationale |
| --- | --- | --- |
| Trend | Adjusted ETF close is within 6% of its 60-session simple moving average. | Avoid entry after a large directional move. |
| Macro | The absolute 20-session change in the lagged 10-year Chinese government yield is at most 25 basis points. | Avoid periods of large rate repricing. |
| Volatility | 20-session annualized realized volatility is at most 30%. | Favor quieter recent price action for a short-volatility position. |

Calculate realized volatility as the sample standard deviation of the last 20 daily adjusted-close returns times $\sqrt{252}$. The supplied `history.load_signal_inputs` provides adjusted ETF closes and a 10-year yield observed by the previous ETF session, in decimal units. The yield's observation date is included for audit. For another macro series, use the value available at the original decision time, with its release lag and vintage if revised. Use enough pre-sample data for the rolling windows. Skip and log a timed signal if any required input is missing. Use raw spot and dated contract terms for selection and valuation.

For the untimed run, submit an entry at every eligible decision close. For the timed run, submit an entry only when the declared filter passes. Both runs use the next session's open proxy and the same exit rule. Record the indicator values, filter decision, scheduled dates, actual fills and reasons for skips. Check that the inputs used on each decision date precede execution.

## 4. Reconcile and interpret the backtest

**(a)** `history.load_history(start, end, root=None)` returns the ETF session series and the option chains keyed by session. By default it reads AlphaLab's local runtime data. Keep reads and plots outside the simulator. Feed both timing variants into the same fill, cash and margin code. Use the [lab guide](https://github.com/yuntingliu/quant-framework/tree/option/examples/course/lab3) to check data coverage and run your historical report from the checkout root.

**(b)** Save a trade ledger with signal, entry and exit dates, contract IDs, signed quantities, dated multipliers, prices, slippage, fees and net P&L. Save daily equity, drawdown and margin, a skip/error log, and a JSON summary with dates and cost assumptions. Check that each entry follows its signal, each exit precedes expiry, and completed-trade net P&L reconciles to final equity less starting capital. Recalculate one closed trade independently from its raw inputs. Verify that a long leg is a debit on entry and contributes positive marked value. Test that an unfilled leg leaves no partial structure in the ledger.

**(c)** For each period, compare timed and untimed cumulative P&L and drawdown on the same axes at base costs. Include the cash benchmark and report sensitivity to wider slippage. Plot trade P&L, mark each worst trade, and show peak margin usage. Plot the three timing indicators with thresholds and decision dates.

**(d)** Report closed trades, net CNY P&L, return on starting capital, win rate, max drawdown, worst trade and peak margin. Explain which legs drove each worst trade. Quantify cost drag and check which strategies beat cash in the holdout under both cost cases. Report how many entries the filter rejected. Explain whether timing reduced downside risk enough to justify missed trades and changed exposure. Discuss the zero-interest cash assumption. State whether either variant merits paper trading and what evidence could change your decision. A well-supported negative result earns full credit.

**Submit** the code, registry patch, API and test evidence, position reports, historical report, CSV/JSON outputs, charts, run instructions and AI audit log.

**Marking.** Position calculations and stress 20%. AlphaLab registration and integration 15%. Strategy timing, historical execution and checks 35%. Figures, interpretation and reproducibility 30%.
