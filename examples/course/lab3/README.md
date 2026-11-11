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
- Build `options_homework/backtest.py`, `options_homework/backtest_report.py` and `run_backtest.py`. Choose a structure and test it with and without market timing. Use a moving-average signal, a point-in-time macro input and a volatility indicator. Keep execution and costs matched across both runs and freeze the rule before the historical holdout.
- Submit the required ledgers, figures, decisions and [AI audit log](ai-audit-log.md).

The input registration, fixture validation, report formatter, acceptance checker and daily data reader are supplied. The finance calculations, historical simulator and analysis are the assignment.


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
      history.py                  # supplied daily chain and signal input readers
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

In a second terminal at the checkout root, run the checker and the two reports. Use the supplied case for ten pairs. Copy `examples/course/lab3/strangle.json` to `strangle-two.json` in the checkout root and set both quantities to `-2`. Write `ten.md` and `two.md` for your interpretations. Use draft text for the first run, then update it with your conclusions and regenerate the reports.

~~~powershell
python -m examples.course.lab3.verify_submission
python -m examples.course.lab3.run_report ``
  --interpretation ten.md --out runs/ten-pairs
python -m examples.course.lab3.run_report strangle-two.json ``
  --interpretation two.md --out runs/two-pairs --api http://127.0.0.1:8765
~~~

## Historical report

Run the history inspection command above to check option-chain and lagged-yield coverage. After implementing the simulator and the report runner, generate both timing variants with one command.

~~~powershell
python -m examples.course.lab3.run_backtest --out runs/history
~~~
