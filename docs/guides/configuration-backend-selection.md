# Coordinated configuration backend selection

This guide preserves the explicit inactive-composition baseline. The current
[durable activation guide](configuration-durable-activation.md) supersedes the
JSON-only production and deferred-publication statements below. Explicit
inactive factories still cannot bypass committed authority; only the verified
production owner and its privately authorized child consume it.

Phase 4B.5B.1 prepares explicit model/search compositions. Production remains
JSON-backed. This code never migrates settings, initializes a store, publishes
an activation marker, or selects SQLite from an environment flag.

## Ownership and lifetime

`ConfigurationBackend.json(root)` is the default. It holds a shared cooperative
maintenance lease. `ConfigurationBackend.sqlite(root, installation_id=...)`
requires an existing readable store and its known opaque installation ID. It
holds an **exclusive** maintenance lease for its whole lifetime. It fails if
JSON users, offline maintenance, or another SQLite owner already hold the root.
Different installation roots remain independent.

This conservative SQLite mode supports one owning Python process, with API
threads and an owned Node child borrowing the same decision. It is preparation,
not a production multi-worker admission protocol. Close the owner on its creating
thread after all API/Agent borrows end. Closing while borrowed fails; no live
backend change is supported. Lock acquisition is fail-fast and bounded. Acquire
the maintenance lease before provider locks or SQLite transactions, and never
acquire it inside a transaction callback.

The owner emits an intentional private `node_contract()` containing version,
canonical installation root, Python-compatible root hash, backend, random
decision ID and optional store installation ID. It contains no credentials.
It is a trusted parent/child handoff, **not** a security capability against
arbitrary local code. The parent must retain a borrow until the child exits.
Only backend and opaque decision/installation IDs belong in public identity
metadata. Do not publish the private contract's root.

`createCoordinatedAgentConfiguration(contract, createModelCompletion, options)`
validates the complete contract and root before constructing either adapter.
SQLite readiness and every later read are pinned to that installation ID.
Failures close the owned reader; they never fall back to JSON. Incomplete
profiles remain unconfigured rather than making missing credentials available.
The existing Worker reader, graceful retirement, cancellation and shutdown
remain in use. Node SQLite support remains Node 22.18+ within major 22; it is
experimental. JSON startup also retains the existing Node 24 support.

## Python API bindings

All model/search GET, Save and Test routes now use `ModelSearchBindings`.
The normal full API constructs JSON only; the production launcher lends its
JSON owner to the API, so the launcher and API use the same decision ID.
Standalone routers without a lifespan also construct JSON only.

For explicit test/internal construction:

```python
with ConfigurationBackend.sqlite(root, installation_id=known_id) as owner:
    app = create_configuration_app(owner, environment={})
    # Run the ASGI app while owner remains alive. Shut down its owned Agent
    # before leaving the owner lifetime.
```

By default this deliberately limited factory mounts only model/search configuration
and health routes. RQData, jobs and recipes require the separate explicit
`include_data=True` composition described in
[isolated RQData execution](configuration-rq-execution.md); other workbench routes remain absent.
It must not replace the production full app before the deferred integrations.
An explicitly supplied environment snapshot is optional, defaults to empty,
and is only resolved for `environment` intent. `none`, incomplete saved profiles
and explicit clearing never borrow those credentials.

SQLite edits use the existing `ConfigurationProfileServices` preflight and
atomic three-document publication. Optional `expected_revision` is surfaced as
HTTP 409 on conflict; internal compare-and-swap also protects omitted revisions
against overlapping writers. `clear_api_key` and `clear_brave_api_key` retain
their explicit SQLite meanings. No JSON writes occur. Invalid edits publish
nothing. Private Test profiles remain internal and tests never save them.

`PUT /api/model-providers/selection` and `/api/search-providers/selection` accept
`{"kind":"none"}`, `{"kind":"environment"}`, or
`{"kind":"profile","id":"saved-id"}` and optional `expected_revision`.
These edit profile selection, never the backend. JSON continues to use its
existing Save/activate contract; SQLite-only flags and selection requests are
rejected rather than silently ignored in JSON mode.

JSON Search Test retains its existing authenticated loopback Agent behavior.
**Non-Off SQLite Search Test is unavailable (HTTP 503)**, before resolving
private credentials or looking up an ambient origin/token. Off remains a local
success with no probe. `agent_identity` is only a consistency assertion: even a
genuine matching Node composition identity cannot authorize HTTP dispatch.

The current Host's health and descriptor are public. Its authenticated search
route does not prove backend/installation identity, and production Host startup
is deliberately JSON-only. This cannot establish endpoint-to-installation
agreement for SQLite. No verified SQLite connection is accepted or minted here,
and no arbitrary caller-supplied connection or identity can bypass the gate.
Do not invent a successful verification by comparing unrelated metadata.

