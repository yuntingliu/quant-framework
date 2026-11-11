# Lab 3 — Build and backtest an options strategy

[Open the assignment](assignment.md) for the financial questions, strategy rules, code layout, deliverables and marking. Run the examples below from the AlphaLab checkout root on the `option` branch.

~~~powershell
git clone -b option https://github.com/yuntingliu/quant-framework.git
cd quant-framework
~~~

Activate the course Python environment, then run:

~~~powershell
python -m examples.course.lab3.run_report --inputs-only
python -m examples.course.lab3.inspect_history
~~~

The first command validates the bundled strangle and invokes the registered `options_lab.case_inputs` tool. It runs without market data. The second checks coverage in the local course store. Use `--underlying 510300.XSHG` or another Ricequant ETF symbol to check a different market. The command reads the populated AlphaLab runtime store. See the [data operations guide](../../../docs/04_DATA_OPERATIONS.md).

## What to implement

- Fit and compare pricing models on held-out option strikes for Question 1.
- Fill in `options_homework/calculations.py` and register the expiry, Greeks and stress functions in `options_homework/tools.py`. Run `python -m examples.course.lab3.verify_submission` after implementing them.
- Connect the lab registry to AlphaLab and pass a schema-driven Monte Carlo call through an agent. Generate both strangle position reports.
- Build `options_homework/backtest.py`, `options_homework/backtest_report.py` and `run_backtest.py`. Choose a structure, define its entry and exit rules, and freeze the design before the historical holdout. An ETF share leg and a timed-versus-untimed comparison are optional.
- Submit the required ledgers, figures, decisions and [AI audit log](ai-audit-log.md).

The input registration, fixture validation, report and figure writer, acceptance checker and daily data reader are supplied. The finance calculations, historical simulator and analysis are the assignment.


## Repository layout

Keep the lab code in the example package. Run the commands from the AlphaLab checkout root.

~~~text
quant-framework/
  alphalab/tools/data.py           # add the lab registration on your local branch
  examples/course/lab3/
    strangle.json, reference_checks.json
    monte_carlo_panel.py, agent_tool.py, mc_call_example.json
    run_report.py, verify_submission.py, inspect_history.py
    options_homework/
      mc_pricing.py               # supplied simulation adapter
      calculations.py             # implement payoff, Greeks and stress
      models.py, tools.py         # typed requests and tool registration
      history.py                  # supplied option, ETF and signal input readers
      report_figures.py           # supplied payoff and stress charts
      backtest.py                 # write strategy, timing, fills, cash and margin
      backtest_report.py          # write summaries and figures
    run_backtest.py               # write the historical report runner
  runs/                           # generated outputs
~~~

## Question 1 · Monte Carlo on Ricequant ETF options

In AlphaLab, open **Options → Data management** and select the ETF symbols and dates. The `rq.etf_options` recipe uses Ricequant for dated contracts, daily bars, dated strikes and multipliers, the government yield curve and underlying history. Use the populated course store or your authorized Ricequant connection. Start with one date and inspect coverage before requesting the historical span.

~~~powershell
python -m examples.course.lab3.monte_carlo_panel --out runs/mc/panels.json
python -m examples.course.lab3.inspect_history --underlying 510300.XSHG
~~~

The panel command defaults to CSI 300 and ChiNext. Repeat `--underlying` to select your own pair, for example `--underlying 510300.XSHG --underlying 588000.XSHG`. It exports one chain slice per ETF, with eligible standard European contracts that traded and had a positive close. The forward is the median of `K + exp(r*T)*(C-P)` at the three put–call pairs closest to spot. The discount rate comes from the dated curve. The implied continuous yield satisfies `q = r - log(F/S)/T`. Price puts with `K < F` and calls with `K > F`. The calibration subset includes OTM strikes within 0.05 absolute log-moneyness and any OTM carry anchors. The holdout contains farther strikes without anchors.

The supplied `mc_pricing.py` wraps `alphalab.options.monte_carlo`. GBM sets jump intensity to zero. Under a Merton specification, the terminal log return is

$$\log(S_T/S_0) = (r-q-\lambda\kappa-\sigma^2/2)T + \sigma\sqrt{T}Z
  + N\mu_J+\sqrt{N}\sigma_J Z_J,$$

