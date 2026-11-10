# Durable authority read-side enforcement

Phase 4B.5B.3B.2A fences supported operations. It does not activate SQLite,
publish committed records, add a backend switch, migrate settings, restore a
backup, or turn the limited configuration app into the full workbench.

## Admission semantics

| Durable namespace | JSON operation | Explicit inactive SQLite composition |
| --- | --- | --- |
| Truly absent | Existing JSON behavior, irrespective of staged database presence | Existing explicit internal/test composition remains available |
| Structurally valid `prepared` | JSON remains authoritative; ordinary edits are allowed | Remains inactive; no activation occurs |
| `committed` SQLite | Rejected before legacy credentials are read or changed | Rejected, including when supplied installation IDs match |
| Missing record in an existing namespace, pending/extra files, invalid schema, unsupported protocol or unsafe storage | Rejected; no JSON fallback | Rejected; no repair or discovery |

Prepared authority admission validates the record, not migration freshness. A
JSON edit can invalidate the candidate's future eligibility while preserving
normal JSON operation. Offline preparation/inspection still performs the
separate source-equivalence checks from the [decision foundation](configuration-durable-decision.md).

`check_backend_authority()` is the common internal check. Its callers retain
an actual maintenance or execution lease through protected use. Backend labels,
HTTP payloads, environment flags and public Agent identities cannot grant a
lease or manufacture a runtime owner. There is no durable SQLite owner factory
in this phase: every committed installation remains unavailable to existing
launchers and inactive compositions until the full coordinated launcher exists.

## Covered entry points

| Entry point | Enforcement and lifetime |
| --- | --- |
| `apps/api/main.py` production lifespan, standalone model/search dependency bindings | `ConfigurationBackend.json()` checks authority after taking its shared maintenance lease; its lifetime excludes transitions |
| API process configuration, RQData health and implicit SDK environment loading | JSON admission precedes dotenv/environment credential resolution; explicitly supplied SDK values remain trusted inputs |
| `create_configuration_app()` | Requires an actual backend instance; borrowed owner checks apply at construction, lifespan and requests; this remains a limited internal app |
| `local_agent()` / `serve_with_local_agent()` / `_local_agent()` | Actual JSON owner is retained while the owned Node process lives; authority is checked before runtime discovery, token reads or installation work |
| Model `read_settings`, active/public/Test resolution and Save | Shared JSON maintenance lease spans the complete operation, including environment resolution and destination-based retention |
| Search read/public/Save/legacy Save/Test resolution | The same authority fence precedes existing settings locks; no JSON/SQLite dual writes |
| RQData read/public/active/config/connection resolution/Save | Same JSON fence; saved incomplete/cleared behavior and environment fallback remain unchanged only where JSON is authoritative |
| `read_json_settings` / `write_json_settings` for the three native profile filenames | Same shared authority fence, including direct helper access |
| Sync job submission, immediate execution and recovery; recipe execution and spawned recipe admission | Existing execution/admission gate remains held; committed authority rejects before job creation or recipe evaluation |
| SQLite data service and captured RQData child | Explicit inactive owner and child execution leases remain required; committed authority is rejected, credentials still use the existing private pipe/current-capture policy |
| CLI data commands and AlphaLab data tools | Delegate to the fenced job manager/recipe functions |
| Offline migration / verify | Reject conflicting authority; no automatic adoption, recovery or activation |
| `coordinated_offline()` and `OfflineInstallation.require_json_writer()` | Recheck authority under actual exclusion, including before stopping services; a previously closed JSON owner cannot authorize deployment after authority changes |
| macOS manager `offline_operation`, snapshot/deploy, pending model-profile writer | Reuse the checked backend and offline capability; no separate selection logic; historical restore remains disabled |
| Node `local-host.mjs`, managed Agent host and coordinated composition | Check admission before core construction; production local-host remains JSON only, private managed authentication remains unchanged |
| Node native model/search profile reads and inactive SQLite snapshot capture | A private Python child takes real OS admission leases through the read; invalid or committed authority rejects without returning credentials |

The full-workbench startup paths are still JSON only. An authority failure does
not select another backend. The Node store's isolated read-only storage protocol
is unchanged; raw storage snapshots remain trusted internal primitives rather
than independent production admission APIs.

## Lock ordering and Node helper

Normal JSON configuration operations take shared maintenance before existing
provider locks. Backend owners hold their shared/exclusive maintenance lifetime;
borrows recheck authority without acquiring another lease or upgrading locks.
Execution admission remains nonblocking. Offline transition order is
execution-exclusive, idle checks, stop explicitly owned listeners, Agent-lifetime
exclusive, maintenance-exclusive, then legacy child locks and another idle check.
An idle Agent therefore permits its stop callback to run; active operations and
RQData children still prevent unsafe mutation until actual retirement/exit. A
stop callback returning alone is not evidence of exit: the independent lifetime
and maintenance leases must actually be released. Foreign owners, unmanaged
processes and unconfirmed termination still fail closed.

