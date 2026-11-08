# Isolated Node configuration reader

Phase 4B.2 supplies an internal read-only client of the Python
`alphalab.utils.configuration_store` protocol 1. It is not connected to the
live Conexus model/search adapters, API services, or frontend. There is no
legacy adoption, environment resolution, import, apply, or rollback operation.

## Runtime

The supported API baseline is Node 22.18 or later within Node 22. The verified
runtime is **Node 22.23.4, SQLite 3.53.4, Windows ARM64**, interoperating with
Python's SQLite 3.54.1. Other major Node versions fail closed until verified.
Node's [`node:sqlite`](https://nodejs.org/docs/latest-v22.x/api/sqlite.html)
API remains experimental. It is synchronous; the reader runs it in Worker
Threads, not on the Agent event loop. Disabling experimental SQLite produces
`UNSUPPORTED_RUNTIME`; there is no writable or legacy fallback.

## Trusted internal interface

```javascript
import { ConfigurationStoreReader } from '../../integrations/conexus/configuration-store.mjs'

const reader = new ConfigurationStoreReader(explicitInstallationRoot)
try {
  const snapshot = await reader.readSnapshot({ signal })
  const revision = snapshot.revision
  const models = snapshot.documents.models // PRIVATE: contains credentials.
  const search = snapshot.documents.search // PRIVATE: contains credentials.
  const selections = snapshot.selections
  // Pass private documents only to trusted internal code. Never log them.
} finally {
  await reader.close()
}
```

The snapshot provides `protocol_version`, `installation_id`, `revision`,
`documents` and `selections`. Document/selection access returns fresh detached
copies. Normal stringification, inspection and JSON serialization are redacted.
Explicit private document access grants the caller responsibility for those
credentials. Large native integers retain their value as JavaScript `BigInt`;
small integers and floats use `Number`. This private structure is not a public
API/export format.

Only model/search documents and their selection intents are returned. The
worker never selects the RQData document. It validates the consumed native
documents and selection structure; full RQData validation remains Python's
responsibility. Incomplete model profiles and unresolved search model references
remain unchanged, as allowed by the storage layer. No provider probes occur.

`profile`, `environment`, and `none` remain distinct. The reader never resolves
environment credentials or selects an alternative profile. Search v1 remains
v1 with its explicit `default` selection. This layer performs no migration.

## Consistency, paths and failure handling

One SQLite read transaction obtains metadata and the referenced latest
generation. Cardinality, singleton, protocol, opaque IDs, installation-root
hash, native versions, profile/active identities, and selections are checked.
Historical pointers fail closed. An existing reader pins the installation ID.
Both documents always belong to one revision.

All connections use `readOnly: true` and `query_only=ON`. Missing stores are
never created. WAL is rejected before opening SQLite; rollback recovery is
never performed automatically. Hot journals fail with `RECOVERY_REQUIRED`.
Explicit recovery remains a separately authorized Python operation.

The installation root is canonicalized as in Python; Windows paths use
backslashes and Windows invariant NLS casing before SHA-256 binding. JavaScript
ICU linguistic lowercasing is insufficient. A compatibility table preserves
the existing Python protocol; tests compare every Node case-changing Unicode
scalar with Python and exercise real Unicode installation names. Changes to
Windows NLS or Node Unicode data require rerunning that compatibility check.
Child-directory
links, database links/hard links, unsafe journal paths and permissive POSIX
secret-file/directory modes are rejected. File/directory identities are checked
before and after the transaction while a read-only descriptor remains open.
Windows ACLs are inherited and never modified.

The trust model requires a private, trusted local directory and working SQLite
filesystem locking. Node's SQLite API does not expose an existing descriptor or
`SQLITE_OPEN_NOFOLLOW`; pre/post checks cannot eliminate a malicious owner-level
substitution-and-restoration race. Copied foreign installations, ordinary link
substitution and observed file replacements fail closed. Deliberate consistent
database edits, externally removed journals, network filesystems, and physical
power-loss guarantees are outside this reader's assurances. POSIX permission
and symlink behavior require validation on those platforms.

## Worker lifecycle

Defaults are two active Workers, 32 outstanding requests and a 5-second request
budget. Options allow 1–4 Workers, up to 64 outstanding requests, and 1–30,000 ms
per read. The deadline includes queueing, startup and SQLite operations. Each
SQLite lock wait is capped at 250 ms; contention returns `STORE_BUSY`.

Each request owns a one-shot Worker. A validated terminal message is held until
the Worker exits cleanly; a message alone cannot publish a successful snapshot.
SQLite handles are closed before that message, and the Worker closes its
MessagePort. Normal completion uses natural exit so asynchronous ESM evaluation
can finish without an immediate `terminate()` call. Errors after a result or
nonzero exits fail closed.

Retirement allows a fixed 500 ms natural-exit window, then forces termination
if needed. Cancellation, timeout and `close()` reject pending reads immediately;
their Workers receive the same bounded exit window. Slots and shutdown promises
remain held until actual exit. Request deadlines also cover retirement. Exit
clears timers, listeners and Worker references. No idle workers persist.
`close()` rejects pending work and awaits exit.
Abort reasons, native errors, worker output and original error causes are never
forwarded. Public failures contain static codes/messages only.

Parent deadlines remain responsive during synchronous SQL work. Termination
cannot interrupt every possible operating-system/native filesystem stall;
bounded cleanup assumes the supported local filesystem and SQLite busy timeout.
Forced termination is not immune to Node/V8 defects. Immediate termination
during asynchronous ESM completion reproduced a native assertion on the verified
Windows ARM64 runtime; graceful completion avoids that observed trigger, while
forced fallback remains a reliability limitation.

## Verification

Set `ALPHALAB_TEST_PYTHON` to the project's Python executable if it is not the
default `python` (Windows) or `python3` (POSIX), then run:

```text
node --test tests/conexus-configuration-store.test.mjs
python -m pytest tests/test_configuration_store.py -q
```

Fixtures create private temporary installations through the actual Python store.
Tests cover publication races, native corruption, real termination of test-owned
writers, credential canaries, worker failures and event-loop responsiveness.
A deterministic transaction test pauses between metadata and document reads;
its negative control bypasses only `BEGIN` in a test-owned Worker and verifies
that the publication/snapshot assertions detect the missing transaction.
