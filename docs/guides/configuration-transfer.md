# Configuration transfer: portable format and local API

Phase 4A's pure schema, projection, serializer and in-memory migration in
`apps/api/services/configuration_schema.py` remain the format authority. Phase 4C
adds coherent export, persisted previews, atomic SQLite application and sanitized
logical rollback through the full production API. It does not activate a backend.
Phase 4D's frontend is not included. Historical private restore remains disabled;
see the [recovery policy](configuration-recovery-security.md).

## Portable format

The wrapper version is independent of the native provider schema versions.
Canonical output uses model v1, search v2, and RQData v1:

```json
{
  "format": "alphalab.service-configuration",
  "version": 1,
  "models": {
    "version": 1,
    "selection": {"kind": "profile", "id": "research"},
    "profiles": [
      {
        "id": "research",
        "name": "Research",
        "base_url": "https://openrouter.ai/api/v1",
        "model": "provider/model-id"
      }
    ]
  },
  "search": {
    "version": 2,
    "selection": {"kind": "profile", "id": "web"},
    "profiles": [
      {
        "id": "web",
        "name": "Web search",
        "provider": "openrouter",
        "model_profile_id": "research"
      }
    ]
  },
  "rqdata": {
    "version": 1,
    "selection": {"kind": "none"},
    "profiles": []
  }
}
```

These are the complete profile-field allowlists:

| Service | Profile fields |
| --- | --- |
| Models | `id`, `name`, `base_url`, `model` |
| Search | `id`, `name`, `provider`, `model_profile_id` |
| RQData | `id`, `name`, `provider`, `user`, `host` |

RQData's provider must be `rqdata`. Search supports `openrouter`, `brave`, and
`off`. Saved RQ usernames and endpoints are account metadata, so this format is
not anonymous. Credential values, credential-presence flags, source diagnostics,
environment values, runtime paths, private-file contents, research data, reports,
and unrelated application state have no transfer fields.

All three services are required, including empty collections. Profile IDs are
scoped to their service. Output profiles are sorted by ID; output JSON has
deterministic ordering. An inactive profile is still included as saved metadata.

## Selection intent

Every canonical service section has exactly one selection:

| Representation | Meaning |
| --- | --- |
| `{"kind":"profile","id":"research"}` | A saved profile in that section. |
| `{"kind":"environment"}` | An environment-fallback intent, without any resolved environment values. |
| `{"kind":"none"}` | Explicit disablement/unselection; no implied environment fallback. |

`environment` and `none` selections must not contain an ID or any settings.
The literal profile ID `environment` remains an ordinary saved ID when used
with `kind=profile`; it is never interpreted as a synthetic profile.
A saved search profile whose provider is `off` remains a saved profile selection.
It is distinct from an unselected search configuration.

These markers survive parse/serialize round trips. They do not change runtime
behavior or authorize a future importer to use destination environment
credentials. Phase 4C refuses environment intent for durable SQLite application;
legacy JSON environment behavior outside the import workflow remains unchanged.

## Pure functions and snapshot provenance

The module exposes:

- `parse_transfer_json(str_or_bytes)`: bounded UTF-8 JSON parsing, validation,
  and in-memory migration;
- `validate_transfer_document(object)`: the same validation/migration for a
  detached JSON-compatible object;
- `serialize_transfer_document(object)`: validation followed by canonical JSON;
- `project_settings_snapshot(service, settings, selection=...)`: validate and
  project one caller-supplied saved native settings snapshot into an immutable
  `SettingsSnapshot` containing only sanitized metadata;
- `project_settings_snapshots(models=..., search=..., rqdata=..., selections=...)`:
  project all three supplied native snapshots, checking each service's metadata
  against credentials supplied by every service. Use this batch entry point for
  unified exports; `selections` maps each service name to its selection intent;
- `export_configuration(models=..., search=..., rqdata=...)`: combine those
  snapshots and validate cross-service references.

Projection accepts native model/RQData v1 and search v1/v2. Known native secret
fields (`api_key`, `brave_api_key`, `password`) are omitted. Their nonempty string
values are used only for in-memory substring comparisons against profile metadata,
including inactive profiles and bounded URL-decoded variants. A match fails with
`CREDENTIAL_IN_METADATA` and a static message. Empty values are ignored; opaque
non-string secret objects are never coerced. Short credentials can conservatively
reject otherwise ordinary metadata; there is no minimum secret length exemption.
The single-snapshot entry point can check only that snapshot's credentials; the
batch entry point checks all three services before serializing any snapshot.
Neither credentials nor credential fingerprints are retained in returned snapshots.

