# Factor assets and version-bound evidence

The Factor workbench gives each project factor a stable research asset identity
independent of its runtime `@factor` ID and `factors/<id>.py` path. Use the asset
panel to inspect lineage, current evidence, usage, and research status.

## Identity and copying

- Renaming through the canonical factor replacement path preserves the asset ID.
  A batch delete plus unrelated add is not treated as a rename.
- Deletion closes the active path. Reusing that path later creates a new asset.
- **Copy asset** creates a new public factor ID and asset ID with the same display
  label and exact source asset/revision lineage. Status and evidence are not copied.
- Project cloning records only known source asset/revision relationships. Equal
  source or hashes never infer lineage.

Assets have four research statuses: `candidate`, `observation`, `adopted`, and
`retired`. Retiring does not delete source. Adoption does not modify a strategy or
prove investment validity. Changing an observed/adopted definition creates a new
factor revision and resets its current status to candidate; the old decision and
evidence remain attached to the old revision. Editing a retired asset does not
reopen it.

## Evidence contract

Research continues through the existing strategy package and factor evaluator.
After the evaluator returns, AlphaLab stores an immutable evidence row containing:

- asset, factor revision, factor-unit hash, strategy and package revisions;
- requested dates, frequency, quantiles, horizons and actual parameters;
- universe entrypoint, sample dates, method assumptions and package-scoped data
  requirements;
- full status, IC, decay, grouped returns and warnings, including `insufficient`
  results.

Historical revision research remains available as historical evidence. Only
observation/adoption requires evidence belonging to the asset's current factor
revision. Empty results are not zero-filled.

Issue #15 is not implemented. Every evidence row therefore records
`data_manifest.status = "unavailable"` with a reason. Current data, recipe source,
or test doubles are not presented as historical data snapshots.

Usage distinguishes structured signal use (`signal_direct`), literal static code
references (`signal_static_reference`), package assembly scope, and
`unknown_or_potential`. Assembly scope alone does not prove signal use.

## API

- `GET /api/strategy/projects/{project}/factor-assets`
- `GET /api/strategy/projects/{project}/factor-assets/{asset}`
- `POST /api/strategy/projects/{project}/factors/{factor}/copy`
- `POST /api/strategy/projects/{project}/factor-assets/{asset}/transitions`
- `GET /api/strategy/projects/{project}/factor-assets/{asset}/evidence`
- `GET /api/strategy/projects/{project}/factor-evidence/{evidence}`

Copy requires source-write and trusted-Python confirmation. Status transitions
require `to_status`, nonempty `reason`, `expected_status_revision`, `request_id`,
`evidence_ids`, and `confirm_write`. Reusing a request ID with a different payload
returns HTTP 409. Retries of the exact request return the original receipt.

Asset and evidence GETs use read-only SQLite connections with `query_only` and no
migration or research execution. Evidence pagination uses `created_at|id` cursors
so records created in the same second are not skipped. Schema and backfill
migrations are additive, transactional, and repeatable.

## Review

Backend coverage is in `tests/strategy/test_factor_assets.py` and
`tests/api/test_factor_assets_api.py`. It includes migration concurrency and
rollback, unrelated delete/add, explicit renames, path reuse, nested and implicit
decorator copying, clone lineage, status idempotency, historical research,
read-only database bytes, and same-second pagination. The real workbench preview
also exercises candidate/observation display, current evidence and same-name copy.
