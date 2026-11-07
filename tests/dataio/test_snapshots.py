import hashlib
import json

import pandas as pd
import pytest

from alphalab import SnapshotStore, StrategyRepository, run_strategy_backtest
from alphalab.dataio import DataLoadError
from alphalab.dataio import snapshots as snapshot_module
from alphalab.dataio.runtime import RuntimeStore


@pytest.fixture
def snapshot_data(tmp_path):
    runtime = RuntimeStore(tmp_path)
    dates = pd.bdate_range("2023-11-01", "2024-03-29")
    bars = pd.DataFrame({"date": dates, "symbol": "000001.SZ"})
    for field in ("open", "high", "low", "close", "raw_open", "raw_high", "raw_low", "raw_close"):
        bars[field] = [10 + i * .02 + (i % 7) * .01 for i in range(len(dates))]
    bars["volume"] = 100_000_000.
    bars["amount"] = bars.close * bars.volume
    bars["limit_up"] = bars.close * 1.1
    bars["limit_down"] = bars.close * .9
    runtime.write("rq.bars", bars)
    runtime.write("rq.paused", bars[["date", "symbol"]].assign(paused=False))
    runtime.write("rq.is_st", bars[["date", "symbol"]].assign(is_st=False))
    runtime.write("rq.instruments", pd.DataFrame([{
        "symbol": "000001.SZ", "asset_type": "CS", "listed_date": "2000-01-01",
        "de_listed_date": "2999-12-31", "exchange": "XSHE", "board_type": "MainBoard",
        "round_lot": 100, "snapshot_date": "2023-11-01",
    }]))
    return runtime, SnapshotStore(tmp_path), bars


def test_snapshot_reuses_objects_and_replays_engine_after_update(snapshot_data, tmp_path):
    runtime, snapshots, bars = snapshot_data
    first = snapshots.capture()
    count = len(list(snapshots.objects.iterdir()))
    snapshots.capture()
    assert len(list(snapshots.objects.iterdir())) == count
    repository = StrategyRepository(tmp_path / "research.db")
    try:
        with snapshots.open(first["id"]) as engine:
            before = run_strategy_backtest(repository, "sdk-v1-default", "2024-01-01", "2024-03-29", engine, execution_data_policy="strict")
        runtime.write("rq.bars", bars.assign(close=bars.close * 2, raw_close=bars.raw_close * 2))
        with SnapshotStore(runtime.root).open(first["id"]) as engine:
            after = run_strategy_backtest(repository, "sdk-v1-default", "2024-01-01", "2024-03-29", engine, execution_data_policy="strict")
        pd.testing.assert_series_equal(before.returns, after.returns)
        pd.testing.assert_frame_equal(before.weights, after.weights)
        assert before.executions == after.executions
        assert not before.returns.empty
        assert snapshots.capture()["files"] != first["files"]
    finally:
        repository.close()


@pytest.mark.parametrize("damage", ["missing", "corrupt", "manifest"])
def test_snapshot_damage_never_falls_back(snapshot_data, damage):
    _, snapshots, _ = snapshot_data
    manifest = snapshots.capture()
    path = snapshots.objects / manifest["files"][0]["sha256"]
    if damage == "missing":
        path.unlink()
    elif damage == "corrupt":
        path.write_bytes(b"corrupt")
    else:
        (snapshots.manifests / f"{manifest['id']}.json").write_text("{}")
    with pytest.raises(DataLoadError):
        with snapshots.open(manifest["id"]):
            pytest.fail("damaged snapshot opened")
    assert not list(snapshots.root.glob("snapshot-*"))


def test_snapshot_rejects_manifest_path_escape(snapshot_data):
    _, snapshots, _ = snapshot_data
    manifest = snapshots.capture()
    del manifest["id"]
    manifest["files"][0]["path"] = "../../outside.parquet"
    payload = json.dumps(manifest).encode()
    identity = hashlib.sha256(payload).hexdigest()
    (snapshots.manifests / f"{identity}.json").write_bytes(payload)
    with pytest.raises(DataLoadError):
        with snapshots.open(identity):
            pytest.fail("unsafe path accepted")


def test_snapshot_read_view_is_isolated(snapshot_data):
    _, snapshots, bars = snapshot_data
    manifest = snapshots.capture(["rq.bars"])
    with snapshots.open(manifest["id"]) as engine:
        read = engine.get_bars(["000001.SZ"], "2024-01-01", "2024-01-02", use_cache=False)
        assert read.close.tolist() == bars.loc[bars.date.between("2024-01-01", "2024-01-02"), "close"].tolist()
    assert not list(snapshots.root.glob("snapshot-*"))


