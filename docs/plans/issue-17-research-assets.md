# Issue #17: research asset version and change management

Status: proposed implementation plan, not a description of shipped features.

Related issue: [#17](https://github.com/yuntingliu/quant-framework/issues/17).
Individual contributor: **gry1024**. One contributor owns design, implementation,
tests, documentation, and review follow-up. The Steps below define deliverables
and completion conditions, not a calendar or a team allocation.

## Objective and financial value

Make it possible to explain which source, data, and research results support a
report, understand the consequences of changing a factor, and safely restore
source without changing historical evidence. A research conclusion should remain
interpretable after its author edits the project or updates local data.

Example: a researcher changes a momentum window from 20 to 60 observations.
The workbench should show the source difference and affected strategies, retain
the evidence behind the earlier report, and allow restoration as a new revision.
Neither restoration nor reading the earlier report should rerun its backtest.
Adopting the factor is a recorded research decision, not proof of investment merit.

## Existing foundation and gaps

The issue names dev3 commit `d0ce50c` and PR #5 as its reference baseline.
This plan was checked against upstream `main` at `19256022ca0972c5628f3e40ea237ff7d2291ef5`.
Implementation branches should start from current `main` and recheck these boundaries.

| Existing owner | What can be reused | What #17 must add or verify |
| --- | --- | --- |
| [StrategyRepository](../../alphalab/strategy/repository.py) and [ProjectStrategies](../../alphalab/strategy/project_strategies.py) | Project/strategy identities, source units, immutable packages, assembly/probes, shared-factor saves and affected-strategy results | Readable history/diffs, change records, restoration, complete concurrency and identity checks |
| [ValidationRepository](../../alphalab/validation/repository.py) | Separate validation source packages and save path | History presentation and restoration with correct concurrency checks |
| [Data operations](../04_DATA_OPERATIONS.md) and [development guide](../03_DEVELOPMENT_GUIDE.md) | Canonical recipe save/execution APIs; sync jobs retain recipe source/hash | Recipe edit history where absent; distinguish edit/submission versions from acquisition provenance |
| [Factor research](../../alphalab/analytics/factor_research.py) and [factor evidence](../../alphalab/analytics/factor_evidence.py) | Existing research calculations and evidence semantics | Version-bound evidence references, lifecycle decisions and usage relationships |
| [ReportWorkbench](../../apps/desktop/src/widgets/research/ReportWorkbench.tsx) | Global report list, document/table/chart views, table filtering and CSV export | Cross-report search/comparison, source/evidence navigation and preserved report revisions |
| [Architecture](../01_ARCHITECTURE.md) and [workspace output service](../../apps/api/services/workspace_output_service.py) | Canonical Conexus Documents, output preparation and atomic workspace edit | Revision retention and structured evidence links through that same publication boundary |

Two implementation details need explicit treatment:

- `_commit_all_sources` can reuse a package with identical content. Restoring an
  old package pointer alone does **not** satisfy the requirement for a new
  restoration revision. Extend the canonical save path with explicit restoration
  lineage and a new revision while allowing ordinary saves to keep deduplication.
- Strategy, recipe, validation, and Conexus persistence do not constitute one
  demonstrated transaction. Do not promise a whole-project atomic rollback by
  chaining their existing save APIs.

## Scope and ownership

Deliver source history/comparison, safe restoration, a factor asset catalogue,
report-to-evidence navigation, search/comparison/export, and preservation of old
content and references. Reuse current workbenches and SDK boundaries.

Do not introduce a second factor evaluator, backtest engine, report-body database,
mandatory manual freeze workflow, automatic investment-valid label, or implicit
reruns. No new data acquisition or experiment orchestration system belongs here.
No broad Conexus core rewrite is planned; required publication extensions must
follow the supported integration boundary and pinned-core upgrade process.

## Proposed design

The following are logical contracts for review, not finalized API or table names.

### Identity, history, and impact

- Use stable project and strategy IDs, not display names, as reference keys.
  Add stable project-scoped factor identity metadata if the current public factor
  ID/path cannot survive rename. Preserve old aliases and package-unit references;
  copying creates a new identity and records its origin, even for equal names.
- A source reference carries kind, owner IDs, immutable revision/package identity,
  unit identity/path, and content digest. Revision numbers are owner-scoped.
  Recipe and validation references remain separate from strategy package numbers.
- A change record carries previous/new references, operation, actor where known,
  an automatic summary, optional user note, and restoration/copy origin.
  Do not require an extra manual versioning step or invent legacy authorship.
- Show per-unit diffs and the full set of strategies whose assembled packages
  change. Since every assembled strategy includes the shared factor library,
  distinguish package impact from confirmed use by a signal. For dynamic uses
  that cannot be resolved statically, show unknown/potential impact rather than
  declaring the factor unused. Retain historical usage edges separately.

### Safe source restoration

1. Read a historical unit/package and preview its changes, impacted strategies,
   required dependencies, and expected current owner revisions.
2. Block application while the relevant editor has unsaved code or pending visual
   edits. Let the user cancel or save first; never silently discard the buffer.
3. Restore through the canonical assembly, validation and probe path. Stage all
   strategy/factor changes together, then recheck expected versions inside the
   write transaction, including changes made during probes. Reject stale requests.
4. Commit a new revision plus its change/lineage record atomically. Preserve old
   package bytes, result records and references. Retrying the same operation must
   not duplicate the restoration or overwrite newer edits.
5. Recipe and validation restoration use their own canonical owners, with any
   missing history/concurrency support added there. Restore one owner at a time;
   do not expose a whole-project restore button that implies cross-store atomicity.
   A strategy/factor multi-file restoration remains one atomic operation.

Saving/probing follows the existing trusted-local Python approval rules. A restore
must not submit a backtest, sync data, or execute research as a hidden side effect.
An old source version incompatible with the current SDK fails explicitly; its
history remains readable and is not silently translated into different code.

### Factor catalogue and research evidence

Each asset exposes its definition/source version, data requirements, usage,
origin/copies, and status: `candidate`, `observation`, `adopted`, or `retired`.
Status changes record a reason and supporting evidence references. Retirement is
not deletion; adopting a factor does not change a strategy automatically.

Evidence references identify the exact factor version, result/run, sample dates,
universe, parameters, data manifest when available, assumptions, and IC/decay/
grouped outputs. Reuse existing analytics; where an evidence type is absent,
show it as unavailable instead of fabricating it. A factor edit does not transfer
old evidence or endorsement to the new definition. Historical evidence remains
visible with its original version and scope; the new version needs explicit review.

### Report revisions and provenance

Keep report bodies in canonical Conexus Documents. Preserve logical report IDs
and global history. Attach structured versioned links to project, strategy and
validation packages, recipe evidence, experiment/trial when applicable, backtest,
data manifest, and actual persisted outputs. Resolve the links server-side;
Agent-written text or an unchecked identifier is not proof of lineage.

Before overwriting a report, retain its previous content and evidence links in
the canonical workspace. First verify whether the supported Document interface
already exposes durable revisions. If not, propose immutable revision Documents
with a logical-report pointer, created with the new content/link update in the
same atomic workspace edit. Do not add a second Markdown copy in SQLite. Prove
this publication contract before claiming historical report retention.

A reference/search index may cache IDs and metadata only. It must be rebuildable
from canonical publication records. Report publication and reference protection
must not race with deletion: prepare a retention reservation before publication,
finalize it idempotently after the workspace commit, and reconcile pending
operations after interruption. Uncertain publication outcomes retain protection.
Until #15 and other owners support that protocol, block destructive cleanup of
possibly referenced evidence; a stale search cache is not authority to delete.

The report view should show readable labels, source differences, sample scope,
and actual result summaries. Keep internal hashes/execution IDs out of narrative
Markdown; use structured technical evidence rather than raw hashes as the answer.

For missing legacy lineage, distinguish `unlinked`, `missing`, `unavailable`, and
`verified` references. Never attach today's source/data to an old report by guess.
Current recipe text is not proof of the recipe that acquired historical data.

### Search, comparison, export, and retention

- Search/filter assets by type, project, title/name, factor status and reference
  availability; support report-content search through the canonical content owner.
- Compare source revisions and report revisions. For result comparisons, display
  both samples, universes, costs and data versions; flag incompatible conditions
  rather than imply like-for-like performance.
- Export selected report content plus a structured evidence manifest and selected
  diffs. Preserve existing table CSV export. Mark incomplete lineage explicitly;
  an export without snapshots is not a self-contained reproducibility package.
- Default to retaining referenced revisions and content. Archiving removes an
  asset from the active list without deleting evidence. Do not implement automatic
  destructive history pruning in this issue. Private exports/data remain off Git.

## Dependency contracts

| Integration | Needed input or behavior | Work that can proceed independently |
| --- | --- | --- |
| [#15](https://github.com/yuntingliu/quant-framework/issues/15), explicit delivery prerequisite | Stable manifest ID, readable dataset/sample/version metadata, availability/integrity status, pinned run association, and reference-protection agreement | Source history, restoration, identity, factor catalogue and report-link contract tests |
| [#18](https://github.com/yuntingliu/quant-framework/issues/18), related integration rather than an explicit #17 prerequisite | Optional experiment/trial IDs resolving to actual runs and their frozen source/data references | All non-experiment assets and reports; an ordinary backtest report needs no experiment |

At the inspected baseline, [PR #45](https://github.com/yuntingliu/quant-framework/pull/45)
for snapshots and [PR #27](https://github.com/yuntingliu/quant-framework/pull/27) /
[PR #42](https://github.com/yuntingliu/quant-framework/pull/42) for experiments are
unmerged. These are interface discussions, not APIs assumed to exist on `main`.
Coordinate the report-reference boundary: #17 owns report associations; #15 owns
snapshot availability and deletion enforcement. Do not duplicate either owner.

Use small deterministic test adapters during independent development, label them
as such, and replace them with contract tests against the actual integrated
services before declaring delivery. Missing prerequisite interfaces remain a
recorded acceptance gap, not an excuse to substitute current data silently.

## Implementation steps

### Step 1 — Confirm contracts and establish the baseline

Deliver a source-owner map, reference schema, identity/rename rules, restore
transaction boundaries, and report-publication retention design. Prepare isolated
fixtures with two strategies sharing a factor, existing reports, and legacy data.
Capture current behavior using existing repository/service entry points.

Completion: maintainers can review the design choices below; each acceptance item
has a named test scenario. Resolve publication/history feasibility before writing
report migration code. This planning PR is the initial artifact for this Step,
not evidence that its implementation checks have already passed.

### Step 2 — Deliver source history and comparison

Add bounded/paginated reads for strategy/factor, validation and recipe histories,
readable change notes, per-unit diffs, and impacted-strategy presentation. Extend
the canonical recipe owner if editable recipe versions are not retained today.
Expose history in the relevant existing workbenches.

Completion: users can inspect changes without writes or research execution;
legacy versions retain their identifiers and bytes; rename/copy identities remain
unambiguous. Recipe history begins at migration where older edits are unavailable.

### Step 3 — Deliver safe restoration

Implement preview/apply through canonical saves, explicit new restoration
revisions, dirty-editor protection, transactional concurrency checks and retry
handling. Cover shared-factor restoration across affected strategies, as well as
separate recipe and validation restores.

Completion: stale requests and failed probes leave no source/history partial
writes; unsaved work survives; historical runs remain unchanged and no job starts.

### Step 4 — Deliver factor assets and version-bound evidence

Add the four statuses, reasoned transitions, definition/data-requirement views,
usage/copy relationships, and links to existing IC/decay/grouped results.

Completion: same-name copies remain distinct, edited definitions cannot inherit
old conclusions silently, and every displayed conclusion retains its sample and
assumptions. Unavailable evidence is visible rather than calculated implicitly.

### Step 5 — Deliver report lineage and asset discovery

Implement canonical report revision retention, validated evidence links, reference
protection/reconciliation, cross-asset search, comparison and export. Integrate
actual #15 manifests; connect #18 experiment references where that interface is
available, while preserving reports without experiments.

Completion: a report opens its exact source/validation packages, data manifest and
persisted results; editing it preserves the prior content. Failure/restart tests
prove retention and retry behavior. Test adapters alone do not satisfy this Step.

### Step 6 — Complete migration, regression checks and demonstration

Use additive, repeatable migrations with recoverable failure behavior. Preserve
existing project/report IDs, package bytes, backtest results and historical links.
Seed only known metadata; never reconstruct nonexistent history. Test canonical
store migration and reference-index rebuild independently.

Deliver user/developer documentation, a reproducible local demo, the acceptance
matrix with test links, and an individual contribution/review log. Each code PR
states its completed Step subset, checks, limitations and review follow-up.

Completion: the full acceptance matrix passes against integrated owners, including
legacy fixtures and end-to-end workbench behavior. Run the repository quality gates
from [CONTRIBUTING.md](../../CONTRIBUTING.md); distinguish local results, CI,
synthetic evidence and any real-provider checks. No production data is required
for the deterministic demonstration.

## Acceptance and demonstration matrix

| Requirement | Reproducible check | Steps |
| --- | --- | --- |
| Report traces to source, validation, manifest and actual results | Publish a report from a known run, edit live source/update current data, and resolve the old report to unchanged frozen references; fail visibly for a missing manifest | 5–6 |
| Shared-factor impact and no partial save | Two strategies share a factor; assert full package impact, inject a probe failure, and compare all source/package/change rows before and after | 2–3 |
| Historical runs never read later code or rerun implicitly | Read/compare/restore with job-submission spies; assert no new job and unchanged stored results | 2–3, 5 |
| Restore creates a new revision | Restore identical historical content; assert new revision and origin, preserved old packages, and idempotent request retry | 3 |
| Same-name copies, rename and concurrent edit | Copy/rename factor; exercise two stale editors and a write during probes; assert stable identities and conflict without overwrite | 2–4 |
| Unsaved user changes survive | Browser test with unsaved Python and pending visual edits; cancel/blocked restore retains both | 3 |
| Migration and historical report content survive | Compare legacy content/IDs before and after migration; rerun migration; interrupt publication and rebuild index without losing revisions | 5–6 |
| Factor conclusions retain scope and assumptions | Attach version-specific evidence, edit source, change status; old evidence remains labelled, no automatic investment-valid claim | 4 |
| Search, comparison, export and retention | Find old/new revisions, compare differing samples, export bounded content/links, and reject deletion while references are unresolved | 5–6 |

The end-to-end demo follows one research story: preserve a report based on a
20-observation momentum factor, change the window to 60, inspect impact and diff,
restore the source as a new revision, and reopen/export the original report with
its unchanged evidence. Include failed-probe and concurrent-edit negative cases.

## Questions for maintainer review

1. Is the incremental scope and reuse of existing workbenches appropriate for #17?
2. Is explicit restoration revision creation, alongside ordinary content deduplication,
   the preferred extension to the canonical source repository?
3. Should historical report retention use existing Document revisions if supported,
   or immutable revision Documents under the same logical report? Is the proposed
   reservation/reconciliation boundary acceptable for #15 reference protection?
4. Are separate owner-scoped restores (atomic strategy/factor restoration, separate
   recipe/validation operations) preferable to a cross-store project restore?
5. Can the proposed #15 manifest and optional #18 experiment reference contracts
   serve as the integration boundary, with unresolved links reported explicitly?

Approval of this plan would establish an implementation direction, not certify
completed features or close #17. Design changes discovered through implementation
and review will be reflected here and in the linked incremental PRs.
