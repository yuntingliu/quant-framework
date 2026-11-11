# Offline deployment and private backup preparation

Phase 4B.5B.2B prepares cooperative shutdown and backups. Normal production
remains JSON-backed. There is no activation record, environment backend toggle,
live cutover route, configuration adoption, automatic recovery or restore API.
Non-Off SQLite search connection tests still return HTTP 503.

## Authority, admission and shutdown

`configuration_deployment.coordinated_offline()` accepts an explicitly verified,
**closed** `ConfigurationBackend`. A JSON decision remains JSON when an inactive
SQLite file exists. A SQLite decision must retain its verified installation ID.
The coordinator never closes, upgrades or bypasses another live backend owner.
It produces a thread-bound, non-serializable capability valid only inside the
offline context. Strings, environment variables and database presence are not
backend authority.

The coordinator acquires the exclusive job-admission gate, rejects unfinished
Agent/backtest/RQData work, calls the bounded owned-service stop callback, then
requires exclusive configuration maintenance and both existing runtime execution
locks. It rechecks persisted work after shutdown. Queued, pending, interrupted,
unknown and running statuses block deployment; they are not erased, silently
adopted or resumed with historical credentials. Cancel or reconcile them through
their existing owner before retrying.

Shared job admission is nonblocking. A shutdown gate cannot deadlock an API
trying to admit work while that API is releasing its maintenance lifetime.
Gate/maintenance/runtime-lock waits share a deadline of at most five seconds;
the service stop callback has its own finite budget. The macOS adapter bounds
each launchctl invocation and service-stop polling. No unrelated PID is killed,
and lock files are never deleted as a recovery technique.

Current sync submission, recovery and direct execution participate in the gate.
Spawned SQLite RQData workers and JSON recipe workers retain a separate shared
OS lease **through actual process exit**, including background SDK-thread
teardown. Target-function return, an IPC result or API exit is not proof that
the child is gone. A surviving child excludes offline work even after its parent
exits. Existing 2A runtime locks are also checked, but an older release that does
not retain the new lease still requires manual process verification.

`ConfigurationDataServices.close(timeout_seconds=...)` stops new admission,
signals cancellation, stops queue submission and waits a bounded time for queued
futures and synchronous workers. On timeout it retains its backend borrow and
returns a sanitized busy error; closing can be retried. Native calls holding the
GIL can delay orphan watchdogs, and an unkillable process must be handled by an
explicit operational procedure. The coordinator fails closed while its lease
remains held.

## Backup contract

`create_backup(offline, destination, ...)` requires the offline capability. It
never treats `offline=True` supplied by a caller as evidence of exclusion.
Destination directories must be new, outside the source trees, and private.
POSIX directories/files use 0700/0600. Windows keeps inherited protection; no
chmod or ACL operation is performed there.

Runtime files are inventoried before and after copying. Symlinks, ambiguous
locations, changed files, incomplete journals and orphan sidecars fail closed.
Required source roots must exist and be readable directories. Enumeration
errors, including nested subtree errors in either source pass or verification,
are propagated as sanitized failures; partial directory listings are not accepted.
Lock bookkeeping and shared-memory indexes are not copied as application state.
Nonempty rollback journals require separate explicit recovery before retrying;
backup does not replay them or recreate a database.

All nested SQLite databases, including
`app/secrets/configuration-store.sqlite3`, use SQLite's supported backup API.
WAL commits in application databases are included; the private destination is
made a standalone DELETE-mode database and checked for integrity and foreign-key
consistency. Storage protocol 1 still requires DELETE mode for the configuration
store: this feature does not adopt a WAL configuration store or change protocol.

The configuration copy preserves all native generations, all three documents,
selections, installation ID and the original root binding. Its current revision
must agree with the captured source. A copied store is validated at the backup
path **against the original root**; it is not opened as a relocated installation.
SQLite integrity alone is insufficient: source-inventory stability and idle
execution are checked before publication.

An in-memory candidate has no `complete` field and is validated against the exact
file inventory, digests, SQLite integrity, metadata and configuration identity
before publication. Only then is the final manifest written to a private
same-directory pending file, flushed and atomically renamed to
`configuration-backup.json`. Failed candidate checks never publish that marker.
Interruption before the rename may leave private `.pending` artifacts; they are
not completed backups and `verify_backup()` refuses them. Independent verification
after publication remains available. An existing destination is never overwritten.
The manifest contains paths, integrity
digests and system identifiers, not document payloads or credential values.
It is private backup metadata, not an export format or public API. Public reports
contain counts, backend and validated identifiers only, with `restorable=false`.
No database contents, native profiles or environment values are logged.

