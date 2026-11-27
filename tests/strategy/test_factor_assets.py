from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from alphalab import StrategyRepository


@pytest.fixture
def repo(tmp_path):
    value = StrategyRepository(tmp_path / "strategies.db")
    value.clone_project("sdk-v1-default", "research")
    yield value
    value.close()


def _asset(repo, project_id="research", path="factors/momentum_20d.py"):
    return next(
        item
        for item in repo.list_factor_assets(project_id)
        if item["path"] == path and item["current_revision_id"] is not None
    )


def _legacy_schema(path):
    with sqlite3.connect(path) as db:
        db.execute("PRAGMA foreign_keys = OFF")
        db.execute("DROP TABLE IF EXISTS factor_status_transition_evidence")
        db.execute("DROP TABLE IF EXISTS factor_status_transitions")
        db.execute("DROP TABLE IF EXISTS factor_evidence")
        db.execute("DROP TABLE IF EXISTS factor_assets")
        columns = {
            row[1] for row in db.execute("PRAGMA table_info(factor_source_revisions)")
        }
        db.execute("DROP INDEX IF EXISTS idx_factor_source_revisions_asset")
        if "asset_id" in columns:
            db.execute("ALTER TABLE factor_source_revisions DROP COLUMN asset_id")
        db.execute(
            "DELETE FROM strategy_contract_migrations WHERE name = 'strategy-factor-assets-v1'"
        )


def test_rename_preserves_asset_but_path_reuse_creates_a_new_asset(repo):
    source = '@factor(id="extra")\ndef extra(context):\n    return context.history("close", window=1).iloc[-1]\n'
    repo.add_factor_source("research", source)
    original = _asset(repo, path="factors/extra.py")

    renamed = repo.replace_factor_source(
        "research", "extra", source.replace("extra", "momentum_renamed")
    )
    assert renamed["current_revision"] > original["package_revision"]
    renamed_asset = repo.get_factor_asset("research", original["id"])
    assert renamed_asset["path"] == "factors/momentum_renamed.py"
    assert renamed_asset["current_revision_id"] != original["current_revision_id"]

    repo.delete_factor_source("research", "momentum_renamed")
    assert repo.get_factor_asset("research", original["id"])["current_revision_id"] is None
    recreated_source = source.replace("extra", "momentum_renamed")
    repo.add_factor_source("research", recreated_source)
    recreated = _asset(repo, path="factors/momentum_renamed.py")
    assert recreated["id"] != original["id"]
    assert recreated["copied_from_asset_id"] is None


def test_same_name_copy_has_new_identity_and_exact_lineage_only(repo):
    original = _asset(repo)
    before_evidence = repo.list_factor_evidence("research", original["id"])

    result = repo.copy_factor(
        "research",
        "momentum_20d",
        "momentum_copy",
        expected_source_sha256=repo.get_project("research")["draft_source_sha256"],
    )

    copied = _asset(repo, path="factors/momentum_copy.py")
    assert result["asset"]["id"] == copied["id"]
    assert copied["id"] != original["id"]
    assert copied["factor_id"] == "momentum_copy"
    assert copied["status"] == "candidate"
    assert copied["copied_from_asset_id"] == original["id"]
    assert copied["copied_from_revision_id"] == original["current_revision_id"]
    assert repo.list_factor_evidence("research", copied["id"])["items"] == []
    assert repo.list_factor_evidence("research", original["id"]) == before_evidence
    labels = {
        item["id"]: item["label"]
        for item in result["project"]["inspection"]["entrypoints"]
        if item["kind"] == "factor"
    }
    assert labels["momentum_copy"] == labels["momentum_20d"]


def test_project_clone_records_known_source_without_hash_lineage(repo):
    original = _asset(repo)
    repo.clone_project("research", "cloned")

    cloned = _asset(repo, project_id="cloned")
    assert cloned["id"] != original["id"]
    assert cloned["copied_from_asset_id"] == original["id"]
    assert cloned["copied_from_revision_id"] == original["current_revision_id"]


def test_migration_backfills_null_delimited_lifetimes_and_is_idempotent(repo):
    path = repo.path
    repo.close()
    with sqlite3.connect(path) as db:
        current = db.execute(
            "SELECT package_revision FROM factor_source_revisions "
            "WHERE project_id = 'research' ORDER BY id DESC LIMIT 1"
        ).fetchone()[0]
        db.execute(
            "INSERT INTO factor_source_revisions(project_id, path, package_revision) "
            "VALUES ('research', 'factors/momentum_20d.py', NULL)"
        )
        db.execute(
            "INSERT INTO factor_source_revisions(project_id, path, package_revision) "
            "VALUES ('research', 'factors/momentum_20d.py', ?)",
            (current,),
        )
        packages = db.execute(
            "SELECT * FROM strategy_source_packages ORDER BY project_id, revision"
        ).fetchall()
        revisions = db.execute(
            "SELECT id, package_revision FROM factor_source_revisions "
            "WHERE project_id = 'research' ORDER BY id"
        ).fetchall()
        db.execute(
            "INSERT INTO backtests(id, config_yaml) VALUES ('frozen-run', 'frozen: true')"
        )
        backtests = db.execute("SELECT * FROM backtests ORDER BY id").fetchall()
    _legacy_schema(path)

    first = StrategyRepository(path)
    first_ids = [row["id"] for row in first.list_factor_assets("research")]
    first.close()
    second = StrategyRepository(path)
    try:
        assets = second.list_factor_assets("research")
        assert [row["id"] for row in assets] == first_ids
        assert len(assets) == 2
        assert assets[0]["current_revision_id"] is None
        assert assets[1]["current_revision_id"] == revisions[-1][0]
        assert all(row["status"] == "candidate" for row in assets)
        assert all(row["copied_from_asset_id"] is None for row in assets)
        with sqlite3.connect(path) as db:
            assert db.execute(
                "SELECT * FROM strategy_source_packages ORDER BY project_id, revision"
            ).fetchall() == packages
            assert db.execute("SELECT * FROM backtests ORDER BY id").fetchall() == backtests
            assigned = db.execute(
                "SELECT id, asset_id FROM factor_source_revisions "
                "WHERE project_id = 'research' ORDER BY id"
            ).fetchall()
        assert [row[1] for row in assigned[:2]] == [assets[0]["id"]] * 2
        assert assigned[2][1] == assets[1]["id"]
        assert second.list_factor_evidence("research", assets[0]["id"])["items"] == []
    finally:
        second.close()