def test_capture_holds_writer_lock_and_publishes_no_partial_manifest(snapshot_data, monkeypatch):
    runtime, snapshots, bars = snapshot_data
    original = snapshot_module._digest
    checked = []

    def digest(path):
        if path in runtime.catalog.files("rq.bars"):
            with pytest.raises(DataLoadError):
                runtime.write("rq.bars", bars)
            checked.append(True)
            raise OSError("injected copy failure")
        return original(path)

    monkeypatch.setattr(snapshot_module, "_digest", digest)
    with pytest.raises(OSError, match="injected"):
        snapshots.capture(["rq.bars"])
    assert checked
    assert list(snapshots.manifests.iterdir()) == []
    runtime.write("rq.bars", bars)


def test_cleanup_preserves_all_manifests_and_shared_objects(snapshot_data):
    _, snapshots, _ = snapshot_data
    first = snapshots.capture()
    second = snapshots.capture()
    orphan = snapshots.objects / ("0" * 64)
    orphan.write_bytes(b"abandoned capture")
    assert snapshots.collect_orphans() == {"objects": [orphan.name], "deleted": False}
    assert orphan.exists()
    assert snapshots.collect_orphans(confirm=True)["deleted"]
    assert not orphan.exists()
    for identity in (first["id"], second["id"]):
        with snapshots.open(identity):
            pass


def test_cleanup_aborts_before_deletion_if_manifest_is_corrupt(snapshot_data):
    _, snapshots, _ = snapshot_data
    manifest = snapshots.capture()
    orphan = snapshots.objects / ("0" * 64)
    orphan.write_bytes(b"abandoned capture")
    (snapshots.manifests / f"{manifest['id']}.json").write_text("{}")
    with pytest.raises(DataLoadError):
        snapshots.collect_orphans(confirm=True)
    assert orphan.exists()


def test_backtest_service_persists_snapshot_and_replays_validation(snapshot_data, tmp_path, monkeypatch):
    from alphalab.store import ResultStore
    from apps.api.services import strategy_service

    runtime, snapshots, bars = snapshot_data
    manifest = snapshots.capture()
    database = tmp_path / "research.db"
    monkeypatch.setattr(strategy_service, "repository", lambda: StrategyRepository(database))
    monkeypatch.setattr(strategy_service, "ResultStore", lambda: ResultStore(database))
    monkeypatch.setattr(strategy_service, "SnapshotStore", lambda: snapshots)

    def forbid_live_engine(_profile):
        pytest.fail("snapshot execution opened live data")

    monkeypatch.setattr(strategy_service, "_engine", forbid_live_engine)
    recipe = {"source": "# frozen recipe", "source_sha256": "recipe-hash"}
    arguments = dict(project_id="sdk-v1-default", start_date="2024-01-01",
                     end_date="2024-03-29", profile="runtime", revision=1,
                     data_manifest_id=manifest["id"], recipe_version=recipe)
    before = strategy_service.run_project_backtest(**arguments)
    runtime.write("rq.bars", bars.assign(close=bars.close * 2, raw_close=bars.raw_close * 2))
    after = strategy_service.run_project_backtest(**arguments)
    assert before["returns"] == after["returns"]
    assert before["validation_output"] == after["validation_output"]
    store = ResultStore(database)
    try:
        provenance = json.loads(store.get_backtest_record(after["id"])["provenance_json"])
        assert provenance["data_manifest_id"] == manifest["id"]
        assert provenance["data"]["manifest"] == manifest
        assert provenance["recipe_version"] == recipe
        assert provenance["code"] and provenance["environment"]
    finally:
        store.close()


def test_backtest_rejects_incomplete_snapshot_without_opening_live_data(snapshot_data, tmp_path, monkeypatch):
    from apps.api.services import strategy_service

    _, snapshots, _ = snapshot_data
    manifest = snapshots.capture(["rq.bars"])
    monkeypatch.setattr(strategy_service, "repository", lambda: StrategyRepository(tmp_path / "research.db"))
    monkeypatch.setattr(strategy_service, "SnapshotStore", lambda: snapshots)
    with pytest.raises(ValueError, match="does not cover"):
        strategy_service.run_project_backtest(
            "sdk-v1-default", "2024-01-01", "2024-03-29", "runtime",
            data_manifest_id=manifest["id"],
        )