Unknown native properties and public/effective API response shapes are rejected.
Private snapshots themselves are never passed to the JSON serializer. Incoming
transfer documents reject all secret properties, including empty, null, or masked
values. A sanitized snapshot or a portable import alone cannot reveal credentials
that were not supplied to its native projection.

The caller must supply saved-store snapshots, not environment-resolved profile
objects. It must also supply selection intent: a native `active_id=null` cannot
prove whether a future caller intended fallback or explicit disablement.
A supplied saved-profile selection must agree with the native active ID.

The functions never inspect files, environment variables, providers, or live
service objects. Their inputs cannot prove their own provenance. In particular,
three independently read live files must not be presented as an atomic export.
The live transfer layer establishes snapshot coordination and provenance before
calling these pure functions, as described below.

## Supported legacy forms

Credential-free model v1, search v2, and RQData v1 sections may use their legacy
`active_id` field instead of `selection`. A non-null ID becomes `kind=profile`;
null becomes `kind=environment`, matching native legacy fallback semantics.
Using both fields, or neither, is rejected. A caller that means explicit
disablement must supply `selection={"kind":"none"}`.

A credential-free singleton search v1 section contains `version=1`, `provider`,
and an optional `model_profile_id`. It migrates to one search v2 profile with
ID `default`, name `Default`, and a saved-profile selection. Missing model
references become null. Migration creates fresh objects and never rewrites the
input or stored settings.

There is no released unified version 0 format. Unknown wrapper/native schema
versions fail closed. Private backups containing credentials are not valid
portable legacy imports.

## Validation and limits

- Maximum JSON size: **256 KiB**, measured as UTF-8 bytes, including whitespace.
- Maximum object/array nesting: **8** containers, counting the root as one.
- Maximum object-tree budget: **4096** visited keys/values/containers.
- Maximum profiles: **20 per service**, at most 60 across all three services.
- IDs: 1–64 lowercase ASCII letters, digits, underscores, and hyphens, starting
  with a letter or digit. No type coercion or whitespace normalization.
- Names: 1–100 characters; model IDs and RQ users: 1–200; base URLs: at most
  2000; RQ hosts: at most 255. Text must be nonempty and have no control,
  surrogate, or formatting characters.
- Model IDs must not be absolute Windows drive, UNC, or POSIX filesystem paths.
  These checks are lexical; identifiers such as `provider/model-name` remain valid.
- JSON is plain UTF-8. Duplicate decoded keys at any depth, non-finite numbers,
  non-object documents, unknown properties, ambiguous selections, unsupported
  versions, duplicate profile IDs, and cyclic/non-JSON Python objects are rejected.

Explicit search model references must resolve to an included saved model whose
endpoint is `https://openrouter.ai/api/v1` after trailing-slash normalization.
This applies to dormant references too. The model need not be active or have a
credential: those are readiness concerns, not metadata integrity. A null search
reference deliberately means follow the selected model and is not resolved by
this module, even when the model selection is environment-backed or unselected.
Reference diagnostics use the original input-array indices. Profiles are sorted
only after reference validation succeeds.

Model endpoints require HTTPS or loopback HTTP. Authorities and ports are
validated without DNS. User information, query strings, fragments, backslashes,
control characters, malformed percent escapes, encoded unsafe forms, and recognized
credential-bearing path labels/token prefixes are rejected. Path-parameter checks
include semicolon and mixed parameter delimiters, case-insensitive credential
labels, and nested encodings. URL safety inspection is bounded to five decoding
passes; deeper unresolved encodings fail closed. Literal encoded percent characters
such as `%25` and otherwise safe encoded paths are preserved in the original URL.
Only the original URL must have well-formed percent triplets and UTF-8 escapes;
decoded literal percent characters are not themselves required to be escapes.
RQ hosts accept a hostname or IP with
an optional valid port; URLs, embedded credentials, paths, and parameters are
rejected.

