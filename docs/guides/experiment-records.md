# Experiment records and serial execution

`POST /api/experiments` creates a recorded experiment, immutable source
candidates, and one planned validation trial per candidate in one transaction.
It does not schedule backtests, select a winner, or run the test period.
`GET /api/experiments?project_id=...` lists records; `GET /api/experiments/{id}`
returns the protocol, candidates, and all trials regardless of status.

Example request (replace the project and revisions with existing ones):

```json
{
  "project_id": "research",
  "strategy_id": "main",
  "base_revision": 1,
  "validation_revision": 1,
  "candidates": [
    {"momentum_20d": {"window": 10}},
    {"momentum_20d": {"window": 30}}
  ],
  "validation": {"start": "2024-01-01", "end": "2024-12-31"},
  "test": {"start": "2025-01-01", "end": "2025-12-31"},
  "selection_metric": "sharpe",
  "selection_direction": "maximize",
  "tie_break": "candidate_order",
  "max_trials": 3,
  "max_concurrency": 1,
  "confirm_python_execution": true
}
```

Candidate validation probes execute trusted local Python, so creation requires
the same explicit confirmation as other Python execution endpoints. Creation
requires an existing validation package; open/save the project's validation
workspace first if it has none. Invalid inputs return 422, missing packages
return 404, and missing execution confirmation returns 409.

Optional `training` has the same start/end shape. Dates are inclusive: training
must end before validation starts, and validation must end before test starts.
The trial budget must cover all validation candidates and one reserved test
trial. Test trials are created only after validation selection. The serial
orchestrator enforces this count budget and one outstanding job per experiment.

Project settings (including existing benchmark and cost settings), base source
hash, validation source/version, and environment information are copied into the
protocol. Source-identical candidates reuse immutable packages, while each
experiment keeps independent trial identifiers. Candidates edit function defaults;
explicit arguments in strategy code can override those defaults. Parameter
influence is not automatically certified.

`data_manifest_id` is an optional reference for the snapshot interface in #15.
Its presence is marked `unverified`; absence is marked `missing`. Neither state
claims reproducibility after market-data updates. Date validation alone does not
establish PIT correctness or prevent arbitrary Python from accessing future data.
Queries retain failed, cancelled, and interrupted rows. There is no cancellation
endpoint, model-fitting stage, rolling-window orchestration, or frontend yet.

## Run a recorded experiment

Call `POST /api/experiments/{id}/run` with
`{"confirm_python_execution": true}`. It returns 202 and starts background
progression through the existing `BacktestJobManager` and canonical engine.
Creation alone still never starts backtests. Experiments with a `training` window
are rejected at start (422): fitting is not implemented and must not be silently
skipped. This increment supports fixed source-parameter candidates.

All validation candidates run serially using their frozen source, validation
revision and project settings. Failed candidates retain their error and do not
prevent other candidates from running. Selection requires reliable execution,
no reported research-invalid reasons, and a finite value of the chosen metric.
The saved direction determines the winner; exact ties use candidate order.
No eligible candidate means a failed experiment without a test trial. Exactly
one winner is then evaluated over the test dates; its score never changes the
validation selection. Test failure does not trigger selection of another winner.

Job insertion and trial linkage commit together. Repeated starts and concurrent
progression reuse the same job IDs, including after reopening the database.
After a process restart, call the same run endpoint to resume orchestration;
completed experiments return their existing state instead of rerunning the test.
The existing backtest manager may independently recover previously queued jobs.
No automatic retry is created for failed trials. The budget counts attempted
jobs, including failures; it does not impose a wall-clock deadline or forcibly
terminate user Python.

Read `execution.status`, `execution.selection` (scores and exclusions), and each
trial's status/job/result/error. The frozen protocol's `execution_status` describes
its creation state only. `succeeded` means the workflow completed, not that the
strategy is profitable or investment-valid. Execution explicitly reports
`data_mode=current_runtime` and `out_of_sample_verified=false`: data snapshots,
full PIT auditing, and protection against arbitrary Python accessing held-out
data are not implemented. Runtime partitions can change between trials; results
must not be presented as a frozen-data out-of-sample finding.
