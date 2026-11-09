# Explicit SQLite RQData execution preparation

Phase 4B.5B.2A is an internal, explicitly constructed integration. Normal
`apps.api.main:app`, existing JSON APIs and launchers remain unchanged. No
backend environment switch, configuration adoption, production activation,
deployment change or rollback mechanism is introduced.

```python
with ConfigurationBackend.sqlite(root, installation_id=known_id) as owner:
    app = create_configuration_app(owner, include_data=True)
    # Serve the app with ASGI lifespan enabled, then shut it down before owner.
```

The default factory still mounts no data routes. Opt-in requires the same
verified SQLite owner as model/search. Its lifespan starts and closes
`ConfigurationDataServices`; the scheduler holds a backend borrow until its
queue and active synchronous operations are drained. Owner close fails while
borrowed. Closing the service stops pending work, cancels owned children and
waits for their exit before releasing ownership. Existing cooperative JSON
startup/writer and maintenance leases remain excluded by the owning backend.
Non-Off SQLite search tests remain HTTP 503; RQData does not bypass that guard.

## Execution and credential lifetime

The existing `SyncJobManager` retains its default JSON path. Optional internal
runner and request-validation hooks allow the explicit binding to reuse its
queue, persisted operations, cancellation flags and recovery. Each admitted job
is tagged with its SQLite installation ID, without credentials. One parent
runtime queue lock covers admission, capture and child completion.
An SDK child also owns a distinct `captured-rq-execution` runtime lock before
announcing readiness. Restart admission waits on that lock before recovering
statuses, so parent queue-lock loss cannot let an old child overwrite a new
attempt's job record. Both admission waits are bounded to five seconds.
Credential-free plan workers skip that execution lock and do not write lock metadata.

After dequeue and lock admission, `RQJobConfiguration` reads one complete
SQLite snapshot and resolves its RQData selection. Installation ID, revision
and a fresh attempt ID are audit metadata; user/password/host remain private.
Saved incomplete or explicitly cleared profiles and `none` fail closed.
Environment intent can use only an explicitly supplied private environment
snapshot; no live environment credential resolution occurs. Saved selections
never borrow that snapshot. Default explicit environment is empty.

Each SDK execution uses a new `multiprocessing` **spawn** process, including
regular syncs, recipe execution and connection tests. A separate Python client
object is not treated as SDK isolation. The parent never initializes rqdatac.
Its single queue is supplemented by process isolation for synchronous probes
and independent installations that can execute concurrently.

The fixed worker target receives only non-secret root/identity/operation fields,
an inherited private pipe, and optional trusted dependency injection. It sends
`ready`; the authorized parent pushes one bounded JSON frame (32 KiB maximum)
containing the captured native connection. Credentials are not Process arguments,
command-line arguments, environment assignments, files or database job requests.
There is no listener/broker through which arbitrary workers can request other
installations. The frame repeats root hash, installation ID and revision, which
must match the spawn context. Result IPC is bounded to 1 MiB.

Before SDK or recipe calls, the worker restricts its environment to OS/interpreter
paths and limits plus the explicit runtime-root path; unrelated application,
Agent and provider tokens are not inherited by recipe code. Ambient proxies and
other application environment options are not part of this prepared SDK contract.
It blocks
legacy `from_env`/saved-settings credential acquisition before SDK initialization.
A private explicit client is used for every canonical call. All SDK/recipe
stdout and stderr, including native file-descriptor writes, are discarded in
the child; errors cross IPC as static status codes, never
original messages, traceback objects or exception chains. Known password matches,
including bounded percent-decoding, are rejected in recipe output metadata and
string data passed through canonical publish. Persisted job updates and recipe
audit requests are guarded too. Known native secrets are checked in submitted
requests and recipe source before persistence. Unknown secrets manually embedded
in source or metadata cannot be identified with certainty and are prohibited.
Only validated system installation/revision/attempt IDs and matching source
hashes are excluded from those comparisons; configurable identifiers remain
checked. Conservative substring checks can reject source containing a short
saved credential as ordinary text.

Timeout/cancellation is polled in the parent; cleanup joins, then terminates and
finally kills only that execution's owned child if necessary. A child pipe-EOF
watchdog exits its own process if its parent disappears while Python threads can
run. A native call holding the GIL can delay the watchdog; the child execution
lock still blocks new SDK admission. This is not atomic process-tree shutdown;
deployment must still drain and
verify all workers before backup or restore. The existing operations database
has finite SQLite busy waits, and the runtime queue admission wait is bounded.

## Recipes, planning and restart

