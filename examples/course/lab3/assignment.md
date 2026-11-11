# PS2 Version A — Build and Backtest an Options Strategy

Work individually. Submit the version assigned to you. Use the [Lab 3 starter on AlphaLab’s `option` branch](https://github.com/yuntingliu/quant-framework/tree/option/examples/course/lab3). Submit your own code, a strangle risk report, and a historical test of a timed options strategy. Document any AI assistance in the supplied audit log.

**Submit online at [submission.acetoken.net](https://submission.acetoken.net) by Wednesday, November 25, 2026, at 1:00 PM (China Standard Time).**

## 1. Calculate expiry P&L and Greeks

On 23 October 2026 the SSE 50 ETF ($510050.\mathrm{XSHG}$) is at CNY 2.869. Consider selling ten puts and ten calls with a common expiry of 23 December 2026. Use the premiums below as the assumed entry prices. Each contract covers 10,000 ETF units.

| Leg | Strike (CNY) | Contracts | Premium (CNY per ETF unit) | Implied volatility |
|:----|---:|---:|---:|---:|
| Put | 2.65 | −10 | 0.0177 | 17.7845% |
| Call | 3.20 | −10 | 0.0058 | 18.5437% |

Use the exact inputs in the supplied case file. For European Black–Scholes pricing, use the supplied continuously compounded risk-free rate $r$ and dividend yield $q$, and actual calendar days divided by 365 for time to expiry.

**(a)** Implement `expiry_pnl` in `options_homework/calculations.py`. If $n_i$ is the signed quantity, $M_i$ is the multiplier and $p_i$ is the entry premium per ETF unit, the opening cash flow is
$$C_0=-\sum_i n_iM_ip_i.$$
Add each signed intrinsic payoff at expiry to $C_0$. Ignore transaction costs and interest on the credit for this calculation. Return `entry_credit_cny` and `rows` with `terminal_spot` and `pnl_cny`. Derive both break-even prices and check them numerically. Plot the position's expiry P&L against the terminal ETF price.

**(b)** Implement `portfolio_greeks` with the public `alphalab.options` functions. Scale each long-option, per-unit Greek by $n_iM_i$. Return per-leg `legs` with `contract` and the four Greeks, and a `net` object with the four totals. Use `delta` in ETF units, `gamma` in ETF units per CNY move in spot, `vega_1pct` in CNY per volatility percentage point, and `theta_day` in CNY per calendar day. Explain how the put and call can offset delta while both add negative gamma and vega.

## 2. Reprice the position under stress

Implement `stress_grid`. Apply spot shocks of −20%, −10%, 0%, +10%, and +20%, and add 0, 5, or 10 volatility percentage points to **each leg's own IV**. Hold time to expiry, $r$, and $q$ fixed. For each scenario, return `spot_shock`, `iv_shift`, `full_pnl_cny` and `local_pnl_cny` in `rows`. Use the signed change in model value for full P&L. Compare it with
$$\Delta_{\mathrm{pos}}\,dS+\tfrac12\Gamma_{\mathrm{pos}}(dS)^2+
\nu_{\mathrm{pp}}\,(100\,d\sigma),$$
where $dS$ is in CNY and $d\sigma=0.05$ for a five-point IV shock. Explain why entry premiums and theta are absent from this instantaneous mark-to-model calculation.

## 3. Connect the functions to AlphaLab

Use the `option` branch of the AlphaLab checkout. Activate the course Python environment and run all commands from the checkout root. The example is in `examples/course/lab3/`.

~~~powershell
python -m examples.course.lab3.run_report --inputs-only
~~~

**(a)** Use the supplied `options_lab.case_inputs` registration as the example. In `options_homework/tools.py`, register three `ToolSpec` handlers using `ReportRequest`. Map `options_lab.expiry`, `options_lab.greeks` and `options_lab.stress` to the functions in Questions 1 and 2. Include units in the tool descriptions. Verify that the registry describes all four tools and accepts a valid request.

**(b)** In `alphalab/tools/data.py`, find `create_data_tool_registry`. Insert these lines just before `return registry`.

~~~python
from examples.course.lab3.options_homework.tools import register_lab_tools

register_lab_tools(registry)
~~~

Start the local service from the checkout root.

~~~powershell
python -m alphalab.cli dev serve --port 8765 --agent off
~~~

Verify discovery with `GET /api/agent/data-tools`. Verify invocation with `POST /api/agent/data-tools/{tool_name}/invoke`, using a JSON body with the case under the `input` key. Save one example of successful invocation and one validation error, such as rejection of a zero quantity.

**(c)** Run `python -m examples.course.lab3.verify_submission` from the checkout root. Add one independent numerical or analytical check. Demonstrate that a deliberate sign or multiplier error causes a failure, then restore the implementation and rerun the check. Record the change and the evidence in the lab’s `ai-audit-log.md`.

## 4. Work in the repository and compare position sizes

Clone the [AlphaLab repository](https://github.com/yuntingliu/quant-framework/tree/option) on the `option` branch. Keep your lab code in the example folder and your local registry patch in `alphalab/tools/data.py`.

~~~powershell
git clone -b option https://github.com/yuntingliu/quant-framework.git
~~~

~~~text
quant-framework/
  alphalab/tools/data.py           # local registry connection
  examples/course/lab3/
    strangle.json, reference_checks.json
    run_report.py, verify_submission.py, inspect_history.py
    options_homework/
      calculations.py             # implement payoff, Greeks and stress
      models.py, tools.py         # typed requests and registration
      history.py                  # supplied daily history reader
      backtest.py                 # write strategy, fills, cash and margin
      backtest_report.py          # write summaries and figures
    run_backtest.py               # write the historical report runner
  runs/                           # generated outputs
~~~

**(a)** Create `ten.md` and `two.md` for your interpretations. Use draft text for the first run, then update each file and rerun. For the two-pair case copy the example’s `strangle.json` to `strangle-two.json` in the checkout root and set both quantities to −2. Run the ten-pair case locally and the two-pair case through the API.

~~~powershell
python -m examples.course.lab3.run_report ``
  --interpretation ten.md --out runs/ten-pairs
python -m examples.course.lab3.run_report strangle-two.json ``
  --interpretation two.md --out runs/two-pairs --api http://127.0.0.1:8765
~~~

**(b)** Compare each size against a CNY 10,000 loss budget for a 20% spot decline with unchanged IV and for the worst point in the full grid. Include a labeled figure comparing full and local P&L. Identify the scenario with the largest absolute approximation error and quantify it in CNY. Explain why the worst grid result is conditional on the specified shocks. Identify the evidence on fills, fees, margin, funding, and real-world outcomes you would need to approve the trade.

## 5. Backtest the options strategy

Use actual dated SSE 50 ETF option contracts ($510050.\mathrm{XSHG}$). Choose a strategy and explain what market conditions should favor it. Run the same strategy with and without an entry filter. Keep the contract rules, execution, costs and capital the same. The position report in Questions 1–4 uses a short strangle. For this backtest, choose a structure such as an iron condor, a bull put spread or a long straddle.

**(a) Freeze the rule.** Use 2020–2024 for development. Reset capital and rerun from the first 2025 session through October 23, 2026 as a fixed historical holdout. Start each period with CNY 100,000 and earn zero on idle cash. Allow at most one structure at a time. Write down your hypothesis, contract rules, entry filter and thresholds before opening the holdout.

**(b) Specify each leg.** Define the option type, long or short quantity, strike target, acceptable expiries and liquidity rule. For the common monthly test, decide at the close of the first ETF session of each month when flat. Consider listed contracts with positive volume and settlement and a multiplier of 10,000 at selection. Use the earliest expiry 25–55 calendar days away that supports the full structure and is on or before the period end. Break strike ties by ascending contract ID.

**(c) Example.** An iron condor sells one put near $0.95S_t$ and one call near $1.05S_t$, both out of the money. It buys one put near $0.90S_t$ and one call near $1.10S_t$. Require the same expiry and wing strikes strictly outside their short strikes. The wings cost premium and limit expiry loss under standard, equal-multiplier contract terms.

**(d) Trade and mark.** Fill the selected structure at the next session's open proxy. Reverse the signed quantities at the fifth scheduled ETF trading session before expiry. If that open is unusable, retry at subsequent opens before expiry. Count exchange sessions using the ETF calendar so holidays do not shorten the exit window. An open is usable when the bar has a positive open and reports positive daily volume. Use the volume only as a retrospective bar-quality check for execution. Cancel an unfilled entry and log delayed exits. If a held mark is missing or an exit remains unresolved by expiry, flag the run as incomplete. Use the multiplier and strike recorded on each date for the same held contract ID, including any adjustment. SSE 50 ETF options normally deliver ETF units on exercise. The required rule closes before expiry. See [SSE 50 ETF contract specifications](https://english.sse.com.cn/markets/derivatives/overview/50etf/).

**(e) Charge costs.** The base case uses two adverse ticks per leg per fill and CNY 2 per contract per fill. One tick is CNY 0.0001 per ETF unit. Record every fill at the positive opening price. Debit slippage in CNY as a separate cost on every buy and sell. On a low-priced option, this cost can exceed sale proceeds. Compare with zero costs and with four adverse ticks and the same fee. The opening print is an execution proxy. Neither auction participation nor simultaneous fills across legs can be verified with daily bars.

**(f) Track cash, equity, and margin.** Credit sales, debit purchases, and subtract fees. Equity equals cash plus the sum of signed settlement values across all open legs, scaled by dated multipliers. Start with the CNY 100,000 cash benchmark. Estimate margin by summing short-leg requirements using the [SSE initial margin formulas](https://english.sse.com.cn/markets/derivatives/overview/50etf/). As a classroom proxy, use the day's settlement and ETF close, with no spread offset. Skip an entry if signal-day equity is below this margin estimate. Specify how you check that any long-leg premium can be funded. If end-of-day equity falls below the estimate during a trade, attempt to close at the next usable open. Report any breaches and the maximum margin-to-equity ratio.

## 6. Define the entry filter

Use at least one indicator from each row. State the sign, lookback, threshold, timestamp and expected effect on your chosen strategy. You can use the following rule as a starting point for an iron condor.

| Input | Example rule | Rationale |
| --- | --- | --- |
| Trend | Adjusted ETF close is within 6% of its 60-session simple moving average. | Avoid entry after a large directional move. |
| Macro | The absolute 20-session change in the lagged 10-year Chinese government yield is at most 25 basis points. | Avoid periods of large rate repricing. |
| Volatility | 20-session annualized realized volatility is at most 30%. | Favor quieter recent price action for a short-volatility position. |

Calculate realized volatility as the sample standard deviation of the last 20 daily adjusted-close returns times $\sqrt{252}$. The supplied `history.load_signal_inputs` provides adjusted ETF closes and a 10-year yield observed by the previous ETF session, in decimal units. The yield's observation date is included for audit. For another macro series, use the value available at the original decision time, with its release lag and vintage if revised. Use enough pre-sample data for the rolling windows. Skip and log a timed signal if any required input is missing. Use raw spot and dated contract terms for selection and valuation.

For the untimed run, submit an entry at every eligible decision close. For the timed run, submit an entry only when the declared filter passes. Both runs use the next session's open proxy and the same exit rule. Record the indicator values, filter decision, scheduled dates, actual fills and reasons for skips. Check that the inputs used on each decision date precede execution.

## 7. Run and reconcile the backtests

**(a)** `history.load_history(start, end, root=None)` returns the ETF session series and the option chains keyed by session. By default it reads AlphaLab's local runtime data. Keep reads and plots outside the simulator. Feed both timing variants into the same fill, cash and margin code. Check local data with `python -m examples.course.lab3.inspect_history`. Generate the reports with `python -m examples.course.lab3.run_backtest --out runs/history`. Submit your AlphaLab integration as a small local patch.

**(b)** Save a trade ledger with signal, entry and exit dates, contract IDs, signed quantities, dated multipliers, prices, slippage, fees and net P&L. Save daily equity, drawdown and margin, a skip/error log, and a JSON summary with dates and cost assumptions. Check that each entry follows its signal, each exit precedes expiry, and completed-trade net P&L reconciles to final equity less starting capital. Recalculate one closed trade independently from its raw inputs. Verify that a long leg is a debit on entry and contributes positive marked value. Test that an unfilled leg leaves no partial structure in the ledger.

## 8. Use the evidence to make a decision

**(a)** For each period, compare timed and untimed cumulative P&L and drawdown on the same axes at base costs. Include the cash benchmark and report sensitivity to wider slippage. Plot trade P&L, mark each worst trade, and show peak margin usage. Plot the three timing indicators with thresholds and decision dates.

**(b)** Report closed trades, net CNY P&L, return on starting capital, win rate, max drawdown, worst trade and peak margin. Explain which legs drove each worst trade. Quantify cost drag and check which strategies beat cash in the holdout under both cost cases. Report how many entries the filter rejected. Explain whether timing reduced downside risk enough to justify missed trades and changed exposure. Discuss the zero-interest cash assumption. State whether either variant merits paper trading and what evidence could change your decision. A well-supported negative result earns full credit.

**Submit** the code, registry patch, API and test evidence, position reports, historical report, CSV/JSON outputs, charts, run instructions and AI audit log.

**Marking.** Position calculations and stress 20%. AlphaLab registration and integration 15%. Strategy timing, historical execution and checks 35%. Figures, interpretation and reproducibility 30%.
