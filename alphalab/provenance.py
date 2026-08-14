"""Reproducible research fingerprints for code, strategy, and input data."""
from __future__ import annotations

import hashlib
import importlib.metadata
import json
import platform
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from alphalab.dataio.catalog import DataCatalog
from alphalab.utils.paths import DATA_DIR, RUNTIME_DIR


def build_research_provenance(
    profile: str,
    strategy_yaml: str | None = None,
    *,
    strategy_python: str | None = None,
    data_root: str | Path | None = None,
    runtime_root: str | Path | None = None,
) -> dict:
    if profile not in {"demo", "runtime"}:
        raise ValueError("profile must be demo or runtime")
    strategy_hash = (
        hashlib.sha256(strategy_yaml.encode("utf-8")).hexdigest()
        if strategy_yaml
        else None
    )
    strategy_python_hash = (
        hashlib.sha256(strategy_python.encode("utf-8")).hexdigest()
        if strategy_python is not None
        else None
    )
    return {
        "version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "profile": profile,
        "strategy_sha256": strategy_hash,
        "strategy_python_sha256": strategy_python_hash,
        "strategy_python": (
            {
                "source": strategy_python,
                "sha256": strategy_python_hash,
            }
            if strategy_python is not None
            else None
        ),
        "code": _code_state(),
        "environment": _environment_state(),
        "data": (
            _demo_snapshot(Path(data_root) if data_root is not None else DATA_DIR)
            if profile == "demo"
            else _runtime_snapshot(
                Path(runtime_root) if runtime_root is not None else RUNTIME_DIR
            )
        ),
    }


def _demo_snapshot(root: Path) -> dict:
    paths = [
        root / "manifest.json",
        root / "market" / "bars.parquet",
        root / "fundamentals" / "fundamentals.parquet",
        root / "factors" / "factor_returns.parquet",
        *(root / "instruments").rglob("*.parquet"),
    ]
    files = _fingerprint_files(paths, root)
    manifest = {}
    manifest_path = root / "manifest.json"
    if manifest_path.exists():
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest = {
            "bundle": raw.get("bundle"),
            "generated_at": raw.get("generated_at"),
            "sample_start": raw.get("sample_start"),
            "cutoff_date": raw.get("cutoff_date"),
            "symbol_count": raw.get("symbol_count"),
        }
    return {
        "kind": "immutable_bundle",
        "manifest": manifest,
        "files": files,
        "aggregate_sha256": _aggregate(files),
    }


def _runtime_snapshot(root: Path) -> dict:
    catalog = DataCatalog(root)
    dataset_ids = [
        "rq.instruments",
        "rq.bars",
        "canonical.fundamentals",
        "runtime.factor_returns",
    ]
    paths = [path for dataset in dataset_ids for path in catalog.files(dataset)]
    files = _fingerprint_files(paths, root)
    return {
        "kind": "runtime_partitions",
        "datasets": {
            dataset: _portable_dataset_status(catalog.status(dataset))
            for dataset in dataset_ids
        },
        "files": files,
        "aggregate_sha256": _aggregate(files),
    }


def _portable_dataset_status(status: dict) -> dict:
    return {
        key: value
        for key, value in status.items()
        if key not in {"path", "error"}
    }


def _fingerprint_files(paths: list[Path], root: Path) -> list[dict]:
    files = []
    for path in sorted({item.resolve() for item in paths if item.exists()}):
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        try:
            relative = path.relative_to(root.resolve()).as_posix()
        except ValueError:
            relative = path.name
        files.append(
            {
                "path": relative,
                "bytes": path.stat().st_size,
                "sha256": digest.hexdigest(),
            }
        )
    return files


def _aggregate(files: list[dict]) -> str:
    digest = hashlib.sha256()
    for item in files:
        digest.update(item["path"].encode("utf-8"))
        digest.update(item["sha256"].encode("ascii"))
    return digest.hexdigest()


def _code_state() -> dict:
    root = Path(__file__).resolve().parents[1]
    source = _source_fingerprint(root)
    try:
        commit = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            stderr=subprocess.DEVNULL,
            timeout=3,
        ).decode("utf-8").strip()
        status = subprocess.check_output(
            ["git", "status", "--porcelain", "--untracked-files=normal"],
            cwd=root,
            stderr=subprocess.DEVNULL,
            timeout=3,
        ).decode("utf-8")
        return {
            "commit": commit,
            "dirty": bool(status.strip()),
            **source,
        }
    except Exception:
        return {"commit": None, "dirty": None, **source}


def _source_fingerprint(root: Path) -> dict:
    paths = [
        root / "pyproject.toml",
        root / "alphalab" / "schema.sql",
        *(root / "alphalab").rglob("*.py"),
        *(root / "dashboard" / "backend").rglob("*.py"),
    ]
    files = _fingerprint_files(paths, root)
    return {
        "source_sha256": _aggregate(files),
        "source_files": len(files),
    }


def _environment_state() -> dict:
    dependencies: dict[str, str | None] = {}
    for name in ("numpy", "pandas", "pyarrow", "scipy", "pydantic", "PyYAML"):
        try:
            dependencies[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            dependencies[name] = None
    return {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "dependencies": dependencies,
    }


__all__ = ["build_research_provenance"]
