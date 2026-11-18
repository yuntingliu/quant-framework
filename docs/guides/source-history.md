# Source history and comparison

Open **Source history & comparison** from the Strategy, Factor, Data, or
Validation workbench. Select two saved versions, then choose **Compare versions**.
The latest two retained versions are compared automatically on opening.
Use **Load earlier versions** for older history. The Factor workbench shows the
selected strategy package, including the project's shared factors.

The dialog is read-only. Opening it, paging, and comparing do not save editor
contents, execute saved Python, start research jobs, or restore source. The
existing editor and visual-form buffers remain separate from the dialog.
A version sidebar and a comparison pane separate navigation from the code.
Changes are shown with line numbers and green/red added/removed lines, followed
by related strategies. Narrow screens use a horizontal version list.
The history explanation can be expanded when needed.
Recipe template-selection changes are shown separately from source changes.

## Owners and identity

| Source | Canonical store | History semantics |
| --- | --- | --- |
| Strategy and shared factors | `alphalab.db`, strategy packages and frozen units | Distinct packages associated with the selected strategy; not every edit event |
| Validation | `alphalab.db`, validation packages | Project-scoped validation revisions |
| Recipe | `dataio.db`, `data_recipe_history` | Project-scoped saved source and template-selection revisions |

References must include the project, source kind, revision, and unit path; strategy
references also include the selected strategy ID. Display names are not keys.
Equal-name strategies and repeated factor-template copies retain distinct IDs.
Historical `strategy.py` is displayed as `strategies/<selected_id>.py`; factor
paths retain their saved names.

A factor rename appears as removal of the old path and addition of the new one.
This increment does **not** infer a continuous factor identity or copy ancestry
from equal source, similar names, or matching diffs. Version-qualified references
remain unambiguous, but a persistent rename/copy lineage graph is future work.

Strategy packages are content-deduplicated. Saving content that existed before
may reuse a package, so revision order and parent links are not a complete edit
timeline. Validation and recipe revision numbers must not be compared with
strategy revision numbers.

## Read API

Both endpoints accept `kind=strategy|validation|recipe` (default `strategy`) and
`strategy_id` (default `main`, only selects an owner for strategy history).

- `GET /api/strategy/projects/{project_id}/source-history` accepts `limit`
  (1–100, default 20) and optional positive `before`. Items are descending by
  revision. Follow `next_before` until null. Lists contain metadata and automatic
  readable summaries, without source or environment payloads.
- `GET /api/strategy/projects/{project_id}/source-comparison` accepts positive
  `from_revision` and `to_revision`. Both must belong to the requested owner;
  unavailable revisions return HTTP 404. Identical, forward, and reverse
  comparisons are supported.

Each request opens SQLite using `mode=ro`, enables `query_only`, and uses one read
transaction. It does not construct the canonical repositories, invoke migration
hooks, import saved Python, or submit jobs. Missing/incompatible databases and
missing frozen strategy units return HTTP 503 with a safe message. Reads never
rebuild missing units or initialize stores.

Comparisons return per-unit changes, before/after digests, automatic change
summaries, and unified text diffs. Diff output has a shared 100,000-character
budget. A unit exceeding 100,000 source characters skips diff calculation;
`diff_truncated` marks omitted/truncated output. Metadata remains available.
There is no editable change-note field or invented legacy actor information.

### Package impact versus signal use

`affected_strategies` lists current active strategies whose package scope includes
the changed units. All active strategies assemble the shared factor library, so
any shared-factor change lists all of them. A strategy-only change lists the
selected strategy. This is a conservative **current assembly scope**, not a
reconstruction of the strategies active at an earlier edit.

`package_strategy_associations` separately records which strategy IDs are
associated with either selected package. Neither field proves that a signal
actually uses a factor. `usage_analysis: not_evaluated` states that limitation.
Recipe and validation comparisons do not claim strategy-package impact.

## Recipe migration and saves

The canonical `OperationsStore` adds `data_recipe_history`. On initialization,
each existing draft without history becomes revision 1 with
`origin=migration_baseline`. Its source, digest, and selected template are kept
unchanged. The recorded time is when the baseline was retained, not an invented
historical edit time. Repeating initialization does not add another baseline.
Edits predating that baseline are unavailable.

Subsequent canonical saves append a revision only when source or template
selection changes. Saving the same state twice adds no duplicate; changing away
and back records both changes. The existing optimistic source-hash check, history
append, and draft update run under one `BEGIN IMMEDIATE` transaction. A failed
save rolls back both draft and history. Template-only concurrency continues to
use the owner's existing source-hash contract.

Deleting a draft does not prune its retained history. This increment does not
implement asset-deletion retention policy, cross-project-ID-reuse lineage, or
cross-store transactions. Recipe edit history is not evidence of which recipe
acquired an old dataset; acquisition provenance requires the actual job/manifest.

## Verification

Backend cases in `tests/api/test_source_history.py` and
`tests/api/test_source_history_extended.py` use real canonical saves in temporary
stores. They cover owner membership, pagination, identical/reverse comparisons,
frozen bytes, missing schema/units, non-execution, diff limits, shared-factor
impact, same-name strategies, repeated template copies, renames, recipe migration,
metadata edits, transactional rollback, and concurrent stale saves.

For an isolated browser check, install frontend dependencies and Playwright with
Chromium. Run this script with a Python interpreter that can import Playwright;
`--python` selects the backend interpreter with AlphaLab app/dev dependencies:

```bash
python scripts/check_source_history_ui.py --python /path/to/backend/python
```

Optionally pass `--output /path/to/local/screenshots`. The script mounts the real
dialog against the real history API with disposable stores. It checks all source
types, pagination, identical comparisons, error/retry, an independent unsaved text
buffer, absence of write requests, and unchanged database bytes. This is component
acceptance, not a full Electron/Monaco or pending-visual-form restoration test.

Safe restoration, persistent factor lineage, factor evidence/status, report
revision retention, and #15/#18 integrations remain later implementation work.

For an interactive local review page using the same component and isolated example
stores, add `--serve`. Open the printed URL; stop the process with Ctrl+C. This
mode does not require Playwright and does not launch the full workstation.
