from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from alphalab import PipelineRepository, STAGE_ENTRYPOINTS, STAGE_NAMES, validate_python_source
from alphalab.pipeline import legacy_migration
from alphalab.strategy.python_runtime import python_source_sha256


def test_repository_seeds_three_stage_python_presets(tmp_path):
    repository = PipelineRepository(tmp_path / "pipeline.db")
    try:
        project = repository.get_project("three-stage-default")
        assert project is not None
        assert project["built_in"] is True
        assert project["editable"] is False
        assert tuple(project["components"]) == STAGE_NAMES
        assert [item["stage"] for item in project["component_manifest"]] == list(STAGE_NAMES)
        assert len(project["component_manifest"]) == 3
        assert "def run_strategy(context):" in project["composed_source"]
        assert project["source_sha256"]

        for item in project["component_manifest"]:
            detail = repository.get_component(item["component_id"], item["version"])
            assert detail is not None
            assert detail["entrypoint"] == STAGE_ENTRYPOINTS[item["stage"]]
            assert validate_python_source(detail["source"], detail["entrypoint"])["valid"]
    finally:
        repository.close()


def test_components_and_projects_create_immutable_versions(tmp_path):
    database = tmp_path / "pipeline.db"
    repository = PipelineRepository(database)
    try:
        with pytest.raises(PermissionError, match="immutable"):
            repository.save_component_version(
                "selection-factor-top",
                source="def select_assets(context):\n    return {'selected': [], 'scores': {}}\n",
            )
        with pytest.raises(PermissionError, match="immutable"):
            default = repository.get_project("three-stage-default")
            assert default is not None
            repository.update_project(
                "three-stage-default",
                name=default["name"],
                description=default["description"],
                components=default["components"],
                settings=default["settings"],
            )

        custom = repository.clone_component(
            "selection-factor-top", "my-selection", "My Selection"
        )
        first_hash = custom["source_sha256"]
        second = repository.save_component_version(
            "my-selection",
            source=custom["source"] + "\n# version 2\n",
            parameters={"count": 5},
            notes="immutable revision",
        )
        assert second["version"] == 2
        assert second["source_sha256"] != first_hash
        assert repository.get_component("my-selection", 1)["source_sha256"] == first_hash

        project = repository.clone_project("three-stage-default", "my-project", "My Project")
        original_hash = project["source_sha256"]
        project["components"]["selection"] = {
            "component_id": "my-selection",
            "version": 2,
        }
        revised = repository.update_project(
            "my-project",
            name=project["name"],
            description=project["description"],
            components=project["components"],
            settings={**project["settings"], "note": "revision 2"},
        )
        assert revised["revision"] == 2
        assert revised["source_sha256"] != original_hash
        assert revised["components"]["selection"] == {
            "component_id": "my-selection",
            "version": 2,
        }
        with pytest.raises(ValueError, match="used by projects"):
            repository.delete_component("my-selection")
    finally:
        repository.close()

    connection = sqlite3.connect(database)
    try:
        snapshots = connection.execute(
            "SELECT revision, source_sha256, composed_source FROM pipeline_project_versions "
            "WHERE project_id = 'my-project' ORDER BY revision"
        ).fetchall()
    finally:
        connection.close()
    assert [row[0] for row in snapshots] == [1, 2]
    assert snapshots[0][1] == original_hash
    assert snapshots[1][1] == revised["source_sha256"]
    assert all("def run_strategy(context):" in row[2] for row in snapshots)


def test_project_requires_exactly_three_stage_references(tmp_path):
    repository = PipelineRepository(tmp_path / "pipeline.db")
    try:
        project = repository.get_project("three-stage-default")
        assert project is not None
        missing = dict(project["components"])
        missing.pop("portfolio")
        with pytest.raises(ValueError, match="exactly three stages"):
            repository.create_project(
                "incomplete-project",
                name="Incomplete",
                description="",
                components=missing,
                settings=project["settings"],
            )
    finally:
        repository.close()


def test_old_execution_entrypoint_is_migrated_once(tmp_path):
    database = tmp_path / "pipeline.db"
    repository = PipelineRepository(database)
    repository.close()

    connection = sqlite3.connect(database)
    try:
        source = connection.execute(
            "SELECT source FROM pipeline_component_versions "
            "WHERE component_id = 'execution-monthly' AND version = 1"
        ).fetchone()[0]
        old_source = source.replace("def configure_execution(", "def create_orders(", 1)
        connection.execute(
            """UPDATE pipeline_component_versions
               SET entrypoint = 'create_orders', source = ?, source_sha256 = ?
               WHERE component_id = 'execution-monthly' AND version = 1""",
            (old_source, python_source_sha256(old_source)),
        )
        connection.execute(
            "DELETE FROM pipeline_contract_migrations "
            "WHERE name = 'execution-entrypoint-configure-v1'"
        )
        connection.commit()
    finally:
        connection.close()

    migrated = PipelineRepository(database)
    try:
        execution = migrated.get_component("execution-monthly", 1)
        assert execution is not None
        assert execution["entrypoint"] == "configure_execution"
        assert "def configure_execution(context):" in execution["source"]
        assert "create_orders" not in execution["source"]
    finally:
        migrated.close()


