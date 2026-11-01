"""Reproduction must tolerate rounding without hiding changes or leaking stores."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from scripts import record_benchmark_references as recorder


@pytest.mark.parametrize(("actual", "expected", "matches"), [
    ({"t": 0.33390812922653657}, {"t": 0.3339081292265366}, True),
    ({"rows": [1, None], "ok": True}, {"ok": True, "rows": [1, None]}, True),
    ({"t": 0.334}, {"t": 0.333}, False),
    ({"count": 34.0}, {"count": 34}, False),
    ({"count": 35}, {"count": 34}, False),
    ({"ok": 1}, {"ok": True}, False),
    ({"value": False}, {"value": None}, False),
    ({}, {"value": None}, False),
    ({"extra": 1}, {}, False),
    ({"rows": [2, 1]}, {"rows": [1, 2]}, False),
    ({"rows": [1]}, {"rows": [1, 2]}, False),
    (float("nan"), 0.1, False),
    (float("inf"), float("inf"), False),
])
def test_payload_comparison_preserves_contract(actual, expected, matches):
    drift = []
    recorder._compare("payload", actual, expected, drift)
    assert (not drift) is matches


@pytest.mark.parametrize("value", [0.33390812922653657, 0.4])
def test_check_compares_values_without_rewriting_recording(tmp_path, monkeypatch, value):
    fixture = tmp_path / "production_payloads.json"
    original = '{"research": {"t": 0.3339081292265366}}\n'
    fixture.write_text(original, encoding="utf-8")
    monkeypatch.setattr(recorder, "PAYLOAD_FIXTURE", fixture)
    monkeypatch.setattr(recorder, "record", lambda: {
        "production_payloads": {"research": {"t": value}},
    })
    monkeypatch.setattr(recorder, "check", lambda _recorded: [])
    monkeypatch.setattr(sys, "argv", ["record_benchmark_references", "--check"])
    if value == 0.4:
        with pytest.raises(SystemExit, match="production_payloads/research/t"):
            recorder.main()
    else:
        recorder.main()
    assert fixture.read_text(encoding="utf-8") == original


@pytest.mark.parametrize("fail", [False, True])
def test_recording_releases_open_sqlite_handles_before_cleanup(tmp_path, monkeypatch, fail):
    # Exercise real process termination and directory cleanup, including failure.
    # The fixture intentionally retains a connection until the worker exits.
    scripts = tmp_path / "worker" / "scripts"
    scripts.mkdir(parents=True)
    (scripts / "__init__.py").write_text("", encoding="utf-8")
    (scripts / "record_benchmark_references.py").write_text(
        "import os, sqlite3\n"
        "from pathlib import Path\n"
        "handles = []\n"
        "def _record_into(root):\n"
        "    db = sqlite3.connect(root / 'locked.db')\n"
        "    handles.append(db)\n"
        "    db.execute('create table test (value integer)')\n"
        "    for key in ('APP_DATA', 'CACHE', 'RUNTIME'):\n"
        "        assert Path(os.environ['ALPHALAB_' + key + '_DIR']).is_relative_to(root)\n"
        + ("    raise RuntimeError('deliberate worker failure')\n" if fail else
           "    return {'root': str(root), 'pid': os.getpid(), 'note': '研究'}\n"),
        encoding="utf-8",
    )
    monkeypatch.setattr(recorder, "REPO_ROOT", scripts.parent)
    original_temporary_directory = recorder.tempfile.TemporaryDirectory
    owned = []

    def temporary_directory():
        directory = original_temporary_directory(dir=tmp_path)
        owned.append(Path(directory.name))
        return directory

    monkeypatch.setattr(recorder.tempfile, "TemporaryDirectory", temporary_directory)
    if fail:
        with pytest.raises(SystemExit, match="deliberate worker failure"):
            recorder.record()
    else:
        result = recorder.record()
        assert result["pid"] != os.getpid()
        assert Path(result["root"]) == owned[0]
        assert result["note"] == "研究"
    assert owned and all(not directory.exists() for directory in owned)
