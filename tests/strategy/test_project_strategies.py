from __future__ import annotations

import sqlite3

import pytest

from alphalab import StrategyRepository, StrategySourceError


@pytest.fixture
def repo(tmp_path):
    value = StrategyRepository(tmp_path / "strategies.db")
    value.clone_project("sdk-v1-default", "research")
    yield value
    value.close()


def test_existing_project_migrates_without_changing_frozen_source(repo):
    original = repo.get_package("research")
    path = repo.path
    # Reproduce the pre-catalog schema with an existing project/package.
    with sqlite3.connect(path) as connection:
        connection.execute("DROP TABLE project_strategy_packages")
        connection.execute("DROP TABLE project_strategies")
    migrated = StrategyRepository(path)
    try:
        project = migrated.get_project("research")
        assert project["strategy_id"] == "main"
        assert project["strategy_path"] == "strategies/main.py"
        assert project["strategies"] == [{"id": "main", "name": "主策略", "path": "strategies/main.py", "current_revision": original["revision"]}]
        assert migrated.get_package("research", original["revision"]) == original
    finally:
        migrated.close()


def test_strategies_edit_independently_and_share_validated_factor_snapshots(repo):
    main = repo.get_project("research")
    copy = repo.create_strategy("research", "concentrated", name="集中持仓")
    assert copy["current_revision"] == main["current_revision"]
    changed = repo.update_strategy_source("research", copy["strategy_source"].replace("top_n: int = 10", "top_n: int = 3"),
                                         expected_source_sha256=copy["draft_source_sha256"])
    frozen = repo.get_package("research", changed["current_revision"])
    assert "top_n: int = 3" in frozen["source"]
    assert repo.select_strategy("main").get_project("research")["draft_source"] == main["draft_source"]
    factor = repo.get_factor_source("research", "momentum_20d")
    updated = repo.replace_factor_source("research", "momentum_20d", factor["source"].replace(" - 1.0", " - 0.9"))
    assert set(updated["affected_strategies"]) == {"main", "concentrated"}
    assert repo.select_strategy("concentrated").get_project("research")["strategy_source"] == changed["strategy_source"]
    assert " - 0.9" in repo.get_package("research")["source"]
    assert repo.get_package("research", frozen["revision"]) == frozen
    assert repo.has_strategy_package("research", frozen["revision"])
    assert not repo.select_strategy("main").has_strategy_package("research", frozen["revision"])


def test_shared_factor_failure_rolls_back_every_strategy(repo):
    main = repo.get_project("research")
    extra = repo.create_strategy("research", "alternate", name="另一策略")
    # Only this strategy depends on the extra shared factor.
    repo.add_factor_source("research", '@factor(id="extra")\ndef extra(context):\n    return context.history("close", window=1).iloc[-1]\n')
    extra = repo.get_project("research")
    source = extra["strategy_source"].replace('weights={"momentum_20d": 1.0}', 'weights={"extra": 1.0}')
    source = source.replace('parameters={"momentum_20d": {"window": 20}}', 'parameters={}')
    repo.update_strategy_source("research", source)
    before = repo.snapshot_strategies("research", ["main", "alternate"])
    with pytest.raises((StrategySourceError, ValueError), match="extra|alternate"):
        repo.select_strategy("main").delete_factor_source("research", "extra")
    assert repo.snapshot_strategies("research", ["main", "alternate"]) == before
    assert repo.get_factor_source("research", "extra")
    assert repo.get_package("research", main["current_revision"])["source"] == main["draft_source"]


def test_strategy_identity_rename_readonly_and_stale_writes(repo):
    extra = repo.create_strategy("research", "other", name="原名称")
    renamed = repo.rename_strategy("research", name="新名称")
    assert renamed["strategy_id"] == "other"
    assert renamed["strategy_path"] == "strategies/other.py"
    assert renamed["current_revision"] == extra["current_revision"]
    with pytest.raises(ValueError):
        repo.create_strategy("research", "../../outside", name="invalid")
    with pytest.raises(FileExistsError):
        repo.create_strategy("research", "other", name="重复")
    with pytest.raises(PermissionError):
        repo.select_strategy("main").create_strategy("sdk-v1-default", "other", name="readonly")
    second = StrategyRepository(repo.path).select_strategy("other")
    try:
        second.update_strategy_source("research", extra["strategy_source"].replace("top_n: int = 10", "top_n: int = 4"))
        with pytest.raises(RuntimeError, match="draft changed"):
            repo.select_strategy("other").update_strategy_source("research", extra["strategy_source"], expected_source_sha256=extra["draft_source_sha256"])
        assert repo.select_strategy("main").get_project("research")["strategy_id"] == "main"
        assert second.get_project("research")["strategy_id"] == "other"
    finally:
        second.close()
