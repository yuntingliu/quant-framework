from __future__ import annotations

import os
import sqlite3
import subprocess
import sys

import alphalab.store as store_module
from alphalab import ResultStore


def test_default_store_copies_the_bundled_seed(monkeypatch, tmp_path):
    runtime_db = tmp_path / "runtime" / "app" / "alphalab.db"
    monkeypatch.setattr(store_module, "_DEFAULT_DB", runtime_db)

    store = ResultStore()
    try:
        stats = store.stats()
    finally:
        store.close()

    assert runtime_db.is_file()
    assert stats["strategies"] >= 6
    assert stats["backtests"] >= 6

    with sqlite3.connect(store_module._SEED_DB) as bundled:
        bundled_count = bundled.execute("SELECT COUNT(*) FROM backtests").fetchone()[0]
    assert stats["backtests"] == bundled_count


def test_sdk_and_results_share_the_runtime_database(tmp_path):
    environment = dict(os.environ, ALPHALAB_RUNTIME_DIR=str(tmp_path / "runtime"))
    source = '''
from alphalab import ResultStore, StrategyRepository
from alphalab.validation import ValidationRepository
from alphalab.utils.paths import RUNTIME_APP_DIR
strategy = StrategyRepository()
project = strategy.clone_project("sdk-v1-default", "runtime-isolation")
validation = ValidationRepository()
assert validation.get_or_create(project["id"])["project_id"] == project["id"]
store = ResultStore()
assert strategy.path == validation.path == store.path == RUNTIME_APP_DIR / "alphalab.db"
store.close()
validation.close()
strategy.close()
'''
    subprocess.run([sys.executable, "-c", source], env=environment, check=True,
                   capture_output=True, text=True, timeout=60)