where `N` is Poisson with mean `lambda*T`, `Z` and `Z_J` are independent standard normals, and `kappa = exp(mu_J + sigma_J**2/2) - 1`. The drift adjustment preserves the model forward under the pricing measure. [Merton (1976)](https://doi.org/10.1016/0304-405X%2876%2990022-2).

Fit each ETF separately. One reproducible approach minimizes squared price errors on the supplied calibration subset, with bounds set in advance. Fit diffusion volatility under both models. For the jump model, fix `jump_mean=-0.10` and `jump_volatility=0.10` and fit jump intensity. Use the analytic `black_scholes` and `merton_prices` facade functions during fitting to avoid optimizing simulation noise. Then call the Monte Carlo engine at the frozen parameters. Report model versus market error separately from simulation versus analytic-price error. If the result warns about sparse payoff hits, report the hit count and avoid treating a zero estimate or an unreliable normal interval as a precise price.

## Question 2 · Agent call and position report

In `options_homework/tools.py`, register `options_lab.expiry`, `options_lab.greeks` and `options_lab.stress` with `ReportRequest`. Follow the supplied `options_lab.case_inputs` example. In `alphalab/tools/data.py` add these lines inside `create_data_tool_registry`, before `return registry`.

~~~python
from examples.course.lab3.options_homework.tools import register_lab_tools

register_lab_tools(registry)
~~~

Start the local API and use `GET /api/agent/data-tools` to verify the names and input schemas. To invoke a tool, use `POST /api/agent/data-tools/{tool_name}/invoke` with `{"input": <the case JSON>}`. A zero quantity should return a validation error.

~~~powershell
python -m alphalab.cli dev serve --port 8765 --agent off
~~~

The starter's `register_lab_tools` already shows the Monte Carlo `ToolSpec` and its `MonteCarloRequest`. The local API uses the patch above to discover it. In a second terminal, export its schema, then replay the example tool call.

~~~powershell
python -m examples.course.lab3.agent_tool --out runs/mc/tool-schema.json
python -m examples.course.lab3.agent_tool --call examples/course/lab3/mc_call_example.json --out runs/mc/example-trace.json
~~~

Give an agent the exported `name`, `description` and `input_schema` together with a fitted model and held-out quote from Question 1. Ask it to produce one JSON object with `name` and `arguments`, using only the fields in the schema. Save that call. Pass it through `agent_tool --call` or use `agent_tool.invoke` as the agent's callable adapter. The adapter places `arguments` under the API's `input` key and records the response. Give the response body back to the agent and ask it to interpret `result.price`, `standard_error`, `positive_payoffs` and `warning`. Submit the prompt, tool call, request and response, plus your own verification. Repeat once with `paths: 1` and record the `422`. This workflow works with a local AlphaLab backend and the coding agent you already use.

In a second terminal at the checkout root, run the checker and the two reports. Use the supplied case for ten pairs. Copy `examples/course/lab3/strangle.json` to `strangle-two.json` in the checkout root and set both quantities to `-2`. Copy the [report template](report_template.md) to `ten.md` and `two.md`. Use draft text for the first run, then add your interpretation and regenerate the reports. Install Matplotlib with `python -m pip install matplotlib` if the course environment lacks it.

~~~powershell
python -m examples.course.lab3.verify_submission
python -m examples.course.lab3.run_report `
  --interpretation ten.md --out runs/ten-pairs
python -m examples.course.lab3.run_report strangle-two.json `
  --interpretation two.md --out runs/two-pairs --api http://127.0.0.1:8765
~~~

Each run writes `report.md`, `report.json`, `expiry_payoff.png` and `stress_comparison.png`. The Markdown includes the leg inputs, expiry payoff, Greeks with units, scenario errors and a loss-budget table. The JSON holds the inputs and tool outputs for reproduction. Submit the two folders with their figures, the test evidence and your size decision. Use the [AI audit skill](skills/options-risk-report-audit/SKILL.md) by asking your assistant to read it and audit the two folders. Record what you checked in the AI audit log.

## Question 3 · Historical backtest

Run `inspect_history --underlying` for the selected ETF to check course data coverage. Use `underlying=` with all three history adapters. Choose an ETF with contracts and a usable holdout. Use options and optionally ETF shares. The starter provides these read-only adapters.

| Function in `options_homework/history.py` | Use |
| --- | --- |
| `load_history` | Raw spot and daily option chains. The ETF session index also reveals dates with missing chains. |
| `load_signal_inputs` | Adjusted ETF closes and lagged 10-year Chinese government yields. Include warm-up dates for moving averages and realized volatility. |
| `load_etf_execution_prices` | Raw open and close proxies from the ETF's first and last five-minute bars. Align to the full session index and log missing prices. |

For example, a covered call can hold ETF shares and write an out-of-the-money call when its entry condition passes. Define the number of ETF units per short call, charge costs on both instruments and credit ETF cash distributions where applicable. A stop evaluated on end-of-day marked position P&L sends a close order for the next available open. Record the trigger date, fill date and actual loss. Treat a missing exit as unresolved until you can account for it. For a classroom short-option margin proxy, apply the [SSE short-leg formulas](https://english.sse.com.cn/markets/derivatives/overview/50etf/) using daily settlements and ETF closes. Document any spread offset separately.

For a timed entry, a moving average or trailing volatility can use data through the decision close. A macro series requires the release lag and original vintage if revised. If you choose the optional timed-versus-untimed comparison, run the same execution and capital rules for both.

Use one command to generate the historical report after implementing the runner.

~~~powershell
python -m examples.course.lab3.run_backtest --out runs/history
~~~
