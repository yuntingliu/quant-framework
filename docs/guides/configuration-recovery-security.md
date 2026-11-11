# Configuration recovery and credential history

This is the combined Phase 4B.5B.3C / 4B.6 policy for SQLite protocol 1.
It does not activate an installation, add a recovery HTTP API or enable restore.
Administration assumes trusted private local storage, cooperative processes and
working SQLite/OS locks. Stop unmanaged editors, older releases, recipe tools
and deployment scripts yourself. Leases cannot exclude a writer that ignores them.

## Inspect before acting

`python scripts/recover_configuration.py inspect --root <installation>` is
read-only: no settings, locks, authority records or journal files are created,
removed or repaired. Optional `--installation-id`, `--decision-id` and
`--current-revision` pins must come from independent trusted observations.
Inspection is an observation, not a startup capability or a lock-held snapshot
for subsequent mutation. Retry inspection if it reports a concurrent change.

| State | Permitted next action |
| --- | --- |
| `legacy_json` | Normal JSON startup; inspection validated native inputs without importing environment credentials. |
| `prepared_candidate` | JSON remains selected; separately review explicit activation. |
| `prepared_stale` | JSON remains selected; investigate candidate/source drift. Never activate the stale candidate. |
| `committed_sqlite` | Normal checked SQLite startup. |
| `activation_acknowledgement_uncertain` | Verify the published decision and current revision; do not replay activation. This state requires `--uncertain-acknowledgement` because receipt cannot be inferred from disk. |
| `prepared_publication_interrupted`, `activation_publication_interrupted` | Preserve pending files and investigate manually. No promotion, deletion or automatic retry. |
| `authority_incomplete`, `authority_missing`, `authority_ambiguous` | Keep runtime admission blocked. No reconstruction from database existence. |
| `unclaimed_store` | A staged store and a store with lost authority cannot be distinguished from this evidence. Do not infer SQLite activation. |
| `sqlite_recovery_required` | Only a committed authority with verified identity and expected latest revision can use the explicit offline journal procedure below. |
| `historical_backup` | Restore prohibited. A backup-only marker denies runtime admission. |
| `identity_conflict`, `store_revision_conflict` | Preserve evidence; do not reset IDs or select an older revision. |
| `recovery_interrupted` | Preserve the private recovery workspace and original store. Manual investigation; no automated retry or workspace cleanup. |
| `store_missing`, `invalid_or_incomplete_state`, `unsupported_state` | Fail closed. No recreation, adoption, journal deletion or JSON fallback. |
| `inspection_busy`, `inspection_conflict`, `installation_missing` | Resolve the stated condition before relying on inspection. |

An existing empty authority namespace is incomplete, not legacy JSON. A durable
owner witness with missing authority also blocks both Python and Node. A copied
backup-only marker or interrupted recovery workspace is a deny-only witness;
none of these files can grant authority. Removing every witness as part of a
whole-root rollback can evade local detection; this policy does not authenticate
the freshness of an internally consistent older copy.

## One supported mutation: native journal recovery

The administrative command is deliberately narrow:

```text
python scripts/recover_configuration.py recover-journal --root <installation> --installation-id <expected-id> --decision-id <expected-id> --current-revision <expected-latest-revision> --node <supported-node-executable> --acknowledge-offline --acknowledge-current-revision
```

Both acknowledgements and all identifiers are required. Obtain the expected
**latest committed** revision from a trusted pre-interruption observation, never
from the activation anchor or an arbitrary backup. If this evidence is missing,
the operation is unavailable. The command does not discover a historical head.

The coordinator requires committed, unchanged authority, exclusive execution,
Agent and configuration leases, legacy execution locks, idle job metadata and
no Agent host lock. Acquisition waits are bounded; there is no lock upgrade.
The process checks private paths, symlinks, hard links, journals and identities.
A WAL/SHM sidecar is unsupported for this DELETE-journal protocol, even when the
main header still claims DELETE mode. No ordinary read performs recovery.

Before modifying the original database, it creates a private, deny-only
`app/secrets/configuration-recovery` workspace, copies the **offline** database
and journal (together bounded to 256 MiB) and lets SQLite recover that staging pair. This is not a live raw
backup. SQLite interprets rollback records; the coordinator does not implement
another journal parser. The restored format, protocol, root binding, identity,
complete ancestry, current documents, references and expected latest revision
must validate. Source hashes and authority must still agree before in-place
SQLite recovery of the original. Original and staged results must match all
three documents, selections, revision and retained history. The Node reader
independently verifies model/search data and authority in the same generation.

All fallible final audits run before publication with the workspace still
blocking runtime admission and all exclusive leases retained. A final read-only
transaction rechecks format, protocol, root binding, identities, latest revision,
integrity, ancestry, documents and selections against the Node-verified recovered
state. Authority, sidecars and idle execution are checked again. This private
audit grants no runtime capability and does not bypass the fence for callers.

Removing the operation's own two staging files and now-empty workspace is the
final publication step. There is no recursive cleanup or fallible authorization
audit after publication. A failed audit leaves the workspace as an admission
barrier. Physical recovery may already have occurred; failure does not imply
that database recovery rolled back. Keep the evidence and investigate offline.

Once fence removal begins, a cleanup, directory-sync or lease-release error
reports `RECOVERY_PUBLICATION_UNCERTAIN`. Publication may already have succeeded;
the command never claims rollback or silently recreates the workspace. Inspect
explicitly with the trusted installation, decision and current-revision pins.
A remaining workspace means `recovery_interrupted` and blocks startup. If it was
removed, inspection must validate committed authority and the expected latest
generation before normal startup. An interruption or lost acknowledgement at
this boundary requires the same inspection; never automatically replay recovery.
An ambiguous workspace is never automatically deleted, repaired or recreated.
The CLI reports uncertain publication with a nonzero exit status; keyboard
interruption retains exit status 130 and explicitly requires inspection.
No automatic authority repair, historical rollback or recovery of a failed
initialization is provided. Healthy stores cannot use this command as another writer.