`execute_data_recipe(execution=...)` provides the explicit internal execution
hook while its JSON default remains unchanged. The captured worker executes
the existing recipe engine. Its full RQData proxy aliases are replaced only
inside that one-shot child and restored on exit; the parent proxy is untouched.
Direct canonical `from_env` acquisition is blocked. No credentials are placed
in source, plan output, logs, reports or saved job requests.

Plan workers receive **no credentials** and have no enabled SDK client. Existing
`DataRecipeContext(mode="plan")` keeps canonical publication non-mutating. A
recipe attempting a provider call during planning fails instead of connecting.
Recipes remain trusted local Python, not a sandbox: deliberate direct filesystem,
network, subprocess or SDK manipulation is outside the canonical proxy contract.
The feature does not authorize untrusted recipe execution.

A recipe returning `RQSyncRequest` continues synchronization with the **same
captured client in the same process**, without a second credential lookup.
Queued jobs use the generation current when they actually begin execution.
Running jobs keep their capture despite profile/selection changes. Recovery
starts a new process and new capture/attempt, using current credentials and
existing validated dataset checkpoints/watermarks. Clearing credentials before
restart makes recovery fail; historical credentials are never restored. Jobs
without a SQLite admission tag or with a foreign installation ID are rejected,
not adopted as SQLite work. Completed/failed/cancelled work is not auto-retried.
Conversely, the default JSON scheduler and legacy recipe/sync runners reject
SQLite-tagged jobs; a restart in JSON mode cannot replay them with JSON credentials.

## Route and call inventory

| Existing entry point | JSON/default | Explicit SQLite composition |
| --- | --- | --- |
| `RQSyncService.run` / `RQAcquirer.from_env` | Existing lazy saved-profile/environment client | Explicit captured acquirer in a fresh child; from_env blocked |
| `SyncJobManager.submit`, `_schedule`, `run_now` | Existing single-thread scheduler | Admission tags; injected runner captures after dequeue |
| `submit_recipe`, `_run_recipe_job` | Existing spawn engine and JSON follow-up sync | Captured child executes recipe and any follow-up sync |
| `execute_data_recipe`, `_recipe_worker` | Existing JSON worker | Explicit execution hook bypasses the legacy worker |
| `RQDataAPI._module`, `context.rq`, module `rq` | Existing lazy from_env proxy | Child-local captured proxy; disabled for plan |
| `data_sync_service.test_connection` | Existing saved/JSON/environment client | Same `/api/data-sync/connection-test`, isolated selected-profile probe |
| `data_sync_service.plan_recipe`, project/draft helpers | Existing global workbench/project manager | Explicit root's operations/draft store; no global project lookup |
| `OperationsStore.recover_jobs`, cancellation | Existing recovery/cancel contract | Tagged jobs recapture; old/foreign jobs fail closed |
| `rq_profiles` readers/writers | Existing JSON behavior and cooperative leases | Native profile services only; no JSON reads/writes |

Opt-in routes cover data-source profile GET/PUT/selection; sync plan, connection
test, jobs/list/detail/cancel; recipe source PUT, plan and job submission. They
use the existing request/response shapes where practical and public job
projection plus opaque configuration capture metadata. Unsupported catalog,
workspace/template-management, health, validation and other workbench routes
remain unmounted (404); none delegates to a global JSON service. Recipe project
IDs are local draft namespaces in the explicit operations store, not a complete
strategy-project integration. No SQLite routes are mounted by normal main.py.

## Phase 4B.5B.2B prerequisites

The deployment manager still checks jobs through its existing API, stops web
and private services, snapshots runtime/config, writes pending JSON provider
settings, swaps releases and restores whole runtime/config directories. These
operations were inspected but **not modified or certified for SQLite**.

Before production use, 2B must make all relevant launchers, CLI paths, deployment
writers and backup/restore tools participate in maintenance/admission; stop
unmanaged and older writers; drain queues and verify owned child exits (including
after abrupt API death); preserve installation/root binding and native private
SQLite generations/journals; coordinate canonical data checkpoints with backups;
and define recovery of queued admissions. Do not rename/replace a runtime or
restore old credential-bearing generations while jobs remain alive. The EOF
watchdog is a cleanup aid, not a substitute for verified offline exclusion.

Durable backend activation, complete multi-process admission, credential-history
retention, rollback policy, authenticated Agent endpoint binding and end-to-end
cutover remain 4B.5B.3 work. No such mechanism is enabled here. Network filesystems
are unsupported. Tests use Windows spawn, real temporary stores and fake SDKs;
POSIX signal/termination and physical power-loss behavior remain unverified.