Before enabling SQLite Search Test, a trusted owned-endpoint contract must bind
the complete origin, private token, slug/PID and backend/decision/installation
identity in an immutable private connection. It must authenticate that exact
destination and retain owner/Agent lifetime ownership; stale, replaced, foreign
or closed connections must fail before credentials are released. The record must
not expose Mapping/dataclass/iteration, repr, implicit JSON/pickle serialization
or diagnostic access to its token. Requests must use its captured destination,
never reread process environment or silently reconnect. This missing prerequisite
is deferred, without adding an unverified record or new authentication protocol.
Production JSON readiness still checks its owned child's private connection
metadata; it is not claimed to supply this missing SQLite HTTP contract.

## Audited model/search entry points

| Entry point | Access and integration |
| --- | --- |
| `apps/api/main.py:configuration_lifespan` | Full production JSON owner; borrows launcher's choice when supplied. |
| `routers/model_providers.py` GET/PUT/Test/selection | One bound model service for every route; local-mode gate retained. |
| `routers/search_providers.py` GET/PUT/Test/selection | One bound search service; non-Off SQLite Test fails closed until an authenticated owned endpoint contract exists. |
| `model_provider_service.py` `read_settings`, `active_profile`, `public_settings`, `prepare_profile`, `save_profile`, `resolve_test_profile` | Legacy JSON retained; all reads/writes enter shared maintenance lease. Paths use the explicit JSON context when bound. |
| `search_provider_service.py` `read_settings`, `resolve_settings`, `public_settings`, `save_profile` and model-reference helpers | Legacy JSON retained; nested model reads share the bound root and shared lease. |
| `alphalab/local_agent.py` `local_agent`, `serve_with_local_agent`, `_local_agent` | Fixed JSON owner; fresh private contract overrides inherited handoff values; lifetime borrow covers actual child shutdown. |
| `local-host.mjs` | Production accepts only JSON handoffs, rejects mismatches before core startup; no handoff preserves old fixed JSON startup. |
| `configuration-runtime.mjs` | Existing first-model and independent first-search capture; SQLite search and referenced model use one snapshot; expected installation pinned when coordinated. |
| `model-providers.mjs:readActiveProvider`, `configuredCompletion` | Existing JSON path or explicit snapshot resolver; unchanged. |
| `web-search.mjs:readSearchSettings`, model/reference resolution, `configuredWebSearch` | Existing JSON files or explicit snapshot resolver; unchanged. |
| `configuration_profile_service.py` | Existing injected SQLite services; validates all native documents and references together; no JSON calls. |
| `configuration_migration_service.py`, migration CLI | Explicit offline tools, not normal request paths; never automatically invoked. |
| `rq_profiles.py` | Legacy reads additionally fenced to prevent accidental JSON reads under a SQLite owner; runtime job cutover remains deferred. |
| `examples/deployment/macos-local/dev3_manager.py` pending provider write, backups/restores | Unmanaged direct JSON access; not covered by this protocol. Deferred to 4B.5B.2. |

Generic private-settings helpers are not an activation mechanism. Ordinary
supported model/search reads and writers enter the maintenance fence before
using them. Context-local JSON root binding does not mutate process environment.
Old releases, editors, direct filesystem callers, manually started Node hosts,
deployment managers, and recipe subprocesses may ignore leases. Operators must
stop them; this code cannot fence uncooperative writers. A private handoff alone
does not give an independently launched Node process an OS maintenance lease.

## Deferred activation sequence and dependencies

Phase 4B.5B.2A prepares optional isolated RQData jobs and recipe execution.
Phase 4B.5B.2B must still integrate deployment writes, worker draining and backups.
None is activated in normal production startup.

Phase 4B.5B.3 must implement offline atomic persistent activation, admission for
all supported processes, recovery/rollback and end-to-end cutover. The required
order is: stop unmanaged writers; exclude cooperative lifetimes; verify complete
migration and Python/Node readability of the same installation and revision;
atomically publish one durable choice; start Python and Node with that choice;
fail closed on inconsistent state. Recovery must never silently select JSON or
older credentials. No activation file or live backend-switch endpoint exists.

Tests use real temporary SQLite stores, synthetic credentials and mocked
providers. Substituted-core entry tests exercise startup wiring and shutdown,
not real Conexus execution. The three real-bundle integration tests remain
unavailable without the optional generated distribution; this does not verify
full deployed cutover. Windows tests do not establish POSIX or power-loss
guarantees. Network filesystems and hostile local filesystem writers are outside
the existing trusted-local-storage contract.
