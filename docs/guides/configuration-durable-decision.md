# Inactive durable backend decision foundation

This guide records the preparation-stage contract. The subsequent
[durable activation implementation](configuration-durable-activation.md)
supersedes the startup/publication limitations below: explicit offline
prepared-to-committed publication and audited descendants now exist. Preparation
itself still never activates SQLite, and its first-generation checks remain.
The tables describing JSON-only production below describe the historical baseline.

Phase 4B.5B.3B.1 supplies metadata validation and explicit offline preparation.
It does **not** activate SQLite, connect authority to startup, or provide an
active-record publication primitive. No production cutover has been authorized
or performed. Committed records appear only as validation fixtures in disposable
test installations. Normal production is still JSON-backed.

## Current choices and the integration boundary

| Participant | Current authority | Work still required before cutover |
| --- | --- | --- |
| Production API lifespan, standalone model/search dependencies | `ConfigurationBackend.json(root)` or the launcher's explicit JSON owner | Consume the same verified durable choice under lifetime leases; reject inconsistent authority |
| Python `local_agent` and `serve_with_local_agent` | Fresh JSON owner, retained while its owned Node process lives | Coordinated offline durable selection and startup admission |
| Explicit configuration app, data/job services and managed SQLite Agent | Explicit internal `ConfigurationBackend.sqlite(root, installation_id=...)` | Validate authority and ancestry before construction; retain actual OS ownership |
| Node `local-host.mjs` | Fixed JSON; production handoffs reject SQLite | Consume the owner's exact verified choice, with authenticated endpoint agreement |
| Node coordinated configuration and private Agent host | Explicit trusted handoff and SQLite reader | A handoff does not confer a Python OS lease or durable activation authority |
| Model/search/RQData JSON writers | Shared maintenance lease and bound JSON root | Refuse JSON access after a verified committed SQLite decision |
| Migration command | Explicit offline first-generation adoption | Remains inactive; must never select or repair a backend automatically |
| macOS manager pending writes, backups and release switches | Closed explicit decision plus offline deployment capability | Read authority before writing; enforce complete drain/admission and reviewed recovery |
| Older releases, editors, manual Node processes, Linux/manual deployment and arbitrary recipe subprocesses | Not certified by the cooperative protocol | Stop/exclude manually and integrate every supported participant before activation |

This phase changes none of these runtime choices. An environment flag, database
presence, old backup, public Agent identity or HTTP response cannot invoke this
preparation or confer activation authority. No route, command, launcher or
deployment entry point imports the new decision service.

## Record format and observations

The private namespace is `app/secrets/configuration-decision/`; its one complete
record is `decision.json`. Root identity is SHA-256 of the canonical root using
the same Python/Windows normalization as the existing backend contract. The
private absolute path itself is not serialized. IDs are random lowercase
32-digit hexadecimal values, never hashes of credentials or profile contents.

```json
{
  "protocol_version": 1,
  "state": "prepared",
  "backend": "sqlite",
  "root_key": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "installation_id": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
  "decision_id": "cccccccccccccccccccccccccccccccc",
  "revision": "dddddddddddddddddddddddddddddddd",
  "evidence": {
    "kind": "legacy-exact-v1",
    "verification_id": "eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee",
    "migration_revision": "dddddddddddddddddddddddddddddddd",
    "store_protocol_version": 1,
    "selection_kinds": {"models": "profile", "search": "none", "rqdata": "none"}
  }
}
```

All fields are required; unknown or credential properties are rejected. Numeric
versions require exact integers. Duplicate decoded keys, including Unicode
escape aliases, floats, non-finite constants, malformed JSON, invalid IDs,
foreign roots and oversized input are rejected without echoing values. Records
are limited to 16 KiB and use the bounded authentication JSON grammar. Node and
Python validate the same field allowlist and selection kinds.

| Observation | Meaning |
| --- | --- |
| Namespace absent | Legacy JSON compatibility only under the operator's never-activated/trusted-local-storage contract; SQLite presence is ignored |
| Valid `prepared` record | Inactive candidate; current JSON remains authoritative; this is not an activation receipt |
| Valid `committed` record | Future committed-choice representation, supported for validation tests; no product code can publish or activate it here |
| Existing namespace with missing, partial, extra, corrupt or unsupported content | Fail closed; never return legacy JSON or repair automatically |

`read_decision_record()` performs strict filesystem/schema inspection without
creating files or choosing a backend. `inspect_configuration_decision()` also
uses a shared maintenance lease, validates the existing private SQLite snapshot,
and checks installation ID, revision and selection metadata. Prepared records
additionally require current legacy-source equivalence. No process-global
selection cache exists. Neither API constructs a runtime owner.

The current validator deliberately accepts only the unchanged revision anchor.
An evolved SQLite generation is rejected, never replaced with an older one.
Before live writers can be connected, Phase 3B.2 must prove anchor ancestry and
legitimate subsequent revisions without rewriting authority for every save.

