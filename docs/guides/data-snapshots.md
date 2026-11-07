# Runtime data snapshots

`SnapshotStore` preserves selected datasets as content-addressed partition files.
Unchanged files share one object. Capture holds all selected dataset read locks
in sorted order and publishes the manifest only after its objects are complete.
Files are copied, not hard-linked to mutable runtime partitions.

```python
from alphalab import SnapshotStore

snapshots = SnapshotStore()
manifest = snapshots.capture()  # All registered datasets; missing datasets stay absent.
with snapshots.open(manifest["id"]) as engine:
    bars = engine.get_bars(["000001.SZ"], "2024-01-01", "2024-03-29")
```

An explicit dataset list limits capture. Manifests record dataset IDs, relative
partition paths, sizes, hashes and capture time. Capture time is not an effective
or availability date; existing PIT fields remain unchanged. This first increment
captures complete dataset partitions, not a trace of the exact queries used by a run.

Opening verifies the manifest and every copied object, then supplies the existing
runtime engine over an isolated temporary view. Missing/corrupt data fails; current
runtime data is never substituted. The engine must stay inside the context manager.
Temporary reads use additional disk space and are removed on normal exit.

Backtest job and batch requests accept an optional `data_manifest_id`. The job
persists this ID with the pinned strategy/validation revisions, settings, and a
copy of the project's recipe draft. Execution verifies the snapshot covers the
required datasets and uses it for the strategy, benchmark, attribution and
validation. Missing/corrupt manifests are rejected at submission with HTTP 409;
missing/corrupt objects fail execution without falling back to current data.
Existing requests without an ID retain their current runtime-data behavior.

Saved backtest provenance includes the manifest, execution-time code fingerprint
and environment, and the submission-time recipe source/hash. The recipe is
explicitly marked `recorded_at_submission` or `not_recorded`: a draft at submission
does not prove which recipe acquired historical partitions. Code/environment
fingerprints describe execution; they do not restore an old executable environment.

Storage stays under the private runtime directory's `snapshots/`. Do not commit
or publish snapshots. All published manifests and their objects are retained,
including those shared by runs or reports. Manifest deletion remains unavailable:
report publication does not yet provide an authoritative reference index.

`snapshots.collect_orphans()` previews unreachable objects from failed captures;
`snapshots.collect_orphans(confirm=True)` removes them. Cleanup shares the capture
publication lock, validates every manifest before deleting anything, and aborts
on invalid manifests or unexpected entries. It never deletes published manifests,
their objects, runtime partitions, or temporary read directories. It is not an
age-based retention policy. Atomic rename protects publication, but this increment
does not promise power-loss durability or automatic cleanup after process crashes.

Synthetic engine/service tests cover return, holding, execution and validation
replay after live partitions change. Real-provider end-to-end validation remains
pending. Other pending work: experiment binding on the #18 dependency branch,
acquisition-time recipe lineage, authoritative report references and selective
manifest deletion, and migration/recovery checks. Frozen data does not by itself
establish PIT correctness or verified out-of-sample validity.
