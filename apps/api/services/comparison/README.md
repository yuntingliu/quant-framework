# Comparison runner (Issue #8)

Compares agent configurations A/B/C on the same tasks and records one `TrialRecord` per trial:
initial state, isolation/reset, execution status, events, limits, budget and result summary.

Everything in this folder is a thin harness. It does not add a financial calculation or backtesting
engine; configuration B calls the same AlphaLab endpoints that the Conexus tools already use.

## Running it

There is no CLI. Drive it from Python, from the repository root, with the project venv:

```powershell
.venv\Scripts\python.exe -c "from apps.api.services.comparison import ComparisonRunner, Config; r = ComparisonRunner().run([Config.A, Config.B, Config.C]); print(len(r))"
```

```python
from apps.api.services.comparison import ComparisonRunner, Config, Limits, create_adapter

runner = ComparisonRunner()          # default TaskLoader = the built-in stub tasks
records = runner.run(
    [Config.A, Config.B],            # configurations to compare
    task_subset=["stub_strategy_backtesting"],
    repetitions=3,
    seed=42,                         # reproducible interleaved order
    use_doubles=True,                # default: test doubles only
    limits=Limits(time_s=120, steps=40),
    adapters={config: create_adapter(config) for config in (Config.A, Config.B)},
    artifact_dir="scratch/comparison-runs",
)
for rec in records:
    print(rec.trial_id, rec.config.value, rec.task_id, rec.execution["status"], rec.is_complete)
```

`record.to_dict()` returns the serializable trial record. Omitting `adapters` keeps the runner
bookkeeping-only (no adapter is executed).

## Task subsets

The only task source today is `TaskLoader`'s built-in stubs: one placeholder task per category
(`data_diagnosis_repair`, `factor_implementation`, `strategy_backtesting`,
`risk_execution_explanation`, `result_auditing`, `failure_recovery`), with ids of the form
`stub_<category>`.

- `task_subset=None` or an empty list runs every task.
- `task_subset=[...]` runs only the matching `task_id`s; unknown ids are silently skipped, so a
  typo yields fewer trials, not an error.
- Pass your own tasks with `ComparisonRunner(TaskLoader([Task(...), ...]))`.

