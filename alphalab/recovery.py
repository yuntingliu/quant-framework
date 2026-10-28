"""Portable backup, verification and restore for an AlphaLab runtime directory.

The routines work on any platform and never touch services, release links or
credentials outside the directories they are given. A backup holds a copy of
the runtime tree plus a manifest with a SHA-256 checksum for every file, so
integrity can be verified before anything is restored.

Backups contain everything under the runtime directory, including private
model credentials. Keep backup directories private and out of Git.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

MANIFEST_NAME = "backup-manifest.json"
MANIFEST_VERSION = 1
DATABASE_SUFFIXES = {".db", ".sqlite3"}
SKIPPED_NAMES = {"host.lock"}
SKIPPED_SUFFIXES = ("-wal", "-shm")
_LABEL = re.compile(r"[A-Za-z0-9._-]+")


class RecoveryError(RuntimeError):
    """Raised when a backup cannot be created, verified or restored safely."""


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_database(path: Path) -> bool:
    return path.suffix in DATABASE_SUFFIXES


def _is_skipped(path: Path) -> bool:
    return path.name in SKIPPED_NAMES or path.name.endswith(SKIPPED_SUFFIXES)


def _read_only(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)


def _database_ok(path: Path) -> bool:
    try:
        connection = _read_only(path)
        try:
            row = connection.execute("PRAGMA integrity_check").fetchone()
        finally:
            connection.close()
    except sqlite3.DatabaseError:
        return False
    return row is not None and row[0] == "ok"


def _copy_database(source: Path, destination: Path) -> None:
    """Copy a SQLite database with SQLite's own backup API, not a file copy."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    origin = _read_only(source)
    saved = sqlite3.connect(destination)
    try:
        origin.backup(saved)
        saved.execute("PRAGMA journal_mode=DELETE")
    finally:
        saved.close()
        origin.close()


def _check_tree(root: Path, recorded: dict[str, Any]) -> list[str]:
    """Compare a directory tree with the manifest's recorded files."""
    problems: list[str] = []
    resolved_root = root.resolve()
    for name, entry in sorted(recorded.items()):
        path = root / name
        if not path.resolve().is_relative_to(resolved_root):
            problems.append(f"unsafe path in manifest: {name}")
        elif not path.is_file():
            problems.append(f"missing file: {name}")
        elif _sha256(path) != entry.get("sha256"):
            problems.append(f"checksum mismatch: {name}")
        elif entry.get("database") and not _database_ok(path):
            problems.append(f"database integrity check failed: {name}")
    if root.is_dir():
        for path in sorted(root.rglob("*")):
            if path.is_file() and not _is_skipped(path):
                name = path.relative_to(root).as_posix()
                if name not in recorded:
                    problems.append(f"unexpected file: {name}")
    return problems


def _load_manifest(backup: Path) -> dict[str, Any]:
    try:
        manifest = json.loads((backup / MANIFEST_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise RecoveryError(f"backup manifest unreadable: {error}") from error
    if not isinstance(manifest, dict) or manifest.get("version") != MANIFEST_VERSION:
        raise RecoveryError("backup manifest has an unsupported version")
    if not isinstance(manifest.get("files"), dict):
        raise RecoveryError("backup manifest has no file list")
    return manifest


def create_backup(runtime_dir: Path | str, backup_root: Path | str, label: str = "manual") -> Path:
    """Back up ``runtime_dir`` into a new timestamped folder under ``backup_root``."""
    runtime = Path(runtime_dir)
    if not runtime.is_dir():
        raise RecoveryError(f"runtime directory does not exist: {runtime}")
    if not _LABEL.fullmatch(label):
        raise RecoveryError("backup label may only contain letters, digits, '.', '_' and '-'")
    target = Path(backup_root) / f"{_stamp()}-{label}"
    if target.resolve().is_relative_to(runtime.resolve()):
        raise RecoveryError("backup directory must be outside the runtime directory")
    target.mkdir(parents=True, mode=0o700)
    files: dict[str, dict[str, Any]] = {}
    try:
        copy_root = target / "runtime"
        copy_root.mkdir()
        for source in sorted(runtime.rglob("*")):
            if source.is_symlink() or not source.is_file() or _is_skipped(source):
                continue
            relative = source.relative_to(runtime)
            destination = copy_root / relative
            database = _is_database(source)
            if database:
                _copy_database(source, destination)
            else:
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, destination)
            files[relative.as_posix()] = {
                "sha256": _sha256(destination),
                "size": destination.stat().st_size,
                "database": database,
            }
        problems = _check_tree(copy_root, files)
        if problems:
            raise RecoveryError("new backup failed verification: " + "; ".join(problems))
        manifest = {
            "version": MANIFEST_VERSION,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "label": label,
            "files": files,
        }
        (target / MANIFEST_NAME).write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    except BaseException:
        shutil.rmtree(target, ignore_errors=True)
        raise
    return target


def verify_backup(backup_dir: Path | str) -> list[str]:
    """Return a list of problems found in a backup; an empty list means it is intact."""
    backup = Path(backup_dir)
    try:
        manifest = _load_manifest(backup)
    except RecoveryError as error:
        return [str(error)]
    return _check_tree(backup / "runtime", manifest["files"])


def restore_backup(backup_dir: Path | str, runtime_dir: Path | str) -> Path | None:
    """Restore a verified backup into ``runtime_dir``.

    The backup is verified first. The state being replaced is kept beside the
    runtime directory and its path is returned (``None`` if nothing existed).
    If the restored data fails verification, the previous state is put back.
    """
    backup = Path(backup_dir)
    runtime = Path(runtime_dir)
    problems = verify_backup(backup)
    if problems:
        raise RecoveryError("backup failed verification: " + "; ".join(problems))
    recorded = _load_manifest(backup)["files"]
    stamp = _stamp()
    staging = runtime.with_name(f"{runtime.name}.restoring-{stamp}")
    shutil.copytree(backup / "runtime", staging)
    preserved: Path | None = None
    try:
        if runtime.exists():
            preserved = runtime.with_name(f"{runtime.name}.before-restore-{stamp}")
            runtime.rename(preserved)
        staging.rename(runtime)
    except BaseException:
        if preserved is not None and not runtime.exists():
            preserved.rename(runtime)
        shutil.rmtree(staging, ignore_errors=True)
        raise
    problems = _check_tree(runtime, recorded)
    if problems:
        shutil.rmtree(runtime, ignore_errors=True)
        if preserved is not None:
            preserved.rename(runtime)
        raise RecoveryError("restored data failed verification: " + "; ".join(problems))
    return preserved