## Offline preparation and freshness

`prepare_sqlite_candidate()` is a trusted internal operation. It requires a
closed JSON `ConfigurationBackend`, explicit expected installation/revision IDs,
a supported Node 22 executable and `acknowledge_offline=True`. The operator must
first stop all unmanaged writers, services and deployment tools. This code does
not enumerate processes, decide that an unknown PID is safe, or stop services.

It reuses `coordinated_offline()` in this order:

1. Exclusive execution/admission gate.
2. Idle-job checks and exclusive maintenance ownership.
3. Existing legacy `sync-job-queue` and `captured-rq-execution` locks.
4. Repeated idle checks, then private-store and source verification.

Shared job admission is nonblocking, preventing the shutdown lock-order cycle.
Active JSON/API/Agent owners, another SQLite owner, cooperative children,
queued/running/interrupted/unknown jobs and existing Agent lock markers block
preparation. Competing preparations cannot both publish. Lock acquisition is
fail-fast; bounded SQLite and Node verification waits remain in force. Native
filesystem failures cannot be given a universal wall-clock bound.

`verify_offline_migration()` reuses the existing migration verifier inside the
actual offline capability, without attempting an exclusive-lock upgrade. A
dictionary or identity assertion cannot bypass the lease. Verification requires
exact current source equivalence to the first and only SQLite generation,
checks Python's complete three-document snapshot, and checks Node's model/search
documents, selections, protocol, installation and revision from that generation.
The new decision bytes are also validated by Node before publication.

All selected saved profiles must be complete, including inactive model profiles
referenced by selected OpenRouter search. Explicit `none` is preserved. Missing
source files retain migration's documented defaults, but an `environment`
selection is not eligible here: readiness cannot be proven without resolving
environment credentials. No environment values are imported or treated as saved
credentials. Future first-use environment admission is a separate prerequisite.

Saved-key clearing, rotation or other legacy drift rejects the candidate. Store
edits reject it even if the new documents match JSON: the first-generation
verifier refuses history and cannot silently select an old revision. No migration,
repair, overwrite or credential restoration is performed. Source identities,
bytes and the current revision are rechecked before and after publication.

These checks establish **current-source equivalence**, not a complete history
of revocation. Unversioned legacy JSON can be changed and restored outside the
protocol; a complete runtime backup can also carry old credentials and IDs.
There is no external monotonic revocation witness here. Operators must exclude
non-cooperative writers. Effective activation remains unavailable until the
credential-history/provenance policy and participant integration are reviewed.
Verification IDs and timestamps alone are never authorization.

## Publication, conflicts and interrupted outcomes

The sole writer publishes `prepared` only, under a real thread-bound offline
capability. Expected absence is checked; an existing committed, conflicting or
invalid record is never overwritten. An explicit matching decision ID permits
read-only acknowledgement after fresh preflight, not replacement.

Exclusive directory creation establishes the namespace witness. The writer
creates `decision.pending` exclusively, flushes and fsyncs it, rereads/validates
it, then atomically hard-links it to the final name without replacement. It
removes the temporary name only on successful completion. POSIX directory
fsync is used; Windows retains inherited directory protection and performs file
fsync without ACL/chmod changes. Hard-link support and trusted local filesystem
semantics are required. This is tested atomic visibility on Windows, not proof
of physical power-loss durability or every filesystem's persistence guarantees.

Any interruption can leave the namespace empty or with pending/final names.
Such remnants fail closed, including a link/unlink interruption with two names.
There is no automatic deletion, recovery, retry/reset, rollback or JSON fallback.
If final publication completed but acknowledgement failed, explicit inspection
and expected-ID acknowledgement can establish the inactive outcome. Changed
JSON invalidates a prepared candidate and requires reviewed handling; it is not
automatically replaced. Namespace deletion or wholesale filesystem rollback is
outside the trusted-storage contract and cannot be detected reliably here.

## Remaining work

Phase 4B.5B.3B.2 must connect all supported Python/Node startup, writer, Agent,
job and deployment participants to one authority; establish explicit offline
durable activation and lifetime admission; handle revision ancestry and inactive
candidate invalidation after legitimate JSON writes; retain authenticated Agent
binding; prove first-use environment behavior; and verify the real Conexus
bundle and macOS/POSIX deployment. No effective commit primitive is provided
until those prerequisites can be enforced together.

Phase 4B.5B.3C must define credential-history retention/revocation, backup
provenance, missing-authority recovery and interrupted restore/power-loss policy.
Private generations and backups retain earlier secrets. Integrity and matching
paths/IDs alone never authorize restoration. No historical generation or backup
is selected, restored, pruned or made authoritative in this phase.

Tests use disposable roots, synthetic credentials, actual SQLite/Node readers,
spawned test-owned processes and injected filesystem failures. Negative controls
demonstrate stale-candidate admission, corrupt-record JSON fallback and conflicting
publication only in isolated test memory. They do not activate any installation.
