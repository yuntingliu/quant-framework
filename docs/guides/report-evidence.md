# Report evidence and deterministic Issue #17 demo

The Report workbench links Conexus Documents to fixed research evidence without
copying report bodies into SQLite. Open **Evidence** to validate references, **Compare**
to inspect two reports, and **Export evidence** to download the Document and a fresh
server-validation snapshot.

## References

`workspaceResult` may declare `projectId` and these reference kinds:

- `strategy_package` and `validation_package`, with `revision`;
- `factor_evidence`, with an evidence `id`;
- `recipe_revision`, with `revision`;
- `backtest`, with a run `id`;
- `data_manifest`, `experiment`, and `trial` for dependency-aware links.

The server checks ownership and fixed records. Status is `verified`, `missing`,
`unavailable`, or `not_linked`; an Agent-provided status or ID alone is not evidence.
Preparation also patches the current Document's `data.provenance` in the same
operation batch, so each report retains its own references after later reports and
reloads. The read-only delivery path can validate and project metadata but never
reserves or confirms references.

Reservation rows live in `alphalab.db` and contain only reference JSON, never report
Markdown. Reservations are created only after semantic validation, are rebound to the
report request ID, and are revalidated under `BEGIN IMMEDIATE`. A confirmed reservation
keeps its status when retried.

Issue #15 is unavailable: data manifests always display an explicit unavailable state,
and exports are marked as not self-contained reproduction packages. Issue #18 remains
optional: experiment/trial references are `not_linked` or `unavailable`.

## Search, comparison, and navigation

Reports can be searched by title, body, ID, project, reference kind, factor path and
factor lifecycle status. Filters cover project, reference availability, asset type, and
factor status. Comparison shows verified/missing/unavailable states, factor sample and
IC, backtest code revisions and execution settings. It exports both reports and their
validation snapshots. Different projects, samples, costs, or invalid references are
called out instead of treated as comparable.

Evidence links first select the owning project. Source links open the exact historical
revision; factor links open the selected evidence; backtest links open the saved run.
Viewing any of them never starts a backtest, recipe, validation, or factor evaluation.

## Declared limits

- Immutable report revision publication and the real local Conexus Host commit/restart
  contract are **not implemented or verified** in this increment.
- Production post-commit reservation confirmation is not wired. The local store's
  confirm method is tested, but no production confirmation lifecycle is claimed.
- The browser check uses isolated Documents and real stores/APIs; it is not a real
  Conexus Host integration test.

## Deterministic demo

Run:

```bash
python scripts/demo_issue17.py
```

The disposable demo covers a shared 20-day momentum factor, 20 → 60 → restore,
version-bound evidence, status reset, same-name copying, a stale restore conflict,
recipe and validation restoration, report-reference validation, and an idempotent
repository reopen. It starts no backtest, sync, validation, factor evaluation, or real
Conexus publication. `tests/integration/test_issue17_demo.py` asserts the same flow.
