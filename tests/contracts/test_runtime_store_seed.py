from __future__ import annotations

import sqlite3

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