Node invokes `alphalab.utils.configuration_authority` using the launcher's
explicit Python interpreter (`ALPHALAB_AUTHORITY_PYTHON` is an executable path,
not a backend toggle). Other internal callers need an installed AlphaLab Python
interpreter; the default is `python` on Windows or `python3` elsewhere. Failure
to start it fails closed. The private helper initially takes execution admission
while establishing an Agent-lifetime shared OS lease and, for JSON, a maintenance
shared lease. It then releases initial execution admission while idle. Inactive
SQLite helpers use the independent Agent lease without conflicting with their
owning Python process's maintenance-exclusive lease.

The bounded pipe protocol contains only `leased`, `enter`/`admitted` (or `denied`),
and `leave`/`released` acknowledgements. Every protected callback acquires a real
execution-shared credit before running, including borrowed and nested calls.
No credentials, documents or diagnostic exceptions cross this pipe. Multiple
callbacks share a helper safely; its OS execution lease remains held until the
last admitted callback actually finishes. A denied admission during shutdown
does not invalidate an otherwise healthy idle owner. EOF releases the helper's
leases if its owning Node process exits. RQData children independently retain
their existing execution leases through actual process exit.

Fresh and borrowed operations race helper exit and suppression-resistant native
`addAbortListener` cancellation. Cancellation rejects the outward operation with
`AUTHORITY_UNAVAILABLE`; it does not release the underlying callback's execution
credit prematurely or terminate a shared helper. Callbacks receive a cooperative
cancellation signal where supported. Late results are discarded and late
rejections are observed without exposing their messages. Node validates current
authority before and after protected work, with final helper/abort checks before
success. Early metadata rejection is permitted; it never authorizes unlocked use.

`close()` rejects new work, cancels pending outward calls and waits up to one
second for retirement. If a callback ignores cancellation, close fails safely
while its actual helper and leases remain retained. Automatic retirement finishes
when that callback settles. The coordinator consequently remains unable to
mutate configuration while unfinished work exists. Only after all callbacks
finish does cleanup close stdin and observe actual helper exit, with bounded
termination fallback for that helper alone. It never force-releases a shared
helper while callbacks can still perform protected work. OS/filesystem stalls
cannot have a universal deadline. SQLite Worker natural retirement is unchanged.

Production and managed Agent entries retain idle ownership until shutdown;
coordinated compositions borrow a live owner or create their own. Each operation
uses actual helper admission, not a handoff ID or cached authority decision.
Losing an owner fails closed without silently replacing it.

Native profile classification and file access use the same resolved location.
Existing paths (including Windows case and short-name aliases) and existing
parents of missing paths are canonicalized before classification. Windows native
filename/directory comparisons are case-insensitive; ordinary trailing-dot/space
aliases are normalized and alternate data streams are rejected. POSIX filename
case distinctions are preserved. Hard-linked private files are rejected rather
than allowing another filename to hide a native profile. Authority checks and
leases span the actual read or atomic write at the canonical location. These
checks do not claim to fence hostile filesystem replacement by unmanaged writers.

Generic adapters accepting arbitrary fixture filenames are library interfaces,
not managed installation entry points. Managed local-host accepts only the
native `conexus/secrets/model-providers.json` layout and validates the owner's
root contract. Arbitrary scripts, old releases, SDK users, editors and tools
which ignore these boundaries remain unmanaged and must be stopped manually.
This protocol does not prevent a trusted user from editing private files or
executing arbitrary Python/Node code.

## SQLite anchor and deferred production admission

`validate_committed_authority()` is observation only. It checks canonical root,
strict record IDs (and expected IDs when supplied), SQLite header/protocol,
installation binding, integrity, all three native documents and selections from
one read transaction, and the exact unchanged first-generation activation anchor.
It requires one generation with a null parent. A changed current revision,
additional generation, missing anchor or corrupt store fails closed. No older
generation is selected as a substitute. Matching paths/IDs never authorize a
historical restore.

Phase 3B.2B must implement and test atomic ancestry admission: walk the bounded,
unique parent chain from the validated latest generation to the immutable
activation anchor, reject cycles/missing parents/forks/rollback, and keep
revision and selection validation in the same SQLite snapshot. Protocol 1 has
parent references but no audited public ancestry operation yet. This step
therefore refuses descendants instead of treating them as valid activation.
It must not be used for live durable saves, whose post-commit acknowledgements
would otherwise become ambiguous. Inactive profile updates remain usable.

3B.2B must also supply complete owner-bound Python/Node startup, one authority
for all workbench/job/deployment participants, authenticated Agent binding,
supported bundle execution, environment capture and a reviewed offline activation
publisher. None is activated here. Phase 3C still owns credential-history
retention/revocation, rollback/restore provenance and interrupted recovery.
Whole-root rollback or removal of the entire authority namespace cannot be
detected under this trusted-local-storage model.

Tests use temporary installations, synthetic decisions/credentials, real SQLite,
test-owned processes and mocked provider/SDK dependencies. Windows ARM64 tests
and intercepted mode checks do not establish real macOS deployment, POSIX locks,
physical power-loss durability or execution of an unavailable Conexus bundle.