def test_six_stage_custom_project_is_migrated_once_and_history_is_preserved(tmp_path):
    database = tmp_path / "pipeline.db"
    schema = (
        (Path(__file__).resolve().parents[2] / "alphalab" / "schema.sql")
        .read_text(encoding="utf-8")
        .replace(
            "'selection', 'portfolio', 'execution'",
            "'universe', 'selection', 'timing', 'portfolio', 'risk', 'execution'",
        )
    )
    sources = {
        "legacy-universe": ("universe", "build_universe", "def build_universe(context):\n    return {'symbols': []}\n"),
        "legacy-selection": ("selection", "select_assets", "def select_assets(context):\n    return {'selected': [], 'scores': {}}\n"),
        "legacy-timing": ("timing", "compute_exposure", "def compute_exposure(context):\n    return {'exposure': 0.5}\n"),
        "legacy-portfolio": (
            "portfolio",
            "construct_portfolio",
            "def construct_portfolio(context):\n"
            "    selected = context['selection']['selected']\n"
            "    weight = context['timing']['exposure'] / len(selected) if selected else 0.0\n"
            "    return {'weights': {symbol: weight for symbol in selected}}\n",
        ),
        "legacy-risk": (
            "risk",
            "apply_risk",
            "def apply_risk(context):\n"
            "    weights = dict(context['portfolio']['weights'])\n"
            "    return {'weights': weights, 'gross_exposure': sum(weights.values())}\n",
        ),
        "legacy-execution": (
            "execution",
            "configure_execution",
            "def configure_execution(context):\n"
            "    return {'execution': {'rebalance_freq': 'monthly'}}\n",
        ),
    }
    refs = {
        stage: {"component_id": component_id, "version": 1}
        for component_id, (stage, _entrypoint, _source) in sources.items()
    }
    settings = {
        "stage_parameters": {
            "selection": {"count": 12},
            "timing": {"threshold": 0.0},
            "portfolio": {"method": "equal_weight"},
            "risk": {"max_weight": 0.08},
        }
    }
    connection = sqlite3.connect(database)
    try:
        connection.executescript(schema)
        for component_id, (stage, entrypoint, source) in sources.items():
            connection.execute(
                "INSERT INTO pipeline_components (id, stage, name) VALUES (?, ?, ?)",
                (component_id, stage, component_id),
            )
            connection.execute(
                """INSERT INTO pipeline_component_versions
                   (component_id, version, entrypoint, source, source_sha256, parameters_json)
                   VALUES (?, 1, ?, ?, ?, '{}')""",
                (component_id, entrypoint, source, python_source_sha256(source)),
            )
        connection.execute(
            """INSERT INTO pipeline_projects
               (id, name, description, revision, component_refs_json, settings_json)
               VALUES ('legacy-custom', 'Legacy Custom', '', 1, ?, ?)""",
            (json.dumps(refs), json.dumps(settings)),
        )
        connection.execute(
            """INSERT INTO pipeline_project_versions
               (project_id, revision, component_refs_json, settings_json,
                composed_source, source_sha256)
               VALUES ('legacy-custom', 1, ?, ?, 'legacy-six-stage-frozen', 'legacy-hash')""",
            (json.dumps(refs), json.dumps(settings)),
        )
        connection.commit()
    finally:
        connection.close()

    repository = PipelineRepository(database)
    try:
        project = repository.get_project("legacy-custom")
        assert project is not None
        assert project["revision"] == 3
        assert tuple(project["components"]) == STAGE_NAMES
        assert [item["stage"] for item in project["component_manifest"]] == list(STAGE_NAMES)
        assert project["settings"]["stage_parameters"] == {}
        assert project["settings"]["pipeline_migration"]["removed_universe_component"] == {
            "component_id": "legacy-universe",
            "version": 1,
        }
        assert project["settings"]["pipeline_migration"]["previous"]["removed_timing_component"] == {
            "component_id": "legacy-timing",
            "version": 1,
        }
        assert "removed_by_migration" in project["composed_source"]
        assert "_migrated_stock_pool_build_universe" in project["composed_source"]
        assert "universe" not in project["components"]
        assert repository.get_component("legacy-universe") is None
        assert repository.get_component("legacy-timing") is None
        assert repository.get_component("legacy-risk") is None
        assert {item["stage"] for item in repository.list_components()} == set(STAGE_NAMES)
    finally:
        repository.close()

    reopened = PipelineRepository(database)
    try:
        assert reopened.get_project("legacy-custom")["revision"] == 3
    finally:
        reopened.close()
    connection = sqlite3.connect(database)
    try:
        versions = connection.execute(
            """SELECT revision, composed_source FROM pipeline_project_versions
               WHERE project_id = 'legacy-custom' ORDER BY revision"""
        ).fetchall()
    finally:
        connection.close()
    assert [row[0] for row in versions] == [1, 3]
    assert versions[0][1] == "legacy-six-stage-frozen"


def test_legacy_local_yaml_is_imported_only_once(tmp_path, monkeypatch):
    runtime_root = tmp_path / "runtime"
    strategy_dir = runtime_root / "strategies"
    strategy_dir.mkdir(parents=True)
    legacy = strategy_dir / "old-value.yaml"
    legacy.write_text(
        "name: Old Value\n"
        "strategy_type: stock_selection\n"
        "selection:\n  n_stocks: 7\n"
        "portfolio:\n  max_weight: 0.2\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(legacy_migration, "RUNTIME_APP_DIR", runtime_root)
    database = tmp_path / "pipeline.db"

    first = PipelineRepository(database)
    try:
        imported = first.get_project("old-value")
        assert imported is not None
        assert imported["settings"]["stage_parameters"]["selection"]["count"] == 7
        assert imported["settings"]["stage_parameters"]["portfolio"]["max_weight"] == 0.2
    finally:
        first.close()

    legacy.write_text("name: Changed Later\n", encoding="utf-8")
    second = PipelineRepository(database)
    try:
        projects = [item for item in second.list_projects() if item["id"].startswith("old-value")]
        assert len(projects) == 1
        assert projects[0]["name"] == "Old Value"
    finally:
        second.close()