## Credentials: current resolution, private retention

Normal resolution uses the verified current generation only. Replacement,
explicit clearing and logical profile deletion publish new atomic generations.
Missing/incomplete selected profiles never borrow a secret from another profile,
the environment or history. Deleting a selected profile means explicit `none`.
Deleting a model referenced by any search profile is rejected. Existing legacy
environment behavior remains available only where an explicit backend selection
permits it; recovery never imports environment credentials.
Deletion publishes from detached raw documents and edits only its target service.
Unrelated native versions, structure, extension fields, credentials and selection
intents remain exactly as stored, including search v1. Whole-generation validation
uses normalized views without persisting those views into unrelated services.

Active jobs retain their captured credential until actual child exit. A restarted
authorized job captures the current generation and a fresh attempt identity;
old job metadata cannot restore its former password. Queued/interrupted jobs,
owned children, execution leases and uncertain shutdown continue to block
offline maintenance. Closing an API is not proof of native child termination.

Historical rows and private backups still contain old secrets. This is logical
revocation, **not physical erasure**. Protocol 1 has no pruning, vacuum-for-erasure,
history restoration or secure-deletion guarantee. Normal profile services read
bounded history in the same snapshot only to reject historical credential values
in configurable public metadata. Original/normalized values, inactive profiles,
all three services and bounded percent-decoding are checked. History never
supplies a runtime credential. Explicit private extraction remains a trusted API;
snapshots are not implicitly JSON/pickle serializable. Public errors remain
static and have no raw exception chains. Validated system UUIDs/digests are not
user metadata; opaque identifier substrings are not secret disclosure.

Unknown secrets manually placed in metadata cannot be detected with certainty.
Never log private snapshots, locals, exception diagnostics, child input pipes or
database contents. Restrict private backups and failed recovery workspaces as
carefully as the active store. No Windows ACL or chmod changes are made; new
Windows files/directories inherit the existing policy, while POSIX private
creation/checks remain enforced.

## Backup evidence and disabled restore

Backups use SQLite's backup API under offline exclusion, validate source
stability, exact file inventory/digests, database integrity, original installation
binding and committed ancestry **before** publishing the complete manifest.
Pending/absent/conflicting manifests, extra files and cross-installation roots
fail closed. Path strings in manifests are checked against current and retained
credentials, including encoded forms. No extra plaintext credential JSON is
derived from SQLite. Existing native private inputs remain private backup data.

`inspect_configuration_backup(root, backup, installation_id=expected_id)` checks
integrity and identity independently of the live backend selection or owner.
It does not require a healthy running installation and never authorizes restore.
Reports distinguish these facts explicitly:

- `verified=true`: observed integrity and installation binding passed.
- `freshness_verified=false`: matching IDs do not prove that this is latest.
- `credential_revocation_verified=false`: rotation/revocation cannot be inferred.
- `rollback_provenance_verified=false`: whole-root rollback cannot be excluded.
- `restorable=false`: `reject_restore()` remains unconditional, including JSON backups.

New private SQLite backup runtimes carry a deny-only backup marker. Copying an
entire backup runtime to the original bound path is blocked. A privileged operator
can still manually remove witnesses or replace only an old internally consistent
database: local IDs/hashes cannot prove this never happened. Do not attempt it.
Full restore requires independent trusted monotonic revision/installation
evidence, externally established revocation policy and a reviewed crash-safe
procedure that preserves newer state. None is invented here.

## Capacity and safe failures

The runtime ancestry bounds remain 4,096 complete generations and 64 MiB of
UTF-8 native documents plus selections across history, with existing per-generation
limits. Bound and profile-service writers validate the candidate inside the
write transaction before current-revision publication. `HISTORY_CAPACITY` (HTTP
409 at existing service boundaries) preserves the exact previous valid revision,
all documents and selections. Reads of that generation remain usable. No history
is deleted or older revision selected to create room. Stop new edits and arrange
a separately reviewed capacity policy; never remove rows manually. Existing
oversized/corrupt histories fail closed rather than being silently repaired.

## Evidence and Phase 4C handoff

Tests use disposable roots, real SQLite transactions/journals, actual test-owned
Python/Node processes, cooperative OS leases and synthetic credential canaries.
They cover publication termination boundaries, bounded failures, restart capture,
history redaction, backup provenance and six isolated negative controls. External
fetch/SDK entry points are replaced before managed Agent startup. Substituted-core
execution verifies our composition/authentication boundaries, not the unavailable
Conexus bundle. Real-bundle certification remains blocked.

Windows ARM64 process-termination tests do not establish macOS/POSIX deployment,
directory durability or physical power-loss recovery. Network filesystems,
malicious local database/journal edits and unrestricted trusted introspection
remain outside the assumptions. Lease/busy waits are bounded, but filesystem I/O,
native SDK calls and arbitrary callback execution have no universal time bound.
Forced Worker termination retains the documented Node/V8 runtime limitation;
normal successful workers retire gracefully.

Phase 4C rollback must be a validated logical update from sanitized metadata,
not an old private database, history row or credential snapshot. It needs reviewed
credential retention/deletion identity rules, missing/revoked credential re-entry,
process-safe preview receipts, expected-revision conflicts, complete snapshot
coordination and crash handling. Independent provenance, retention/erasure,
power-loss behavior, actual POSIX deployment and real Conexus execution remain
prerequisites for any broader recovery claim. No 4C import/rollback is implemented.
