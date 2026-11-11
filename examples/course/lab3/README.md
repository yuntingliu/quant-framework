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

The first command validates the bundled strangle and invokes the registered `options_lab.case_inputs` tool. It runs without market data. The second checks coverage in the local course store. It expects the SSE 50 ETF daily underlying history and dated option snapshots already available through AlphaLab. See the [data operations guide](../../../docs/04_DATA_OPERATIONS.md) for data setup.

## What to implement

- Fill in `options_homework/calculations.py` and register the expiry, Greeks and stress functions in `options_homework/tools.py`. Run `python -m examples.course.lab3.verify_submission` after implementing them.
- Follow the assignment’s local registry patch and API checks. Use the supplied report runner to generate Markdown and JSON for both position sizes.
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
    run_report.py, verify_submission.py, inspect_history.py
    options_homework/
      calculations.py             # implement payoff, Greeks and stress
      models.py, tools.py         # typed requests and registration
      history.py                  # supplied option, ETF and signal input readers
      report_figures.py           # supplied payoff and stress charts
      backtest.py                 # write strategy, timing, fills, cash and margin
      backtest_report.py          # write summaries and figures
    run_backtest.py               # write the historical report runner
  runs/                           # generated outputs
~~~

## Position report and local API

In `options_homework/tools.py`, register `options_lab.expiry`, `options_lab.greeks` and `options_lab.stress` with `ReportRequest`. Follow the supplied `options_lab.case_inputs` example. In `alphalab/tools/data.py` add these lines inside `create_data_tool_registry`, before `return registry`.

~~~python
from examples.course.lab3.options_homework.tools import register_lab_tools

register_lab_tools(registry)
~~~

Start the local API and use `GET /api/agent/data-tools` to verify the names and input schemas. To invoke a tool, use `POST /api/agent/data-tools/{tool_name}/invoke` with `{"input": <the case JSON>}`. A zero quantity should return a validation error.

~~~powershell
python -m alphalab.cli dev serve --port 8765 --agent off
~~~

In a second terminal at the checkout root, run the checker and the two reports. Use the supplied case for ten pairs. Copy `examples/course/lab3/strangle.json` to `strangle-two.json` in the checkout root and set both quantities to `-2`. Copy the [report template](report_template.md) to `ten.md` and `two.md`. Use draft text for the first run, then add your interpretation and regenerate the reports. Install Matplotlib with `python -m pip install matplotlib` if the course environment lacks it.

~~~powershell
python -m examples.course.lab3.verify_submission
python -m examples.course.lab3.run_report `
  --interpretation ten.md --out runs/ten-pairs
python -m examples.course.lab3.run_report strangle-two.json `
  --interpretation two.md --out runs/two-pairs --api http://127.0.0.1:8765
~~~

Each run writes `report.md`, `report.json`, `expiry_payoff.png` and `stress_comparison.png`. The Markdown includes the leg inputs, expiry payoff, Greeks with units, scenario errors and a loss-budget table. The JSON holds the inputs and tool outputs for reproduction. Submit the two folders with their figures, the test evidence and your size decision. Use the [AI audit skill](skills/options-risk-report-audit/SKILL.md) by asking your assistant to read it and audit the two folders. Record what you checked in the AI audit log.

## Historical report

Run the history inspection command above to check course data coverage. Use option contracts and optionally ETF shares. The starter provides these read-only adapters.

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