Unknown secrets deliberately or accidentally entered into approved metadata such
as display names or arbitrary endpoint path segments cannot be detected with
certainty. The module detects supplied credential strings and recognized URL
credential forms; it does not consult live settings or environment variables to
discover other secrets, nor infer arbitrary transformations such as encryption or
base64 encoding. Approved metadata still needs review before sharing.

`TransferValidationError` exposes only a static `code`, static message, and
schema-owned `path`. Unknown property names, values, and raw JSON decoder
exceptions are not included in diagnostics. There are no logs or provider calls.

## Coherent export and supported backends

SQLite export uses one verified current snapshot of all documents/selections;
retained history contributes only credential rejection canaries. JSON export
takes an exclusive snapshot lease across three raw reads. Cooperative JSON
writers hold its shared counterpart for the whole read-modify-write operation,
inside existing maintenance use. Offline writers already require exclusive
maintenance. Busy acquisition fails; no lock upgrades or mixed-file atomicity
claims are made. Older releases and unmanaged editors remain outside this contract.

Exports use Phase 4A's batch projection and canonical serializer. Unknown native
fields cause rejection, not silent omission. A staged store never selects SQLite;
if present, its verified history is used only for credential rejection. No
environment values are resolved. Downloads have a fixed filename, JSON content
type, `no-store` and `nosniff`.

JSON supports coherent export and non-mutating previews. Multi-file JSON apply
and rollback return `UNSUPPORTED_JSON_APPLY` with no applicable receipt: existing
files cannot atomically publish three settings documents. Import never activates
SQLite to work around this restriction.

## Preview and credential decisions

Preview takes a complete portable object/JSON string, `services`, `mode` (`merge`
or `replace`), `apply_selections` and `credential_policy`. Merge keeps other
destination IDs; replace removes IDs absent from selected sections. Selections
change only when requested. All input/prospective references must validate,
including references from untouched services.

Responses show added/changed/unchanged/removed IDs, before/after selections and
explanations, canonical prospective metadata, credential actions, missing-key
diagnostics, legacy migrations and required consents. Profile differences compare
metadata; credential actions are separate. Preview never writes settings or
generations, probes providers or starts jobs.

Every imported profile in a selected section needs one action:

| Action | Behavior |
| --- | --- |
| `retain` | Keep only the current same-ID secret with exactly matching destination identity. |
| `clear` | Store an empty secret, including explicitly unconfigured selected profiles. |
| `replace` | Require a fresh value through the separate private apply input. |

Identity means model base URL/model ID; search provider/model reference; or
RQData provider/user/host. Display names are excluded. Different spellings do not
qualify for retention. New/deleted/reappearing IDs never inherit historical or
neighboring secrets. Missing policy returns `CREDENTIAL_DECISION_REQUIRED` with
no receipt, never implied retention or clearing.

Fresh values are bounded nonempty strings without control characters. Known
current/historical values are rejected unless still authorized for that exact
same profile/destination. Moving destinations can require a new key. No revocation
probe occurs. Fresh input is checked against prospective and undo metadata before
mutation. No secrets, masked prefixes or credential fingerprints enter plans.

Incomplete selected profiles remain unconfigured. `none` never enables fallback.
Durable SQLite import rejects environment intent, including `active_id=null`
migrations, without silently converting it. Untouched raw documents retain
versions, structure, extensions, credentials and selections, including search v1.
Internal comparison views are not exports. Selecting a service with unsupported
native extensions returns `UNSUPPORTED_NATIVE_FIELDS`; no lossy edit is offered.

## One-use receipts and application

Applicable previews persist private metadata-only plans under
`app/secrets/configuration-transfers`. Random receipts bind installation/root,
backend decision, revision, canonical input, operations, policy and consents.
They expire after 600 seconds using the trusted local clock. Plans do not grant
backend authority or activate SQLite.

An installation OS lease serializes transitions; private atomic file publication
persists the claim before SQLite mutation. Claimed/rejected/committed/uncertain
receipts cannot replay. A crash spends the claim even if no settings write occurred.
Interrupted `.pending` artifacts and corrupt records block further plan operations
without automatic cleanup. The limit is 256 retained plans; cleanup needs review.

