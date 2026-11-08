# Inactive configuration runtime integration

Phase 4B.4 prepares explicit runtime bindings for the transactional store. Normal
Agent startup and RQData jobs still use JSON. This phase performs no adoption,
recovery, migration, import, or backend cutover.

## Agent composition

[`configuration-runtime.mjs`](../../integrations/conexus/configuration-runtime.mjs)
exports two internal factories:

- `createJsonAgentConfiguration(providerPath, createModelCompletion, options)`
  composes the existing JSON model and search adapters.
- `createSqliteAgentConfiguration(root, createModelCompletion, options)` owns one
  read-only `ConfigurationStoreReader` for an explicit installation root. Tests
  construct it directly. Both adapters use this reader; neither receives a JSON
  path or falls back to JSON when SQLite is unavailable.

The returned contract is `complete`, `webSearch`, `testSearch`,
`modelConfigured`, and idempotent `close`. Dependencies include a model-completion
factory and an optional fake `fetchImpl`. There is no backend-selection argument
on the production launcher, environment toggle, or global runtime switch.
[`local-host.mjs`](../../integrations/conexus/local-host.mjs) explicitly constructs
the JSON factory, including health/readiness checks and authenticated search
connection tests. Its shutdown hooks close the composition.
[`alphalab/local_agent.py`](../../alphalab/local_agent.py) retains its JSON settings
path and existing process-ownership behavior.

SQLite's optional `env` is an explicit, copied snapshot, empty by default. Only
model endpoint/model/key and search provider/Brave-key settings are accepted.
It is used only for selection intent `environment`, never for `none` or an
incomplete selected saved profile. It does not persist or convert environment
values into saved credentials. A saved ID literally named `environment` remains
a saved profile. Local loopback model endpoints may omit a key; remote endpoints
require one. Explicitly cleared remote model and Brave keys stay unusable.

## Capture timing and private results

The run's AbortSignal identifies its cached configuration:

- Model selection is captured on the first completion, including simultaneous
  first calls. Subsequent completions in that run keep the captured provider.
- Search selection is captured independently on the first search. Its selected
  search profile and any referenced model are resolved from **one** SQLite read
  transaction. An inactive referenced model is allowed when complete; a missing
  or incomplete reference fails without borrowing another profile's credentials.
- A model capture and a later search capture can deliberately use different
  revisions, matching the existing separate first-use timing. Their storage
  backend and installation remain the same.
- An operation without a run signal gets a fresh capture. Search connection-test
  overrides are separate private calls and never mutate saved selections. Blank
  override keys do not inherit a saved or environment key.

Only trusted completion/fetch dependencies receive resolved keys. Tool inputs
do not carry them. SQLite-bound provider failures are replaced with static
errors; public search/model results reject reflected execution credentials,
including bounded percent-encoded forms. This is not a way to identify unknown
secrets manually placed in arbitrary provider output. Do not log dependencies,
requests, or private snapshots. Default JSON adapter behavior is preserved.

Each SQLite composition links run cancellation with its shutdown signal, uses
suppression-resistant abort listeners, and checks cancellation before returning
success. A provider that ignores cancellation cannot keep the public operation
pending after close. `close()` aborts work and awaits the owned reader's cleanup.
The existing reader observes natural Worker exit after a terminal result and
uses bounded forced termination as a fallback. Forced termination retains the
documented Node/V8 runtime risk; graceful shutdown does not prove immunity.

SQLite runtime requirements and Worker limitations remain those of the
[Node reader](configuration-store-node.md): Node 22.18 or later within Node 22,
experimental `node:sqlite`, and local filesystem storage. This preparation does
not narrow the existing JSON launcher's separate runtime compatibility.

## RQData jobs

[`RQJobConfiguration`](../../apps/api/services/configuration_runtime_service.py)
accepts explicitly constructed `ConfigurationProfileServices`. It captures one
validated snapshot's RQData selection, credentials, installation identity, and
revision at an execution boundary. Capture fails for `none`, incomplete saved
configuration, or unavailable storage. Environment selection uses only the
environment snapshot deliberately supplied to those inactive services.

`client(module=...)` creates a fresh lazy `RQDataClient` using the existing
`RQ_USER`, `RQ_PASSWORD`, and `RQ_HOST` contract. `sync_service(root, module=...)`
injects its acquirer into the existing `RQSyncService` dependency seam. Tests use
synthetic SDKs and real temporary SQLite stores. SDK exceptions are sanitized
before the existing runner can persist a public failure.

A captured job stays pinned across profile edits, and the next capture observes
the new generation. Credentials are not put in persisted job requests, process
environment, or a global active-profile cache. Private binding objects have safe
representations and reject implicit JSON/pickle serialization. Explicit internal
client access is trusted and must not be logged or serialized.

The real `rqdatac` SDK maintains process-global connection state. These bindings
do not isolate that SDK: differently configured live clients must not overlap on
one SDK module. The existing production job manager and recipe subprocesses
remain JSON-backed. Constructing a binding schedules no job and changes no
default reader, writer, or profile service.

## Coordinated cutover prerequisites (Phase 4B.5)

The following work is deliberately deferred:

1. Coordinate backend selection across API profile readers/writers, Agent
   startup, RQData jobs, and subprocesses. Stop old writers before adopting an
   existing installation, validate a complete snapshot, and never leave mixed
   JSON/SQLite readers active. Design rollback/recovery and private-generation
   retention separately; these factories provide none of those operations.
2. Update the launcher/readiness contract together, closing the old composition
   before replacing it. Coordinate Node-version availability and the reader's
   installation-root binding before moving installations or switching roots.
3. Bind jobs at their actual execution boundary. Define interrupted-job restart
   and credential-rotation policy without persisting passwords in job payloads.
   Preserve one execution lane or isolate SDK state by process. Recipe workers
   need an explicit private configuration channel and lifecycle contract before
   SQL-backed jobs can use them; no environment mutation or JSON fallback is safe.
4. Update
   [`dev3_manager.py`](../../examples/deployment/macos-local/dev3_manager.py)
   together with cutover. Its pending-model-profile deployment step writes JSON;
   its backup/restore operations do not establish a coordinated SQLite generation.
   Its online SQLite backup covers top-level application databases, so the nested
   private configuration store needs explicit handling.
   They must not run as configuration writers after cutover until changed to use
   the chosen transactional interface. Stop all processes before filesystem
   restore and validate installation identity and the complete restored state
   before readiness. Do not copy a live SQLite database as a consistent backup.
5. Exercise the real bundled Agent, deployment, and RQData subprocess paths on
   each supported platform with test providers before activation. The isolated
   startup tests substitute the optional core bundle, not the real host server.
