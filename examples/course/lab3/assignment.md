# PS2 Version A — Build and Backtest an Options Strategy

Work individually. Use the [AlphaLab Lab 3 starter](https://github.com/yuntingliu/quant-framework/tree/option/examples/course/lab3).

**Submit your GitHub pull request URL at [submission.acetoken.net](https://submission.acetoken.net) by Wednesday, November 25, 2026, at 1:00 PM (China Standard Time).**

## 1. Which model prices out-of-the-money options better?

Use the October 23, 2026 option chains for at least two ETFs with December 23 expiry. The starter uses Ricequant data. Choose from CSI 300 (`510300.XSHG`), ChiNext (`159915.XSHE`) and SSE 50 (`510050.XSHG`). Use the same eligible puts and calls for both models.

**(a) Check the simulation.** Price a put and a call under the Black–Scholes dynamics with 1,000, 10,000 and 100,000 paths. Report estimates, standard errors, and the closed-form prices. Count paths with positive payoffs. Explain what a zero-hit run tells you about the simulation.

$$V_0=e^{-rT}\mathbb E_Q[g(S_T)],\qquad
g_C(S_T)=(S_T-K)^+,\quad g_P(S_T)=(K-S_T)^+.$$

**(b) Choose a competing model.** Explain how the model changes the distribution of terminal ETF prices and which wing you expect it to help. One candidate is [Merton jump diffusion](https://doi.org/10.1016/0304-405X%2876%2990022-2), with diffusion and jumps under the pricing measure. State the law of motion, including the drift adjustment. Check the simulated discounted ETF price against $S_0e^{-qT}$. Use the starter or justify another model.

**(c) Fit, then test.** Fit constant-volatility Black–Scholes and your alternative to the same near-the-money quotes for each ETF. The starter estimates the forward from central put–call pairs. It reserves farther-out strikes with $|\log(K/F)|>0.05$ for testing, excluding forward anchors. Freeze the parameters before scoring. Plot market and model prices against log-moneyness and report held-out mean absolute error in CNY per ETF unit, separately for puts and calls.

**(d) Interpret.** Identify where each model helps and where the quote error is large relative to simulation error. Save the inputs, fit parameters, held-out prices, path counts and seeds. These comparisons price held-out strikes on one date. The historical trading test is in Question 3.

## 2. Register AlphaLab tools and audit a short strangle

The SSE 50 ETF closes at CNY 2.869 on 23 October 2026. Both options expire on 23 December 2026. Use 10,000 ETF units per contract and the rate, yield and days/365 convention in `strangle.json`.

| Leg | Strike (CNY) | Contracts | Premium (CNY per ETF unit) | Implied volatility |
| --- | ---: | ---: | ---: | ---: |
| Put | 2.65 | −10 | 0.0177 | 17.7845% |
| Call | 3.20 | −10 | 0.0058 | 18.5437% |

**(a) Payoff and Greeks.** Implement `expiry_pnl` and `portfolio_greeks`. Add the opening cash flow $-\sum_i n_iM_ip_i$ to signed exercise payoffs. Verify both break-even prices. Multiply each per-unit Greek by the signed contracts $n_i$ and units per contract $M_i$. Explain the net delta, gamma and vega.

**(b) Stress.** Implement `stress_grid`. Apply each spot shock and add 0, 5 or 10 IV points to each leg’s IV. Hold time, rate and yield fixed. Compare the signed change in model value with
$$\Delta\,dS+\tfrac12\Gamma(dS)^2+\nu_{\mathrm{pp}}(100\,d\sigma).$$
Here $dS$ is the ETF price change in CNY and $\nu_{\mathrm{pp}}$ is the signed portfolio vega in CNY per IV percentage point. The IV change $d\sigma$ is in decimal form, so a rise from 20\% to 25\% gives $100d\sigma=5$. Identify the largest error and its scenario.

**(c) Organize and register.** Separate the pricing calculations, typed input models, tool definitions and report runners. Register your three strangle functions with `ReportRequest`. Connect the lab registry to AlphaLab and verify that it exposes `options_lab.monte_carlo` with `MonteCarloRequest`. Add an independent automated check for a sign or multiplier error to your PR.

**(d) Pass a call through an agent.** Give the agent the tool name and JSON schema. Ask it to produce a call for one fitted model and strike from Question 1. Save the name and arguments, the API request with arguments under `input`, and the returned `result`. Explain the price, standard error and any warning using the returned fields. Cover rejection of a one-path request in your automated checks.

**(e) Create one comparison report.** Run ten pairs locally and two through the API. Submit one `report.md` with both sizes side by side, one `report.json` and the figures. Compare both sizes with CNY 10,000 under the 20% price drop at fixed IV and under the worst grid scenario. Recommend a size and explain what evidence you would still need before trading.

**(f) Write a report skill.** Submit a `SKILL.md` that tells an agent how to read the two JSON cases, verify units and size scaling, and draft the comparison using the supplied template. Run it once and review its claims against the tool outputs.

## 3. Backtest an options strategy

Use Ricequant option history for an ETF of your choice, such as CSI 300, ChiNext or SSE 50. You may include shares of the underlying ETF. Write down the rules before running the holdout.

**(a) Market setup and entry.** State the conditions for opening a position. You can use a moving average, volatility, macro news or another observable signal. Define the decision time, strike, expiry and signed size of each leg. Use only data available at the decision time and simulate fills at the next usable open.

**(b) Exit and risk.** Specify when to close. For example, use a maximum holding period, profit target or loss stop, with a final exit before option expiry. A loss detected at the close triggers an order for the next usable open. The realized loss may exceed the threshold. Log missing quotes, skipped fills and unresolved positions.

**(c) Capital and costs.** Start with CNY 100,000. Track signed cash flows, fees, daily marks and short-option margin. Use the dated strike and multiplier for each option. The course defaults are two adverse ticks and CNY 2 per option contract per fill. State ETF trading costs and how you account for distributions if you hold ETF shares. Test a higher-cost case.

**(d) Historical evidence.** Use available history through 2024 for development. State the actual start date if the options listed after 2020. Reset capital for the January 2025–October 23, 2026 holdout. Plot equity versus zero-interest cash, drawdown and trade P&L. Report net P&L, max drawdown, worst trade, trade count and peak margin. Reconcile the trade ledger to final equity and explain the worst trade. Decide whether the evidence merits paper trading. A comparison with and without your entry filter is optional.

**Pull request.** Open a PR from your lab branch into `option` in your own course copy. Commit the code, registry change, automated tests and report skill. Use the [PR template](https://github.com/yuntingliu/quant-framework/blob/option/examples/course/lab3/pull_request_template.md) to summarize findings and link run instructions and one report bundle containing the Monte Carlo comparison, JSON tool-call trace, single position report, backtest report and ledgers, and AI audit log. Check the PR's **Checks** tab for test results.

**Marking.** Monte Carlo model comparison 25%. Position analysis 15%. AlphaLab and agent integration 15%. Strategy backtest 25%. Reports, figures and interpretation 20%.
