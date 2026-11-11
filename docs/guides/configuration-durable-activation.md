# Coordinated durable SQLite activation

This is an explicit administrative transition, not an automatic upgrade. An
absent authority namespace or a valid `prepared` record keeps JSON authoritative.
A valid `committed` record selects SQLite. Invalid, incomplete, conflicting or
unsupported authority fails closed, including on restart. A staged database
alone does not select a backend. No real installation was activated during
development of this implementation.

## Offline procedure and prerequisites

First use the [offline migration](configuration-offline-migration.md) and
[decision preparation](configuration-durable-decision.md) procedures. Review the
read-only `inspect_configuration_decision(root)` result and record its installation,
decision and revision identifiers. Stop all cooperative work and owners. Check
that the source JSON contains the current credentials, not restored history.
Never repair a stale candidate by editing its database or decision record.

The administrator may then invoke, with explicit reviewed values:

```text
python scripts/activate_configuration.py --root <installation-root> --node <node22-executable> --installation-id <installation-id> --decision-id <decision-id> --revision <revision> --acknowledge-offline --acknowledge-current-credentials
```

There is no HTTP activation route or environment-only selector. The command
does not migrate, recover, adopt, restore, install a runtime or probe providers.
It requires the pinned Conexus build and a compatible Node 22 runtime. Missing
build artifacts or required exports block publication. Substituted-core tests
exercise composition and authentication, not real-bundle certification.

All checks run inside the existing offline coordinator: execution and Agent
exclusion, idle job checks, maintenance exclusion and legacy writer locks. It
validates all three native documents, selected-profile readiness, Python/Node
agreement, installation binding and exact fresh source equivalence. Durable
production currently permits `profile` and `none`; `environment` is rejected
before activation and in subsequent generations. JSON environment behavior is
unchanged. Incomplete inactive profiles remain saved but cannot supply missing
credentials to selected profiles.

Publication compares the exact prepared record under those locks, writes and
syncs a private pending witness, then atomically replaces `decision.json` with
the committed record. The namespace is never temporarily absent. A pending
witness, unexpected files or failed validation rejects startup. An error after
replacement may mean publication succeeded but acknowledgement failed: inspect
the state; never automatically retry, revert to JSON or restore an older file.
Windows directory syncing and physical power-loss durability are not certified.

## Runtime composition and ownership

`ConfigurationBackend.production(root)` observes authority and rechecks it under
real OS leases. A committed installation has one exclusive Python owner. Before
minting a new owner epoch, it excludes actual SDK children and Agent helpers.
`json()` and the earlier explicit inactive `sqlite()` factory cannot bypass a
committed decision. `offline_decision()` is only a closed administrative request;
it grants no runtime ownership and must be revalidated by the offline coordinator.

`serve_with_local_agent()` imports and starts the complete `apps.api.main` app
under that owner. The API includes the original workbench routes, health,
configuration bindings, job scheduler and Run recovery. Set installation paths
before importing the application. A mismatching imported runtime root fails
closed. SQLite requires an explicit loopback API origin for Agent tools; a bare
ASGI launch without the necessary owned-Agent composition is not a supported
SQLite hosting recipe and does not become ready. Legacy/prepared JSON startup
and remote Agent behavior retain their previous contracts.

The launcher privately spawns and authenticates the actual Node child. A private
ephemeral owner witness binds installation, decision, live Python PID/process
birth, actual Node PID and a digest of the fresh launch key. It is not an
activation marker. Copying a handoff dictionary or key to another Node process
does not authorize it. Python helpers hold actual OS leases through callbacks
and native completion. The managed Node entry checks this proof before core
construction; the ordinary standalone JSON entry cannot upgrade itself.

The full API uses the owned connection for public Agent HTTP operations as well
as search tests. Requests require the existing one-use, same-socket HMAC proof;
there is no ambient origin discovery, redirect, reconnect or foreign endpoint
fallback. Streams retain ownership through actual reads and close sockets on
cancellation. The Node SQLite reader keeps graceful Worker retirement and
bounded fallback. RQData is validated privately when auditing bound generations
but is never included in Node snapshot/Worker responses.

Model configuration is captured at first completion for a run. Search captures
independently at first search, with any referenced model from that same read
transaction. Explicit `none`, cleared credentials and incomplete selected
profiles never borrow JSON, environment or another profile's credentials.

RQData jobs retain the existing per-job captured credentials and spawned SDK
process isolation. A child proves its actual parent and holds its execution
lease until process exit. Its internal sync operation receives a process-local
admission capability, not a backend flag. Clearing/rotation affects subsequent
captures; it does not replace a running job's credentials. JSON, foreign or
unidentified persisted jobs cannot be retagged as SQLite work on restart.

During coordinated shutdown the execution gate rejects new work, active work
must retire, owned children must actually exit and maintenance exclusion must
be obtained before deployment or backup. Idle API lifetime alone does not hold
a job-admission lease. API shutdown is not proof of SDK child exit. Unmanaged
editors, old releases and arbitrary deployment tools remain outside cooperative
exclusion and must be stopped operationally.

## Immutable anchor and generation audit

The protocol-1 database schema is unchanged. The committed revision is the
immutable first-generation anchor. Normal profile saves append generations
without rewriting authority. Python and Node audit the complete ordered chain
inside their SQLite snapshot transaction: installation/root/protocol, ordinal
continuity, UUID identities, exact parent links, anchor selection evidence and
current metadata pointing to the final generation. Missing parents, cycles,
forks, gaps, corrupt/unsupported documents and metadata rollback fail closed.
The current native documents and cross-service references are validated together.

Audit limits are 4,096 generations and 64 MiB total encoded history, with the
existing per-generation document limit. A bound update audits its candidate
before commit, so reaching a limit fails without publishing. There is no
automatic pruning, historical selection or rollback. History is private and
contains old credentials; capacity management needs a separately reviewed policy.

## Evidence and remaining recovery work

Disposable tests use real SQLite, Python/Node processes, OS leases and synthetic
credentials; provider/SDK/core functions are substituted where needed. They do
not certify the unavailable real Conexus bundle. Windows ARM64 is the exercised
development platform. POSIX/macOS lease, process-birth, directory-sync and real
deployment behavior remain unverified. No Windows ACL changes are performed.

This assumes trusted local storage and cooperative writers. An ancestry audit
detects inconsistencies in the supplied database; it cannot prove that an entire
database/authority namespace was not replaced by an internally consistent older
copy. Offline source equivalence and operator acknowledgement cannot establish
provider revocation or independent credential provenance. Do not restore a
historical store, reset an installation identity or delete witness files to force
startup. Existing historical restore and automatic rollback guards remain closed.

The combined [recovery and security policy](configuration-recovery-security.md)
defines private history retention/redaction, capacity errors, interrupted-state
inspection and explicit same-installation journal recovery. Historical restore,
pending-record promotion and automatic recovery remain disabled. Independent
freshness/revocation evidence, whole-root rollback provenance, physical power-loss,
real-bundle and real macOS/POSIX certification remain unavailable.
