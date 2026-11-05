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

Storage stays under the private runtime directory's `snapshots/`. Do not commit
or publish snapshots. Retention is append-only: deletion and garbage collection
are intentionally unavailable until run/report references are authoritative.
Failed captures may leave unreferenced objects; they cannot become readable through
an incomplete manifest. Atomic rename protects publication, but this increment
does not promise power-loss durability or automatic cleanup after process crashes.

Pending #15 work: persisted run/experiment manifest binding, recipe/code/environment
versions, readable API provenance, reference-aware retention, migration/recovery
checks, and end-to-end validation-output replay. Frozen data does not by itself
establish PIT correctness or verified out-of-sample validity.
