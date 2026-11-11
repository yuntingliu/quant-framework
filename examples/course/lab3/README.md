# Lab 3 — Build and backtest an options strategy

[Open the assignment](assignment.md) for the financial questions, strategy rules and marking. Fork the course repository with its `option` branch or use your assigned course copy. Replace `YOUR-GITHUB-USER` below with the owner of your fork.

~~~powershell
git clone -b option https://github.com/YOUR-GITHUB-USER/quant-framework.git
cd quant-framework
git switch -c lab3
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
- Connect the lab registry to AlphaLab and pass a schema-driven Monte Carlo call through an agent. Generate one strangle position comparison and write a reusable report skill.
- Build `options_homework/backtest.py`, `options_homework/backtest_report.py` and `run_backtest.py`. Choose a structure, define its entry and exit rules, and freeze the design before the historical holdout. An ETF share leg and a timed-versus-untimed comparison are optional.
- Link the reports, ledgers, figures and [AI audit log](ai-audit-log.md) from your pull request.

The input registration, fixture validation, report and figure writer, acceptance checker and daily data reader are supplied. The finance calculations, historical simulator and analysis are the assignment.


## Repository layout

Keep the lab code in the example package. Run the commands from the AlphaLab checkout root.

~~~text
quant-framework/
  alphalab/tools/data.py           # add the lab registration on your local branch
  examples/course/lab3/
    strangle.json, reference_checks.json
    tests/                        # add independent automated checks
    monte_carlo_panel.py, agent_tool.py, mc_call_example.json
    run_report.py, verify_submission.py, inspect_history.py
    options_homework/
      mc_pricing.py               # supplied simulation adapter
      calculations.py             # implement payoff, Greeks and stress
      models.py, tools.py         # typed requests and tool registration
      history.py                  # supplied option, ETF and signal input readers
      report_figures.py           # supplied payoff and stress charts
      position_comparison.py     # supplied comparison report writer
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

Give an agent the exported `name`, `description` and `input_schema` together with a fitted model and held-out quote from Question 1. Ask it to produce one JSON object with `name` and `arguments`, using only the fields in the schema. Save that call. Pass it through `agent_tool --call` or use `agent_tool.invoke` as the agent's callable adapter. The adapter places `arguments` under the API's `input` key and records the response. Give the response body back to the agent and ask it to interpret `result.price`, `standard_error`, `positive_payoffs` and `warning`. Save the prompt and valid tool-call trace in the report bundle. Add an automated check that the API rejects `paths: 1` with `422`. This workflow works with a local AlphaLab backend and the coding agent you already use.

Run the checker after implementing and registering the three strangle functions. Generate one draft report using the supplied case. The comparison mode computes ten pairs locally and resizes only the signed quantities to two pairs for the API run. Install Matplotlib with `python -m pip install matplotlib` if the course environment lacks it.

~~~powershell
python -m examples.course.lab3.verify_submission
python -m examples.course.lab3.run_report --compare-sizes --api http://127.0.0.1:8765 --out runs/strangle
~~~

The runner writes `runs/strangle/report.json` with `ten_pairs` and `two_pairs`. Each contains `inputs` and `results`. The same folder contains one `report.md` with side-by-side positions, stress results and budget checks. Keep the linked figures with the report.

Write your report skill at `skills/strangle-position-report/SKILL.md` within the Lab 3 example. Give it a name, a short description, the JSON fields it should read, the checks to run, and the output it should draft. Use the [report template](report_template.md) for the sections and the supplied [audit skill](skills/options-risk-report-audit/SKILL.md) for independent checks. Ask your agent to apply your skill to `report.json` and draft `interpretation.md`. Verify the calculations and make the final size decision yourself.

~~~powershell
python -m examples.course.lab3.run_report --compare-sizes --api http://127.0.0.1:8765 --interpretation interpretation.md --out runs/strangle
~~~

Include the single position report folder in the report bundle and commit your skill in the PR. Record what you checked or corrected in the AI audit log.

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

## Submit a GitHub pull request

Create a lab branch from `option` in your own fork or course copy and open a PR back into that copy's `option` branch. Use the [Lab 3 PR template](pull_request_template.md) as the description. Commit the lab source, registry change, report skill and tests under `examples/course/lab3/tests/`. Add at least one independent check for a sign or multiplier error and one check for the invalid Monte Carlo input. Keep checks runnable with the bundled inputs or synthetic fixtures.

The **Lab 3 PR checks** workflow runs the supplied checker and the student tests. Enable GitHub Actions in your copy if needed. Run the same commands from the checkout root before opening the PR.

~~~powershell
python -m examples.course.lab3.verify_submission
python -m pytest examples/course/lab3/tests -q
~~~

Link one report bundle in the PR description where the grader can access it. Include the Monte Carlo comparison, one position comparison with its JSON and charts, the valid agent tool-call trace, the backtest report and ledgers, and your AI audit log. Keep raw Ricequant data, credentials and generated run folders out of Git. Submit the PR URL at [submission.acetoken.net](https://submission.acetoken.net).