The real task manifest format is still open (Issue #7), which is why only stubs exist.

## Repetitions and ordering

`repetitions=N` multiplies every (config, task) pair by N. Trial counts are
`N * len(configs) * len(tasks selected)`, e.g. 2 configs x 6 stub tasks x 2 repetitions = 24.

With `interleave=True` (the default) the plan is shuffled so configurations are mixed in time, and
the seed is recorded in every record as `reproducibility.interleave_seed`. Pass `seed=` to pin it;
when omitted a random seed is generated and recorded, so any run can be replayed:

```python
records = runner.run([Config.A, Config.B], repetitions=2, seed=7)
replay = ComparisonRunner().run([Config.A, Config.B], repetitions=2, seed=7)  # same order
```

`reproducibility` also holds `repetition_index`, `repetition_count`, `plan_size` and the trial's
`interleaved_order` (its position in the shuffled plan), so the full order can be reconstructed from
the records. With `interleave=False` the declared order is kept and `interleave_seed` is `None`.

## Limits, timeouts and cancellation

Limits are enforced per trial by the runner, which consumes the adapter's event stream from a worker
thread and checks the deadline, the cancel flag and the counters between events:

| Field | Enforced from | Resulting status |
|---|---|---|
| `time_s` | wall clock | `timeout` (`errors[0].code == "TIMEOUT"`) |
| `steps` | number of events | `failed`, `LIMIT_EXCEEDED` |
| `tokens` | `event["usage"]["total_tokens"]` | `failed`, `LIMIT_EXCEEDED` |
| `cost_usd` | `event["cost_usd"]` | `failed`, `LIMIT_EXCEEDED` |

Every outcome still produces a closed record: `is_complete=True`, `timestamps.end` and
`timestamps.duration_s` set, and an entry in `results.errors`.

Cancel a run from another thread either with an event or with the runner itself; the trial in flight
is recorded as `cancelled` and the remaining plan is skipped:

```python
runner = ComparisonRunner()
threading.Timer(30, runner.cancel).start()
records = runner.run([Config.A], adapters={Config.A: create_adapter(Config.A)}, limits=Limits(time_s=120))
```

On completion, timeout, cancellation or limit breach the runner calls `adapter.stop()`, then waits at
most one second for the worker. An adapter that blocks inside a single event and ignores `stop()`
leaves a daemon thread running until it returns, but the record is still closed.

## Persistence

Each finished trial is written to JSON with `redact_obj` applied automatically:

- with `artifact_dir=...`, to `<artifact_dir>/<trial_id>.json`;
- otherwise to `<trial runtime dir>/trials/<trial_id>.json` (removed by `cleanup=True`, the default).

The in-memory record keeps the real artifact path in `results.artifacts`; in the file that path is
redacted to `<abs-path>`, like every other absolute path and any token/secret-looking value. Set
`cleanup=False` to inspect the default location, and call `runner.reset_mgr.cleanup()` afterwards.

## Enabling real runs (opt-in plus a stated budget)

Default is `use_doubles=True`. Nothing calls a model, a search API or a paid service in that mode.

A run is treated as "real" only via `use_doubles=False`, and it is then refused unless both of these
are supplied:

```python
runner.run(
    [Config.A],
    use_doubles=False,
    budget_opt_in=True,          # explicit human opt-in
    total_budget_usd=5.0,        # stated budget, required
)
```

`BudgetGuard.check_real_run()` raises `Real runs require explicit opt-in` when `budget_opt_in` is
false and `Real runs require a stated budget` when `total_budget_usd` is `None`. The failure is
recorded instead of raised: the trial ends `failed` with `results.errors[0].code == "BUDGET_OR_OPTIN"`.
Adapters that report spend through `BudgetGuard.add_cost()` fail with `BUDGET_EXCEEDED` once the
total is passed, and per-event `cost_usd` values are summed into `rec.budget.spent_usd`.

Opting in does not by itself make a live run happen: the adapters in this folder still emit scripted
events (see Known limits). Real execution wiring is the remaining work.

## How A/B/C differ

| | A | B | C |
|---|---|---|---|
| Agent | OpenCode | OpenCode | Conexus single agent |
| Financial tools | none (SDK/project docs only) | yes, loads `Project-Files.tool.json` and `Project-Run.tool.json` from `integrations/conexus/alphalab-research-agent/tools/` | yes (existing Conexus agent) |
| `create_adapter(...)` | `OpenCodeAdapter(Config.A)` | `OpenCodeAdapter(Config.B)` | `ConexusAdapter()` |
| `adapter_type` in record | `opencode` | `opencode` | `conexus` |
| `get_capabilities()` | `has_financial_tools=False` | `has_financial_tools=True`, `loads_tool_jsons=True` | `has_financial_tools=True` |
| Financial entry points | `[]` | parsed from the tool JSONs | parsed from the same tool JSONs |

The entry-point list is produced by `extract_tool_endpoints()`, which scans the `call()`/`request()`
invocations in the tools' `code` and resolves string concatenations, path-prefix variables and
`?`/`:` alternatives into templates such as `/api/backtests/jobs/{arg}/wait`. It currently yields 41
`(method, endpoint)` pairs. B and C are both derived from those files; if a tool changes an endpoint
the contract test fails until `EXPECTED_ENDPOINTS` in `tests/comparison/test_contract_b_loads_tools.py`
is updated. B and C raise `FileNotFoundError` if a tool file is missing rather than quietly using a
stale list. `Config.D` exists as a placeholder and `create_adapter(Config.D)` raises `ValueError`.

**What is NOT matched across A/B/C**

- **Model and provider:** not pinned or recorded; each side inherits its own default.
- **Prompt / system instructions:** A and B share the OpenCode adapter but the prompt is not
  normalised; C uses whatever the Conexus agent uses.
- **Tool surface:** deliberately different (A has no financial tools). B and C now share the same
  entry-point list, but how they reach it (MCP wrapper vs Conexus agent) is not normalised.
- **Harness behaviour:** the OpenCode and Conexus adapters are separate implementations with their
  own setup/stop paths, event vocabularies and retry behaviour. Nothing normalises event shape
  beyond the shared `TrialRecord` envelope.
- **Cache state and warm starts:** every trial is recorded as cold start with an empty cache, but
  that is asserted by the runner, not measured.
- **Limits:** the runner enforces the same limits for every configuration, but adapters only respect
  them through the stop/stream mechanism; an adapter that ignores `stop()` still leaves work running.
- **Task difficulty and fixtures:** stub tasks carry no starting files or fixtures, so all three
  configurations face the same empty starting state.
- **Scoring:** there is no scoring or pass/fail evaluator; `pass_conditions` are stored but never
  checked. Comparisons are by inspection of the records.

## Known limits

- **Tested only against test doubles.** The OpenCode adapter and the A/B/C runs have only been
  tested against test doubles (scripted events, fake/hanging adapters and stub tasks), never against
  a live `opencode` process and never against a real Conexus host. No real model, search API or paid
  service was called.
- The adapters are scaffolds: `run()` yields synthetic events (`opencode.start`, `opencode.done`,
  `conexus.start`, `conexus.done`, ...) and does not spawn a process, stream a real event log or
  parse a real OpenCode event log.
- No token or cost accounting is captured from a real run; `resources.tokens` and
  `resources.cost_usd` only reflect values present in emitted events.
- Redaction is applied automatically to the persisted JSON, but the in-memory `TrialRecord` keeps
  raw values; call `redact_obj(record.to_dict())` before logging one yourself.
- Reset is a temp runtime dir plus a fresh stub project id (`method` string mentions
  `sdk-v1-default`, but no real project is created). `ALPHALAB_RUNTIME_DIR`, `ALPHALAB_CACHE_DIR`,
  `CONEXUS_LOCAL_ROOT` and `ALPHALAB_MODEL_PROVIDERS_FILE` are the isolated paths; temp dirs are
  removed at the end of a run unless `cleanup=False`.
- Records are persisted one file per trial; there is no aggregate run index or database.
- Task manifest loading is stubbed pending Issue #7, so subsets only work against the built-in stub
  ids.
- `interleave_seed` is recorded, but the code that builds the plan must stay unchanged for a replay
  to match.

## Tests

```powershell
.venv\Scripts\python.exe -m pytest tests/comparison -q
```
