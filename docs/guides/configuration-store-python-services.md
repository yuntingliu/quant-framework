# Inactive Python configuration services

Phase 4B.3 provides explicitly constructed Python service instances over the
existing protocol-1 `ConfigurationStore`. Production routes, the application
startup path, RQData clients/jobs, and Node model/search adapters still use
their existing JSON configuration. There is no runtime flag, environment
switch, dependency override, automatic adoption, or production singleton.

## Explicit internal interface

```python
from alphalab.utils.configuration_store import ConfigurationStore
from apps.api.services.configuration_profile_service import ConfigurationProfileServices

# An authorized caller must already have created this installation's store.
store = ConfigurationStore.open_existing(explicit_installation_root)
services = ConfigurationProfileServices(store)

current = services.read_snapshot()
public_model_settings = current.public_settings("models")
public_search_settings = current.public_settings("search")
public_rq_settings = current.public_settings("rqdata")

# profile_input is a native, private profile, never a sanitized transfer document.
services.models.save_profile(
    profile_input, activate=True, expected_revision=current.revision
)
```

`models`, `search`, and `rqdata` expose `public_settings`, `read_settings`,
`active_profile`, `active_config`, `save_profile`, `resolve_test_profile`, and
`select`. Search additionally exposes its legacy `save_settings` and
`resolve_settings` forms. These resolve/validate private connection parameters;
they never perform connection tests or provider requests. They are internal
facades, not new HTTP endpoints or replacements wired into the application.
Test resolutions return opaque private results whose explicit
`extract_private()` copy has exactly the existing connection shape: RQData uses
`RQ_USER`, `RQ_PASSWORD`, `RQ_HOST`; search Off contains only `provider`, Brave
contains `provider`/`brave_api_key`, and OpenRouter contains
`provider`/`model_profile_id`. Dormant Brave keys are excluded from Off and
OpenRouter results. Model resolutions keep the existing validated profile shape.

`save_profile` accepts `activate`, optional `expected_revision`, and the
service-specific deletion flag: `clear_api_key`, `clear_brave_api_key`, or
`clear_password`. `select` accepts an explicit `{"kind": "profile", "id": ...}`,
`{"kind": "environment"}`, or `{"kind": "none"}`. Selection may deliberately
leave a saved profile incomplete. Runtime resolution then returns no usable
profile; `active_config` fails with a sanitized error. It never borrows a key.

`save_profiles({"models": model_input, "search": search_input, ...}, ...)`
supports one atomic internal edit across services. Its `activate` flag applies
to each supplied profile; `clear_secrets` names the supplied services to clear.
This is normal private profile editing, not an import/apply/rollback contract.

## Snapshots and publication

Use one `read_snapshot()` result when consuming all three services together.
It exposes the installation identity, opaque revision, protocol version,
explicit selection intents, and detached private documents. Separate calls
through the individual facades are independently consistent operations, not
a shared snapshot spanning those calls.

An edit reads one complete generation and preflights all profile/credential
rules outside the transaction. Search validates references against the proposed
model document, including a model changed in the same edit. Publication calls
`ConfigurationStore.update` with the exact preflight revision. The store checks
that revision under `BEGIN IMMEDIATE` and atomically publishes all three native
documents and all selections. A concurrent publication returns
`REVISION_CONFLICT`; there is no automatic retry or callback replay.

The transaction callback only returns the prepared documents/selections. It
does not read JSON files, resolve environments, invoke services, or probe any
provider. Unchanged services and their private extension fields survive edits.
Search v1 is normalized to v2 in a read view only; an unrelated service edit
preserves its native v1 document. Editing/selecting search itself persists v2.
Reading never rewrites the database or the original JSON files.

## Credentials and selection

The shared pure preparation functions are also used by the existing JSON
writers. Their retention behavior remains authoritative:

- Models retain a blank key only for the same saved ID and normalized API
  destination. Changing the destination requires a replacement remote key.
  Explicit SQLite key deletion preserves an incomplete saved profile.
