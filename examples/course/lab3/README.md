# Lab 3 — Build and backtest an options strategy

[Open the assignment](assignment.md) for the financial questions, strategy rules, code layout, deliverables and marking. Run the examples below from the AlphaLab checkout root on the `option` branch.

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