Optional deployment metadata accepts exactly `release`, `created_at`, `tag`,
`old_ssh_registered` and `offline`, with the existing string/boolean types.
Supported tags are `manual`, `before-rollback` (a backup label, not restore
authorization) and `before-local-` followed by eight lowercase hexadecimal commit
characters. Arbitrary free-form tags are rejected before artifact creation.
Caller metadata is detached before copying. Known credential values, including
runtime-trimmed forms and up to five percent-decoding layers, are rejected in
caller-controlled metadata with the static `INVALID_METADATA` error. Checks use
only explicit backup sources: native model/search/RQData JSON, all retained
SQLite generations and recognized credential keys in an explicitly supplied
`alphalab.env` file. No live environment values or provider readers are consulted.
The same metadata checks run during candidate and independent verification.
Conservative matching can reject otherwise innocent text containing a short saved
credential; validated system-generated manifest identifiers are not caller metadata.
Unknown secrets manually embedded in metadata cannot be identified with certainty
and must never be supplied.

Native JSON/environment files may be copied as existing private backup inputs;
SQLite credentials are never materialized into extra plaintext JSON files.
Backups contain secrets, including old generation secrets. Restrict access and
do not distribute them as ordinary diagnostic artifacts. Unknown secrets manually
placed in arbitrary research files cannot be identified automatically.

## Restore and legacy writes

`reject_restore()` and the macOS manager's rollback entry point fail before any
service stop, directory rename, copy or release switch. This applies to JSON
backups too: native profiles and environment files may resurrect revoked keys.
A matching path or installation ID does not establish credential freshness.
No failed deployment automatically restores a backup, chooses an older release,
or falls back to JSON. Current state and private backup remain available for
review; failed post-start verification stops only the manager's owned listeners.

`write_pending_model()` requires the offline capability and a JSON decision.
It preserves the existing native JSON deployment write and rejects SQLite
decisions before touching configuration. There are no dual writes. A staged
SQLite file does not disable JSON writes; the result explicitly marks staged
data as requiring revalidation. A future activation must compare all legacy
sources again, rather than assume an earlier migration is still current.

## Entry-point inventory

| Entry point | Preparation and remaining boundary |
| --- | --- |
| macOS `dev3_manager.py`: deploy, backup, stop, restart | Shared coordinator gates admission, stops owned listeners, checks maintenance/worker exclusion and performs recursive verified backups; normal deployment is explicitly JSON-only. |
| macOS pending model profile | Writes native JSON only through the guarded offline writer; no inferred SQLite selection. |
| macOS restore/rollback and deployment failure | Historical state restoration is disabled; no runtime/config replacement or credential rollback. |
| macOS release `switch()` and runner/config writes | Deployment invokes them only inside the offline context, then releases fences before starting new services. They remain private adapter helpers, not standalone safe administrative APIs. |
| `build_local_release.py` | Builds and validates disposable releases under its separate build lock; runtime/cache test paths are isolated. It neither selects a backend nor restores configuration. |
| `local_agent.py`, production API lifespan | Existing JSON maintenance ownership and owned-Node shutdown remain; no SQLite composition is activated. |
| `configuration_data_service.py`, sync manager and sync runner | Per-job capture is unchanged; admission and bounded drain now participate in offline exclusion. |
| Captured RQData worker / legacy recipe worker | Independent process-lifetime lease; no credential IPC or SDK selection change. |
| Supported model/search/RQData JSON services | Existing shared maintenance leases continue to exclude offline writes; no native profile protocol changes. |
| `migrate_configuration.py` / migration service | Explicit offline migration remains separate and inactive; backup never invokes adoption. |
| Linux deployment script, manual release swaps, older managers, editors and arbitrary recipe subprocesses | Not certified by this preparation. Stop or exclude them manually; they must join the final activation protocol before cutover. |

The backup command now requires a brief coordinated outage rather than claiming
a raw live runtime copy is consistent. After a successful manual backup the
manager may restart services that were previously registered. An unprepared
historical-report import is rejected instead of launching an uncoordinated
mutating subprocess inside maintenance exclusion.

## Verification and remaining prerequisites

Tests use temporary stores, synthetic credentials, real SQLite backup/WAL,
Windows spawn and mocked launchctl/process control. Negative controls bypass
only test-owned protections to demonstrate live-worker exclusion, generation
inconsistency detection and historical credential resurrection. They never
operate on personal configuration or real deployment services.

Mocked tests do not establish real macOS/POSIX or Conexus bundle compatibility.
Filesystem I/O and native SDK code cannot be given a universal wall-clock bound.
Manifest publication is not a physical power-loss or crash-safe restore protocol.

Durable activation and authenticated owner binding are described in the
[activation guide](configuration-durable-activation.md). The combined
[recovery and security policy](configuration-recovery-security.md) adds private
history redaction, backup-only admission fences and independent backup inspection.
Integrity and identity never establish freshness or revocation; verification
reports explicitly leave those claims false. Historical restore and automatic
rollback remain disabled. Real Conexus, macOS/POSIX deployment and physical
power-loss/interrupted restore certification remain unavailable. No real cutover
is authorized by these preparation operations.
