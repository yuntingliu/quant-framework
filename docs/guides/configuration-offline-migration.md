# Explicit offline configuration migration

Phase 4B.5A provides a maintenance API and command for adopting saved JSON
configuration into one **inactive** SQLite generation. Normal startup, profiles,
Agent execution and RQData jobs remain JSON-backed. There is no automatic
migration, backend toggle, credential backup, import/apply or logical rollback.

## Offline preconditions

Use a private installation on a local filesystem with working OS/SQLite locks.
Before invocation, stop the API, Agent launchers, background/recipe jobs, older
releases, editors and deployment operations for that installation. The command
never stops processes. Do not acknowledge offline access unless these conditions
are established. An acknowledgement is an operator precondition, not evidence
obtained by scanning processes or reading three files sequentially.

The updated API lifespan and Python Agent launcher hold shared installation
leases. All three supported JSON Save paths hold shared leases before their
existing local locks, throughout read/modify/write. Migration acquires an
exclusive lease without waiting; any incompatible lease fails with `STORE_BUSY`.
API/launcher startup and supported saves cannot enter the maintenance window.
An existing Agent `host.lock`, even stale, also refuses migration. The utility
does not delete stale markers or decide that an unknown process is safe to kill.

The lock file is `app/secrets/configuration-maintenance.lock`. POSIX uses
`flock`; Windows uses shared/exclusive `LockFileEx` byte locks. Lock files contain
no credentials or source data and remain on disk; the OS releases leases when
handles close or owning processes exit. No Windows ACL or permission changes
are made. POSIX lock files/directories must be private. Never unlink or replace
a live lock file, rename the installation while it is in use, or acquire this
lease inside a SQLite mutation callback. Lock ordering is maintenance lease,
then existing provider locks, then the isolated store operation.
The OS primitives are documented in [LockFileEx](https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-lockfileex)
and [Python flock](https://docs.python.org/3/library/fcntl.html#fcntl.flock).

This is a **cooperative** maintenance protocol. Older releases, direct file
editors and the existing deployment manager do not honor it. They must be
stopped separately before acknowledgement. The utility cannot prove that an
operator's statement is true or fence a privileged, uncooperative writer.
Source identity/byte rechecks detect interference but are not a substitute for
offline exclusion. If those external processes cannot be reliably excluded,
do not invoke migration. There is no supported online migration mode.

## Explicit command and API

The command requires an explicit installation root, a supported Node 22
executable (22.18 or later within major 22), and offline acknowledgement:

```powershell
python scripts/migrate_configuration.py --root <installation-root> --node <node-22-executable> --acknowledge-offline
```

The Python entry point is
[`migrate_legacy_configuration`](../../apps/api/services/configuration_migration_service.py):

```python
report = migrate_legacy_configuration(
    explicit_root,
    node_executable=explicit_node_22,
    acknowledge_offline=True,
)
public_result = report.as_dict()
```

Neither entry point obtains a source root or credentials from environment
resolution. Omitting acknowledgement fails before creating directories.
Invoking the module or importing services never starts migration.

## Saved data and selection semantics

The source paths, relative to the explicit root, are:

| Service | Legacy source | Supported schema |
| --- | --- | --- |
| Models | `conexus/secrets/model-providers.json` | 1 |
| Search | `conexus/secrets/search-providers.json` | 1 or 2 |
| RQData | `app/secrets/rqdata-profiles.json` | 1 |

Migration reads each file under the exclusive fence, checks regular-file
identity, rejects links and oversized input, parses duplicate-free finite JSON,
and validates all three documents before destination initialization. It reuses
the pure transfer schema's metadata/reference checks and the native validators.
Inputs are limited to one MiB each, 20 profiles per service, and the existing
store's aggregate/node/depth limits. Unknown fields, malformed IDs, invalid
active selections and missing/non-OpenRouter model references fail closed.
Runtime probes are never used for validation.

Search schema 1 becomes a schema 2 `default` profile in memory. Search schema 2
retains named/inactive profiles and ordering. Saved credentials retain their
original spelling in matching profiles, including inactive profiles. No secret
retention helper, environment lookup or credential borrowing runs during
migration. Original and runtime-trimmed known credentials, including encoded
forms, cannot appear in configurable metadata. Unknown secrets manually put in
metadata cannot be detected with certainty.

| Legacy state | SQLite selection intent |
| --- | --- |
| Existing `active_id` | `profile`, with the same ID |
| Missing settings or `active_id: null` | `environment`, with no resolved values |
| Search schema 1, including Off | `profile`, ID `default` |
| Explicit `--none <service>` for a null selection | `none` |

`--none` cannot silently deselect an existing active profile. The API equivalent
is `unselected_intents={"models": "none"}`; only `environment` and `none` are
accepted for services whose legacy selection is null. A selected incomplete
profile stays selected. Blank/absent keys or passwords stay blank; inactive
credentials and environment credentials cannot complete it. Incomplete secrets
are permitted, but invalid metadata is not silently repaired.

## Publication and verification

The destination is `app/secrets/configuration-store.sqlite3`, using the existing
store protocol unchanged. Its initializer exclusively creates the destination
and commits all three native documents and selection intents in one transaction.
Any existing destination, including an empty/corrupt file, is refused. Orphan
journal/WAL/SHM files also refuse initialization. Nothing is overwritten, merged,
reset or selected from history.

Before reporting success, the utility verifies a single parentless generation,
exact document and selection equality through a fresh Python handle, and the
same protocol, installation identity, revision, model/search documents and
selection intents through the real Node reader. Node receives only model/search
expectations over a private pipe; it receives no RQData document or password.
Node runs with bounded subprocess and reader timeouts. Its diagnostic output is
never copied into public errors. Both readers use existing read-only behavior;
the migration does not introduce recovery or a new storage protocol.

Source identities and bytes are compared before initialization and after
verification. Windows descriptor/path `ctime` meanings differ, so checks there
use file IDs, size, modification time and bytes; POSIX also compares `ctime`.
No source file is deleted, renamed, truncated, rewritten or backed up.

Public reports contain only opaque store identifiers, protocol version, profile
counts, selection kinds and `backend_activated: false`. They contain no profile
names/IDs, endpoints, credential-presence flags, credentials, paths or secret
fingerprints. Errors reuse static sanitized codes/messages and redact exception
chains. Private subprocess input and diagnostic locals must not be logged.

## Interrupted or uncertain outcomes

A failure during initialization may leave an incomplete private database and
journal. A failure after commit may leave a valid but unverified inactive store.
Neither situation is silently removed or retried. A subsequent migration refuses
the existing destination. Preserve all original JSON and private SQLite artifacts
for an explicitly reviewed recovery procedure.

If the first generation committed but its acknowledgement was lost, use the
same offline procedure with `--verify-only` or `verify_legacy_migration(...)`.
This is read-only verification against unchanged sources and the same explicit
null-selection overrides, apart from acquiring/creating its maintenance lock.
It does not initialize, recover a journal, repair a
database, roll back, or create a generation. Any source drift, later generation,
changed credentials or damaged store fails. Never use old JSON as an automatic
rollback: that could resurrect a password or key deliberately cleared later.

## Phase 4B.5B prerequisites

Successful migration does not authorize activation. JSON remains authoritative
until coordinated cutover. Restarting the old application and changing JSON
after migration makes the staged store stale; revalidation must reject that
drift. Do not switch runtimes to an earlier staged generation.

Before production cutover, coordinate Python/Node backend selection and
readiness, eliminate every legacy/deployment JSON writer, keep RQData SDK global
state in one execution lane or separate processes, and define private recipe
subprocess configuration and interrupted-job credential policy. Review private
generation retention, credential revocation, rollback and backup/restore policy
together. The deployment manager's pending-profile JSON writer, installation
rename/restore and nested configuration database backups need coordinated
changes; this phase does not change or run that program. Complete real Conexus
bundle and platform verification before activation. See the
[inactive runtime guide](configuration-runtime.md) for the existing boundaries.
