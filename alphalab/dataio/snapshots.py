"""Content-addressed runtime snapshots with verified, isolated reads."""

import hashlib
import json
import re
import shutil
import tempfile
from contextlib import ExitStack, contextmanager
from datetime import datetime, timezone
from pathlib import Path

from alphalab.dataio.catalog import DATASETS, DataCatalog
from alphalab.dataio.engine import create_runtime_engine
from alphalab.dataio.errors import DataLoadError
from alphalab.dataio.runtime import RuntimeStore
from alphalab.utils.paths import RUNTIME_DIR


def _encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _digest(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class SnapshotStore:
    def __init__(self, runtime_root=None):
        self.runtime_root = Path(runtime_root) if runtime_root is not None else RUNTIME_DIR
        self.root = self.runtime_root / "snapshots"
        self.objects = self.root / "objects"
        self.manifests = self.root / "manifests"
        self.objects.mkdir(parents=True, exist_ok=True)
        self.manifests.mkdir(parents=True, exist_ok=True)

    def capture(self, datasets=None):
        runtime = RuntimeStore(self.runtime_root)
        names = sorted(set(DATASETS if datasets is None else datasets))
        if not names:
            raise ValueError("snapshot requires at least one dataset")
        for name in names:
            runtime.catalog.spec(name)
        with runtime.dataset_lock("snapshot-publication", timeout_seconds=30), ExitStack() as locks:
            for name in names:
                locks.enter_context(runtime.dataset_read_lock(name))
            files = []
            for name in names:
                for source in runtime.catalog.files(name):
                    if source.is_symlink() or not source.resolve().is_relative_to(self.runtime_root.resolve()):
                        raise DataLoadError("Snapshot source escapes runtime root")
                    digest = _digest(source)
                    target = self.objects / digest
                    if target.exists():
                        if _digest(target) != digest:
                            raise DataLoadError(f"Corrupt snapshot object: {digest}")
                    else:
                        with tempfile.NamedTemporaryFile(dir=self.objects, delete=False) as stream:
                            temporary = Path(stream.name)
                        try:
                            shutil.copyfile(source, temporary)
                            if _digest(temporary) != digest:
                                raise DataLoadError("Partition changed during snapshot capture")
                            temporary.replace(target)
                        finally:
                            temporary.unlink(missing_ok=True)
                    files.append({"dataset": name, "path": source.relative_to(self.runtime_root).as_posix(),
                                  "sha256": digest, "bytes": source.stat().st_size})
            manifest = {"version": 1, "captured_at": datetime.now(timezone.utc).isoformat(),
                        "scope": "complete_dataset_partitions", "datasets": names, "files": files}
            payload = _encoded(manifest)
            identity = hashlib.sha256(payload).hexdigest()
            with tempfile.NamedTemporaryFile(dir=self.manifests, delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(payload)
            try:
                temporary.replace(self.manifests / f"{identity}.json")
            finally:
                temporary.unlink(missing_ok=True)
        return {"id": identity, **manifest}

    def manifest(self, identity):
        if not isinstance(identity, str) or not re.fullmatch(r"[0-9a-f]{64}", identity):
            raise ValueError("invalid snapshot ID")
        path = self.manifests / f"{identity}.json"
        try:
            payload = path.read_bytes()
            if path.is_symlink() or hashlib.sha256(payload).hexdigest() != identity:
                raise DataLoadError(f"Corrupt snapshot manifest: {identity}")
            manifest = json.loads(payload)
            if manifest["version"] != 1:
                raise DataLoadError("Unsupported snapshot version")
            catalog = DataCatalog(self.runtime_root)
            for name in manifest["datasets"]:
                catalog.spec(name)
            seen = set()
            for entry in manifest["files"]:
                relative = Path(entry["path"])
                dataset = entry["dataset"]
                if (dataset not in manifest["datasets"] or relative.is_absolute() or ".." in relative.parts
                        or not relative.is_relative_to(catalog.spec(dataset).relative_path)
                        or relative.suffix != ".parquet" or entry["path"] in seen
                        or not re.fullmatch(r"[0-9a-f]{64}", entry["sha256"])):
                    raise DataLoadError("Invalid snapshot partition entry")
                seen.add(entry["path"])
            return {"id": identity, **manifest}
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise DataLoadError(f"Missing or invalid snapshot manifest: {identity}") from exc

    @contextmanager
    def open(self, identity):
        manifest = self.manifest(identity)
        with tempfile.TemporaryDirectory(prefix="snapshot-", dir=self.root) as directory:
            root = Path(directory)
            for entry in manifest["files"]:
                source = self.objects / entry["sha256"]
                target = root / entry["path"]
                try:
                    if source.is_symlink():
                        raise DataLoadError("Snapshot objects cannot be symlinks")
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(source, target)
                    if target.stat().st_size != entry["bytes"] or _digest(target) != entry["sha256"]:
                        raise DataLoadError(f"Corrupt snapshot object: {entry['sha256']}")
                except OSError as exc:
                    raise DataLoadError(f"Missing snapshot object: {entry['sha256']}") from exc
            yield create_runtime_engine(root)

    def collect_orphans(self, *, confirm=False):
        """Retain every published manifest; remove only unreachable objects."""
        runtime = RuntimeStore(self.runtime_root)
        with runtime.dataset_lock("snapshot-publication", timeout_seconds=30):
            referenced = set()
            for path in self.manifests.iterdir():
                if path.suffix != ".json":
                    raise DataLoadError("Unexpected manifest file; cleanup aborted")
                referenced.update(entry["sha256"] for entry in self.manifest(path.stem)["files"])
            candidates = sorted(path.name for path in self.objects.iterdir()
                                if path.name not in referenced)
            for name in candidates:
                path = self.objects / name
                if path.is_symlink() or not path.is_file():
                    raise DataLoadError("Unexpected snapshot object; cleanup aborted")
            if confirm:
                for name in candidates:
                    (self.objects / name).unlink()
            return {"objects": candidates, "deleted": confirm}
