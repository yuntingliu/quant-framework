# Experiment records (initial implementation)

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
trial. Test trials are not created yet. Selection settings and budgets are
recorded only; execution and enforcement belong to the future orchestrator.

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
There is no scheduler, status-update API, cancellation endpoint, or frontend in
this increment. Trial rows reserve job/result references and failure details for
the orchestrator; queries retain failed, cancelled, and interrupted rows.