def test_failed_migration_rolls_back_assets_and_marker(repo):
    path = repo.path
    repo.close()
    _legacy_schema(path)
    with sqlite3.connect(path) as db:
        db.execute(
            "CREATE TABLE factor_assets ("
            "id TEXT PRIMARY KEY, project_id TEXT NOT NULL, path TEXT NOT NULL, "
            "current_revision_id INTEGER, status TEXT NOT NULL, status_revision INTEGER NOT NULL, "
            "copied_from_asset_id TEXT, copied_from_revision_id INTEGER)"
        )
        db.execute(
            "CREATE TRIGGER fail_factor_asset_backfill BEFORE INSERT ON factor_assets "
            "BEGIN SELECT RAISE(ABORT, 'injected migration failure'); END"
        )

    with pytest.raises(sqlite3.DatabaseError, match="injected migration failure"):
        StrategyRepository(path)
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT COUNT(*) FROM factor_assets").fetchone()[0] == 0
        assert db.execute(
            "SELECT COUNT(*) FROM strategy_contract_migrations "
            "WHERE name = 'strategy-factor-assets-v1'"
        ).fetchone()[0] == 0
        db.execute("DROP TRIGGER fail_factor_asset_backfill")

    migrated = StrategyRepository(path)
    migrated.close()


def test_concurrent_legacy_schema_initialization_is_idempotent(repo):
    path = repo.path
    repo.close()
    _legacy_schema(path)
    with ThreadPoolExecutor(2) as pool:
        repositories = list(pool.map(lambda _index: StrategyRepository(path), range(2)))
    for repository in repositories:
        repository.close()
    migrated = StrategyRepository(path)
    assert len(migrated.list_factor_assets("research")) == 1
    migrated.close()


def test_unrelated_delete_and_add_do_not_share_asset_identity(repo):
    source = '@factor(id="extra")\ndef extra(context):\n    return context.history("close", window=1).iloc[-1]\n'
    repo.add_factor_source("research", source)
    original = _asset(repo, path="factors/extra.py")
    repo.replace_factor_source("research", "momentum_20d", repo.get_factor_source("research", "momentum_20d")["source"])
    repo._commit_source_units(
        "research",
        repo.get_project("research")["strategy_source"],
        [
            repo.get_factor_source("research", "momentum_20d")["source"],
            '@factor(id="unrelated")\ndef unrelated(context):\n    return context.history("close", window=1).iloc[-1]\n',
        ],
        expected_source_sha256=repo.get_project("research")["draft_source_sha256"],
    )
    remaining = _asset(repo, path="factors/unrelated.py")
    assert original["id"] != remaining["id"]
    assert repo.get_factor_asset("research", original["id"])["current_revision_id"] is None


def test_copy_changes_only_the_registered_factor_function(repo):
    source = '''@factor(id="nested")
def nested(context):
    def helper(values):
        return values
    return helper(context.history("close", window=1).iloc[-1])
'''
    repo.add_factor_source("research", source)
    result = repo.copy_factor("research", "nested", "nested_copy")
    copied_source = repo.get_factor_source("research", "nested_copy")["source"]
    assert "def nested_copy(" in copied_source
    assert "def helper(" in copied_source
    assert result["asset"]["factor_id"] == "nested_copy"


def test_copy_uses_decorator_identity_when_function_name_differs(repo):
    source = '@factor(id="public_name")\ndef python_name(context):\n    return context.history("close", window=1).iloc[-1]\n'
    repo.add_factor_source("research", source)
    result = repo.copy_factor("research", "public_name", "new_public_name")
    copied_source = repo.get_factor_source("research", "new_public_name")["source"]
    assert '@factor(id="new_public_name")' in copied_source or "@factor(id='new_public_name')" in copied_source
    assert "def new_public_name(" in copied_source
    assert result["asset"]["factor_id"] == "new_public_name"


def test_copy_supports_implicit_decorator_identity(repo):
    source = '@factor\ndef implicit_name(context):\n    return context.history("close", window=1).iloc[-1]\n'
    repo.add_factor_source("research", source)
    result = repo.copy_factor("research", "implicit_name", "implicit_copy")
    copied_source = repo.get_factor_source("research", "implicit_copy")["source"]
    assert "def implicit_copy(" in copied_source
    assert result["asset"]["factor_id"] == "implicit_copy"
