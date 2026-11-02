# Comparison runner (Issue #8)

Compares agent configurations A/B/C on the same tasks and records one `TrialRecord` per trial:
initial state, isolation/reset, execution status, events, limits, budget and result summary.

Everything in this folder is a thin harness. It does not add a financial calculation or backtesting
engine; configuration B talks to the existing AlphaLab endpoints that the Conexus tools already use.

## Running it

There is no CLI. Drive it from Python, from the repository root, with the project venv:

```powershell
.venv\Scripts\python.exe -c "from apps.api.services.comparison import ComparisonRunner, Config; r = ComparisonRunner().run([Config.A, Config.B, Config.C]); print(len(r))"
```

```python
from apps.api.services.comparison import ComparisonRunner, Config

runner = ComparisonRunner()          # default TaskLoader = the built-in stub tasks
records = runner.run(
    [Config.A, Config.B],            # configurations to compare
    task_subset=["stub_strategy_backtesting"],
    repetitions=3,
    use_doubles=True,                # default: test doubles only
)
for rec in records:
    print(rec.trial_id, rec.config.value, rec.task_id, rec.execution["status"], rec.is_complete)
```

`record.to_dict()` returns the serializable trial record.

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

## Repetitions

`repetitions=N` multiplies every (config, task) pair by N. Trial counts are
`N * len(configs) * len(tasks selected)`, e.g. 2 configs x 6 stub tasks x 2 repetitions = 24.

Each repetition gets its own trial id, `reproducibility.repetition_index` and a fresh isolated
environment. With `interleave=True` (the default) the plan is shuffled so configurations are mixed
in time; the position in that shuffled plan is stored as `reproducibility.interleaved_order`. The
shuffle seed is not recorded, so the order is not reproducible from the record alone.

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
recorded instead of raised: the trial ends `failed` with
`results.errors[0].code == "BUDGET_OR_OPTIN"`. `BudgetGuard.add_cost()` raises `Budget exceeded`
once spend passes the total, and `spent_usd` is tracked per trial.

Opting in does not by itself make a live run happen: the adapters in this folder still emit
scripted events (see Known limits). Real execution wiring is the remaining work.

## How A/B/C differ

| | A | B | C |
|---|---|---|---|
| Agent | OpenCode | OpenCode | Conexus single agent |
| Financial tools | none (SDK/project docs only) | yes, loads `Project-Files.tool.json` and `Project-Run.tool.json` from `integrations/conexus/alphalab-research-agent/tools/` | yes (existing Conexus agent) |
| `create_adapter(...)` | `OpenCodeAdapter(Config.A)` | `OpenCodeAdapter(Config.B)` | `ConexusAdapter()` |
| `adapter_type` in record | `opencode` | `opencode` | `conexus` |
| `get_capabilities()` | `has_financial_tools=False` | `has_financial_tools=True`, `loads_tool_jsons=True` | `has_financial_tools=True` |
| Financial entry points | `[]` | derived from the tool JSONs (`/api/strategy/projects` GET, `/api/backtests/jobs` POST, `/api/backtests/jobs/{job_id}/wait` GET, `/api/backtests/{backtest_id}/summary` GET) | declared by the Conexus adapter |

B must load the tool JSON files rather than copy them; `adapters.py` raises `FileNotFoundError` if
either file is missing, and `tests/comparison/test_contract_b_loads_tools.py` checks the files exist
and that B's endpoints include the ones the tools use. `Config.D` exists as a placeholder and
`create_adapter(Config.D)` raises `ValueError`.

**What is NOT matched across A/B/C**

- **Model and provider:** not pinned or recorded; each side inherits its own default.
- **Prompt / system instructions:** A and B share the OpenCode adapter but the prompt is not
  normalised; C uses whatever the Conexus agent uses.
- **Tool surface:** deliberately different (A has no financial tools). Only B and C are meant to be
  comparable on financial entry points, and even there B's list is declared by the adapter while
  C's is a static list.
- **Harness behaviour:** the OpenCode and Conexus adapters are separate implementations with their
  own setup/stop paths, event vocabularies and retry behaviour. Nothing normalises event shape
  beyond the shared `TrialRecord` envelope.
- **Cache state and warm starts:** every trial is recorded as cold start with an empty cache, but
  that is asserted by the runner, not measured.
- **Time, token, cost and step limits:** `Limits` fields default to `None` (unlimited) and are not
  enforced by the adapters.
- **Task difficulty and fixtures:** stub tasks carry no starting files or fixtures, so all three
  configurations face the same empty starting state.
- **Scoring:** there is no scoring or pass/fail evaluator; `pass_conditions` are stored but never
  checked. Comparisons are by inspection of the records.

## Known limits

- **Tested only against test doubles.** The OpenCode adapter and the A/B/C runs have only been
  tested against test doubles (scripted events and stub tasks), never against a live `opencode`
  process and never against a real Conexus host. No real model, search API or paid service was
  called.
- The adapters are scaffolds: `run()` yields synthetic events (`opencode.start`, `opencode.done`,
  `conexus.start`, `conexus.done`) and does not spawn a process, stream real events or parse a real
  OpenCode event log.
- `stop()` is a no-op, and timeouts/cancellation are not implemented. Statuses `timeout`,
  `cancelled` and `interrupted` exist in `Status` and are accepted by the records, but only
  `completed` and `failed` are currently produced.
- No token or cost accounting is captured from a run; `resources` stays empty and `budget.spent_usd`
  only moves when an adapter calls `add_cost()` (none do yet).
- Redaction (`redact_text` / `redact_obj`) exists and is unit-tested, but the runner does not apply
  it automatically to recorded events; callers must redact before persisting records.
- Reset is a temp runtime dir plus a fresh stub project id (`method` string mentions
  `sdk-v1-default`, but no real project is created). `ALPHALAB_RUNTIME_DIR`, `ALPHALAB_CACHE_DIR`,
  `CONEXUS_LOCAL_ROOT` and `ALPHALAB_MODEL_PROVIDERS_FILE` are the isolated paths; temp dirs are
  removed when the run finishes.
- Records are in memory only (`TrialRecorder.records`); nothing is written to disk.
- Task manifest loading is stubbed pending Issue #7, so subsets only work against the built-in stub
  ids.

## Tests

```powershell
.venv\Scripts\python.exe -m pytest tests/comparison -q
```