Apply requires receipt, expected revision, `confirm=true`, exact preview consents
and private inputs matching replacement actions. Authority, state, expiration,
revision, policy and the entire prospective native generation are rechecked.
Changed settings cause `STALE_PREVIEW`. One `ConfigurationStore.update()` publishes
all intended changes under `BEGIN IMMEDIATE` and expected-revision protection.
Callbacks perform no I/O or network calls. Capacity is checked before commit;
ordinary rejection preserves the generation and spends an attempted receipt.
Running jobs retain their captures; no jobs are started or reinterpreted.

Plans and settings are separate durable publications: there is no distributed
transaction or exactly-once acknowledgement claim. Write errors trigger revision
inspection, never callback replay. `claimed`/`uncertain` may survive process death.
Failure recording success reports `APPLICATION_UNCERTAIN`, not success/rollback.
Inspect operation/current revision explicitly. Unconfirmed operations cannot
authorize rollback. Existing runtime/recovery fences remain authoritative.
If the revision is demonstrably unchanged after an ordinary publication failure,
`APPLICATION_REJECTED` reports that observation. The receipt is still spent;
another attempt requires a new preview. This does not undo any concurrent writer.

## Logical rollback

Rollback reads only a committed import's sanitized pre-change description. Its
installation/decision and exact resulting revision must still be current. Any
intervening write, including a clear/rotation, causes `ROLLBACK_CONFLICT`.

Request a preview with an empty policy list to inspect restored metadata, then
choose explicit actions to obtain a receipt. Reappearing profiles have no keys;
retention uses only current same-ID/same-identity values. Missing/revoked values
require fresh input. Apply creates a new audited generation; no historical row,
database or backup is restored. Only confirmed imports are supported origins;
recursive rollback and undo of uncertain operations are unavailable. There is no
automatic rollback on failure, restart or expiration.

## Full production API

Routes require loopback peer and Host, a local Origin when supplied, and
`X-AlphaLab-Configuration-Consent: local-configuration-transfer`. Existing
workstation Basic authentication still applies when enabled. Remote/proxied
administration and `null` origins are unavailable. This header is a local/CSRF
requirement, not an authentication substitute.

| Method/path | Contract |
| --- | --- |
| `GET /api/configuration-transfer/export` | Canonical attachment `alphalab-configuration.json`. |
| `POST /api/configuration-transfer/preview` | `configuration`, `services`, `mode`, `apply_selections`, `credential_policy`. |
| `POST /api/configuration-transfer/apply` | `receipt`, `expected_revision`, `confirm`, `consents`, `credentials`. |
| `POST /api/configuration-transfer/rollback/preview` | `operation`, `credential_policy`. |
| `POST /api/configuration-transfer/rollback/apply` | Same confirmation/private-input fields as apply. |
| `POST /api/configuration-transfer/operation` | `operation`; returns safe state, confirmed and observed current revisions. |

For example (include policy for every imported model row):

```json
{
  "configuration": "<complete portable JSON file contents>",
  "services": ["models"],
  "mode": "merge",
  "apply_selections": false,
  "credential_policy": [{"service":"models","id":"research","action":"retain"}]
}
```

Confirm using the returned receipt/revision and exact `required_consents`.
`credentials` is `[]` unless replacement was chosen; private entries contain only
`service`, `id`, `value`. Keep these inputs out of portable files, receipts and
diagnostics; accepted values are saved only in the private native store. Apply returns
`operation`, `state`, `revision`, `kind`; preview returns an optional receipt.

Envelopes allow 1 MiB, ten levels and 8,192 values; portable limits remain as above.
Duplicate keys, nonfinite values, unknown options, arbitrary paths and request
backend switches fail closed. Errors contain static codes/messages. HTTP codes:
conflict/replay/capacity/uncertain 409, expiry 410, validation 422, authorization
403 (existing Basic 401), unavailable/corrupt records 503. Responses use `no-store`.

Phase 4D still needs file selection/download, previews, per-profile decisions,
private entry, consent and conflict/uncertain-state UX. Real Conexus, macOS/POSIX,
physical power loss, whole-root rollback provenance and physical erasure remain
uncertified or unavailable. Tests use disposable roots, real SQLite/OS leases,
test-owned processes, synthetic secrets, fake SDK/provider entry points and
substituted managed cores; these do not certify the real bundle.
