# PS2 Version A — Build and Backtest an Options Strategy

Work individually. Use the [AlphaLab Lab 3 starter](https://github.com/yuntingliu/quant-framework/tree/option/examples/course/lab3).

**Submit online at [submission.acetoken.net](https://submission.acetoken.net) by Wednesday, November 25, 2026, at 1:00 PM (China Standard Time).**

## 1. Build and audit a short-strangle position report

The SSE 50 ETF closes at CNY 2.869 on 23 October 2026. Both options expire on 23 December 2026. Use 10,000 ETF units per contract and the rate, yield and days/365 convention in `strangle.json`.

| Leg | Strike (CNY) | Contracts | Premium (CNY per ETF unit) | Implied volatility |
| --- | ---: | ---: | ---: | ---: |
| Put | 2.65 | −10 | 0.0177 | 17.7845% |
| Call | 3.20 | −10 | 0.0058 | 18.5437% |

**(a) Payoff and Greeks.** Implement `expiry_pnl` and `portfolio_greeks`. Add the opening cash flow $-\sum_i n_iM_ip_i$ to signed exercise payoffs. Verify both break-even prices. Multiply each per-unit Greek by the signed contracts $n_i$ and units per contract $M_i$. Explain the net delta, gamma and vega.

**(b) Stress.** Implement `stress_grid`. Apply each spot shock and add 0, 5 or 10 IV points to each leg’s IV. Hold time, rate and yield fixed. Compare the signed change in model value with
$$\Delta\,dS+\tfrac12\Gamma(dS)^2+\nu_{\mathrm{pp}}(100\,d\sigma).$$
Use $d\sigma=0.05$ for five IV points. Identify the largest error and its scenario.

**(c) Connect and verify.** Register the functions with `ReportRequest`. Show tool discovery, a valid invocation and rejection of a zero quantity. Run the checker and show that an independent test catches a sign or multiplier error.

**(d) Submit two reports.** Run ten pairs locally and two through the API. Each folder must contain `report.md`, `report.json`, the expiry chart and the stress chart. Use the supplied report template to explain break-evens, Greek signs and the largest approximation error. Compare both sizes against CNY 10,000 under the 20% price drop with IV fixed and under the worst grid scenario. Recommend a size and state what evidence you would still need before trading.

## 2. Backtest an options strategy

Use historical SSE 50 ETF options. You may include shares of the underlying ETF. Choose the strategy and write down the rules before running the holdout.

**(a) Market setup and entry.** State the conditions for opening a position. You can use a moving average, volatility, macro news or another observable signal. Define the decision time, strike, expiry and signed size of each leg. Use only data available at the decision time and simulate fills at the next usable open.

**(b) Exit and risk.** Specify when to close. For example, use a maximum holding period, profit target or loss stop, with a final exit before option expiry. A loss detected at the close triggers an order for the next usable open. The realized loss may exceed the threshold. Log missing quotes, skipped fills and unresolved positions.

**(c) Capital and costs.** Start with CNY 100,000. Track signed cash flows, fees, daily marks and short-option margin. Use the dated strike and multiplier for each option. The course defaults are two adverse ticks and CNY 2 per option contract per fill. State ETF trading costs and how you account for distributions if you hold ETF shares. Test a higher-cost case.

**(d) Historical evidence.** Use 2020–2024 for development and reset capital for the January 2025–October 23, 2026 holdout. Plot equity versus zero-interest cash, drawdown and trade P&L. Report net P&L, max drawdown, worst trade, trade count and peak margin. Reconcile the trade ledger to final equity and explain the worst trade. Decide whether the evidence merits paper trading. A comparison with and without your entry filter is optional.

**Submit** your code, registry patch, API and test evidence, both position report folders, the backtest report with ledgers and charts, run instructions and the AI audit log.

**Marking.** Position analysis 20%. AlphaLab integration 15%. Strategy and backtest checks 35%. Reports, figures and interpretation 30%.
