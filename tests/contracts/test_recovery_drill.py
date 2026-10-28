from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from alphalab.recovery import (
    MANIFEST_NAME,
    RecoveryError,
    create_backup,
    restore_backup,
    verify_backup,
)

REPORT = "conexus/reports/report-1.md"
SESSION = "conexus/sessions/session-1.json"
CONFIG = "conexus/secrets/model-providers.json"
DATABASE = "app/alphalab.db"


def _write(runtime: Path, name: str, text: str) -> None:
    path = runtime / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _rows(path: Path) -> list[tuple[str]]:
    connection = sqlite3.connect(path)
    try:
        return connection.execute("SELECT value FROM results ORDER BY id").fetchall()
    finally:
        connection.close()


@pytest.fixture
def runtime(tmp_path: Path) -> Path:
    """A small fake runtime directory with synthetic data only."""
    root = tmp_path / "runtime"
    (root / "app").mkdir(parents=True)
    connection = sqlite3.connect(root / DATABASE)
    try:
        connection.execute("CREATE TABLE results (id INTEGER PRIMARY KEY, value TEXT)")
        connection.executemany(
            "INSERT INTO results (value) VALUES (?)", [("alpha",), ("beta",)]
        )
        connection.commit()
    finally:
        connection.close()
    _write(root, REPORT, "# Report\nsynthetic research report\n")
    _write(root, SESSION, '{"messages": ["hello"]}')
    _write(root, CONFIG, '{"provider": "example"}')
    _write(root, "editor/strategy/demo/strategy.py", "x = 1\n")
    _write(root, "host.lock", "12345")
    return root


def test_backup_is_verified_and_records_every_file(runtime, tmp_path):
    backup = create_backup(runtime, tmp_path / "backups", label="drill")
    assert verify_backup(backup) == []
    manifest = json.loads((backup / MANIFEST_NAME).read_text(encoding="utf-8"))
    assert set(manifest["files"]) == {
        DATABASE, REPORT, SESSION, CONFIG, "editor/strategy/demo/strategy.py",
    }
    assert manifest["files"][DATABASE]["database"] is True
    assert "host.lock" not in manifest["files"]


def test_recovery_drill_restores_damaged_runtime(runtime, tmp_path):
    backup = create_backup(runtime, tmp_path / "backups")
    (runtime / REPORT).unlink()
    _write(runtime, SESSION, "corrupted")
    connection = sqlite3.connect(runtime / DATABASE)
    try:
        connection.execute("DROP TABLE results")
        connection.commit()
    finally:
        connection.close()

    preserved = restore_backup(backup, runtime)

    assert (runtime / REPORT).read_text(encoding="utf-8").startswith("# Report")
    assert (runtime / SESSION).read_text(encoding="utf-8") == '{"messages": ["hello"]}'
    assert (runtime / CONFIG).read_text(encoding="utf-8") == '{"provider": "example"}'
    assert _rows(runtime / DATABASE) == [("alpha",), ("beta",)]
    assert preserved is not None
    assert (preserved / SESSION).read_text(encoding="utf-8") == "corrupted"
    assert not (preserved / REPORT).exists()


def test_restore_into_missing_runtime_directory(runtime, tmp_path):
    backup = create_backup(runtime, tmp_path / "backups")
    target = tmp_path / "fresh" / "runtime"
    target.parent.mkdir()
    assert restore_backup(backup, target) is None
    assert _rows(target / DATABASE) == [("alpha",), ("beta",)]


def test_corrupted_backup_is_rejected_and_runtime_is_untouched(runtime, tmp_path):
    backup = create_backup(runtime, tmp_path / "backups")
    with (backup / "runtime" / SESSION).open("a", encoding="utf-8") as handle:
        handle.write("tampered")
    assert any(p.startswith("checksum mismatch") for p in verify_backup(backup))
    _write(runtime, SESSION, "current state")

    with pytest.raises(RecoveryError, match="failed verification"):
        restore_backup(backup, runtime)

    assert (runtime / SESSION).read_text(encoding="utf-8") == "current state"
    assert [p.name for p in tmp_path.iterdir() if "restor" in p.name] == []


def test_missing_and_unexpected_files_are_reported(runtime, tmp_path):
    backup = create_backup(runtime, tmp_path / "backups")
    (backup / "runtime" / REPORT).unlink()
    _write(backup / "runtime", "extra/file.txt", "unexpected")
    problems = verify_backup(backup)
    assert f"missing file: {REPORT}" in problems
    assert "unexpected file: extra/file.txt" in problems


def test_unreadable_manifest_is_a_problem_not_a_crash(runtime, tmp_path):
    backup = create_backup(runtime, tmp_path / "backups")
    (backup / MANIFEST_NAME).write_text("not json", encoding="utf-8")
    assert verify_backup(backup)[0].startswith("backup manifest unreadable")


def test_backup_destination_and_label_are_validated(runtime, tmp_path):
    with pytest.raises(RecoveryError, match="outside the runtime"):
        create_backup(runtime, runtime / "backups")
    with pytest.raises(RecoveryError, match="label"):
        create_backup(runtime, tmp_path / "backups", label="../escape")
    with pytest.raises(RecoveryError, match="does not exist"):
        create_backup(tmp_path / "missing", tmp_path / "backups")
    assert not (runtime / "backups").exists()