- Brave retains the same profile's key, supports replacement and explicit
  clearing, and never borrows from another saved profile. Explicit model
  references resolve against the pinned model document. A null reference
  follows that document's explicit model selection.
- RQData retains a blank password only for the same saved ID, user and host.
  Replacement and deletion are separate requests. Each `active_config` call
  reads the current generation, so later jobs see later selections; returned
  private values remain detached. No RQData client or synchronization code is
  changed in this phase.

The factory accepts an optional **explicit private environment dictionary**.
It copies approved configuration keys; it never loads `.env` or reads process
environment variables itself. Without that input, environment selections are
unconfigured. A future runtime integration must supply a fresh, approved
environment snapshot outside storage transactions when needed. Current JSON
services continue their legitimate existing live environment behavior.

`none` always disables fallback. A selected-but-cleared or incomplete profile
also disables fallback. A saved profile whose literal ID is `environment`
remains an ordinary profile. A saved search `off` profile is distinct from
`none`. Only an explicit environment search selection permits the legacy
implicit-default form's independent first-time Brave key capture. That capture
is prepared outside the transaction and is blocked for named, existing,
cleared, or explicitly unselected profiles. Explicit clearing takes precedence.

## Private values and failure behavior

Private documents and resolved configurations use redacted wrapper objects.
Their `repr`/`str` hide values. They expose no Mapping, iteration, dataclass,
sequence, or instance-dictionary interface. Standard JSON, FastAPI's
`jsonable_encoder`, Pydantic JSON response serialization, dataclass conversion,
and pickle cannot implicitly serialize their private data. Pydantic's Python
mode may retain the opaque object; it does not extract its contents.
Only the explicitly named `extract_private()` method grants trusted callers a
detached private copy. That copy must never be logged or returned by an API.
This protects common implicit serializers, not arbitrary trusted Python code
using unrestricted introspection or an explicit custom serializer. Public settings
use fixed per-service field allowlists and credential-presence flags matching
the service UI metadata. They are not portable exports.

Known credentials from all saved profiles, including inactive profiles and
other services, must not occur in approved public metadata. Supplied environment
credentials are also checked before publication/public projection, as are old
credentials being replaced or cleared in the current edit. Checks include raw
and runtime-trimmed nonempty credentials and up to five URL decoding passes,
following Phase 4A's convention. Deeper unresolved encodings fail closed when
credential scanning is required. Empty/whitespace-only credentials are ignored.
Read validation, update preflight and public projection use the same scanner;
raw credential spellings are retained privately for scanning before normalization.
Unknown secrets manually put
in metadata cannot be recognized with certainty. Short secrets can cause
conservative metadata rejection. Unknown private native fields remain private.

Failures use static `ConfigurationStoreError` codes/messages. The integration
reuses the existing store's interruption redaction utilities without changing
the SQLite protocol. Original exceptions, notes, causes and contexts are not
retained in public exception chains. Diagnostic tooling that captures private
caller locals remains outside the redaction guarantee.

Missing/corrupt/foreign stores and recovery-required states fail closed. The
factory cannot initialize or recover a database. Generations retain old
credentials; history retention, revocation/rollback policy, legacy adoption,
coordinated Python/Node cutover, environment loading at that cutover, and
Windows/POSIX crash durability remain later-phase decisions. No Windows ACLs
or permissions are modified. The Node reader retains its documented
experimental SQLite and native forced-termination limitations.

## Verification

```text
python -m pytest tests/api/test_configuration_profile_service.py -q
```

Tests use synthetic canaries and real temporary SQLite stores, including
thread/process conflicts, pinned reads, atomic batches, pre-commit failure,
restart selections, no JSON/environment reads, unchanged JSON behavior and
Node interoperability. Set `ALPHALAB_TEST_NODE` to a supported Node 22.18+
executable for the cross-language test if the default Node is unsupported.
No real provider connections are made.
