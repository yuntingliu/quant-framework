# Configuration transfer format (Phase 4A)

Phase 4A implements pure schema validation, sanitized metadata projection,
serialization, and in-memory migration in
`apps/api/services/configuration_schema.py`. It does not provide API endpoints,
file collection, import application, preview receipts, rollback, transaction
storage, or a frontend. Existing runtime selection and credential behavior are
unchanged.

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
credentials. The apply-time policy and confirmation for environment intent are
deliberately unresolved in Phase 4A.

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
Phase 4B must establish the consistent snapshot and trusted provenance before
calling this module.

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

## Decisions reserved for subsequent phases

1. **Retention versus deletion:** omitted credentials say nothing about retaining,
   clearing, or replacing destination secrets. Existing writer behavior must not
   become the import policy implicitly. Eligibility, explicit consent, and
   cleared-secret state require a separate reviewed contract.
2. **Rollback secrets:** private generations may contain old credentials. Storage,
   retention, revocation precedence, and preventing secret resurrection remain
   unresolved. This format contains no rollback data.
3. **Preview receipts:** choose a stateless signed receipt or process-safe plan
   storage, with installation binding, expiration, idempotency, and stale-state
   detection. No receipts exist in Phase 4A.
4. **Snapshot locking:** coordinate every process and reader, including Node and
   RQData, before claiming a coherent live snapshot or cross-file atomicity.
5. **Incomplete model profiles:** sanitized metadata contains no keys. Existing
   remote-model runtime validation may require them; applying this metadata needs
   an explicit unconfigured state and safe environment-fallback policy.
6. **Crash recovery:** Windows/POSIX locking, durability, publication, interrupted
   imports, and rollback need separate fault-injection and platform tests. Phase
   4A makes no storage, permission, ACL, atomicity, or crash-recovery changes.

The existing model/search/RQData APIs, settings files, normal credential
retention/deletion rules, and Conexus behavior remain authoritative and unchanged.
