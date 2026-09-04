from __future__ import annotations

from types import SimpleNamespace

from fastapi.testclient import TestClient

from alphalab import ResultStore
from alphalab.dataio.recipes import (
    _previous_raw_close_builtin_recipe,
    render_builtin_recipe,
)
from alphalab.dataio.runtime import OperationsStore
from alphalab.strategy.repository import StrategyRepository
from alphalab.strategy.source import registered_function_source, replace_registered_function
from alphalab.validation.repository import ValidationRepository
from dashboard.backend.main import _public_error_value, app
from dashboard.backend.routers import backtests as backtests_router
from dashboard.backend.services import (
    backtest_analytics_service,
    data_sync_service,
    result_service,
    strategy_service,
)


def test_data_and_strategy_sdk_read_contracts(tmp_path, monkeypatch):
    database = tmp_path / "strategy.db"
    monkeypatch.setattr(strategy_service, "repository", lambda: StrategyRepository(database))
    client = TestClient(app)
    assert client.get("/").json()["status"] == "ok"
    providers = client.get("/api/data/providers")
    assert providers.status_code == 200
    assert providers.json()["active_profile"] == "runtime"
    assert set(providers.json()["profiles"]) == {"runtime"}
    assert client.get("/api/data/manifest").status_code == 404
    assert client.get("/api/strategy/fields", params={"profile": "demo"}).status_code == 422
    assert client.get("/api/strategy/project-templates").status_code == 404
    projects = client.get("/api/strategy/projects")
    assert projects.status_code == 200
    assert projects.json()[0]["id"] == "sdk-v1-default"
    project = client.get("/api/strategy/projects/sdk-v1-default").json()
    assert project["current_package"]["sdk_version"] == 1
    assert project["inspection"]["source_sha256"] == project["draft_source_sha256"]
    assert "@factor" in project["draft_source"]
    assert "@factor" not in project["strategy_source"]
    assert {item["path"] for item in project["source_units"]} == {
        "strategy.py",
        "factors/momentum_20d.py",
    }
    entrypoint = client.get(
        "/api/strategy/projects/sdk-v1-default/entrypoints/monthly_momentum/source"
    )
    assert entrypoint.status_code == 200, entrypoint.text
    assert entrypoint.json()["source_sha256"] == project["draft_source_sha256"]
    assert entrypoint.json()["source"].startswith("@signal(")


def test_project_create_atomically_saves_strategy_and_factors(tmp_path, monkeypatch):
    database = tmp_path / "atomic-project.db"
    operations = OperationsStore(tmp_path)
    monkeypatch.setattr(strategy_service, "repository", lambda: StrategyRepository(database))
    monkeypatch.setattr(strategy_service, "operations_store", lambda: operations)
    monkeypatch.setattr(
        strategy_service,
        "_project_recipe_bounds",
        lambda: {"start": "2020-01-01", "end": "2025-12-31"},
    )
    client = TestClient(app)

    created = client.post(
        "/api/strategy/projects",
        json={
            "project_id": "new-agent-project",
            "name": "New Agent Project",
            "data_requirements": {"fundamentals": ["roe"]},
            "factors": [
                {
                    "template_id": "custom_factor",
                    "factor_id": "quality",
                    "label": "Quality",
                    "body": 'return context.fundamental("roe")',
                }
            ],
            "recipe_parameters": {"start": "2021-01-01", "end": "2025-06-30"},
            "validation_parameter_edits": [
                {
                    "entrypoint_id": "performance",
                    "parameter": "risk_free_rate",
                    "value": 0.02,
                }
            ],
            "function_replacements": [
                {
                    "entrypoint_id": "monthly_momentum",
                    "function_source": """@signal(
    id="monthly_momentum",
    label="月末动量 Top N",
    schedule=Monthly.last_trading_day(at="close"),
)
def monthly_momentum(context, state, *, top_n: int = 7):
    scores = context.factor("quality").dropna()
    return SignalResult(
        selected=list(scores.nlargest(top_n).index), scores=scores, state=state
    )
""",
                }
            ],
            "confirm_save": True,
            "confirm_python_execution": True,
        },
    )

    assert created.status_code == 201, created.text
    project = created.json()
    assert project["id"] == "new-agent-project"
    assert project["current_revision"] == 1
    assert project["dirty"] is False
    assert {item["path"] for item in project["source_units"]} == {
        "strategy.py",
        "factors/quality.py",
    }
    assert project["inspection"]["data_requirements"]["fundamentals"] == ["roe"]
    assert "top_n: int = 7" in project["strategy_source"]
    assert '@execution_data_fill(id="fill_missing_market_state"' in project["strategy_source"]
    assert 'eq("CS")' in project["strategy_source"]
    recipe = operations.get_recipe_draft("new-agent-project")
    assert recipe is not None
    assert recipe["selected_template_id"] == "rq.a_share_research"
    assert "start: str = '2021-01-01'" in recipe["source"]
    validation_repository = ValidationRepository(database)
    try:
        validation = validation_repository.get_or_create("new-agent-project")
    finally:
        validation_repository.close()
    assert validation["current_revision"] == 1
    assert "risk_free_rate: float = 0.02" in validation["source"]

    arbitrary_source = client.post(
        "/api/strategy/projects",
        json={
            "project_id": "arbitrary-agent-project",
            "name": "Arbitrary Agent Project",
            "strategy_source": project["strategy_source"],
            "confirm_save": True,
            "confirm_python_execution": True,
        },
    )
    assert arbitrary_source.status_code == 422

    arbitrary_factor = client.post(
        "/api/strategy/projects",
        json={
            "project_id": "arbitrary-factor-project",
            "name": "Arbitrary Factor Project",
            "factor_sources": [
                '@factor(id="quality")\ndef quality(context):\n    return 1.0\n'
            ],
            "confirm_save": True,
            "confirm_python_execution": True,
        },
    )
    assert arbitrary_factor.status_code == 422


def test_project_create_removes_strategy_when_recipe_persistence_fails(
    tmp_path, monkeypatch
):
    database = tmp_path / "failed-project-bundle.db"
    operations = OperationsStore(tmp_path)
    monkeypatch.setattr(strategy_service, "repository", lambda: StrategyRepository(database))
    monkeypatch.setattr(strategy_service, "operations_store", lambda: operations)
    monkeypatch.setattr(
        strategy_service,
        "_project_recipe_bounds",
        lambda: {"start": "2020-01-01", "end": "2025-12-31"},
    )

    def fail_save(*_args, **_kwargs):
        raise RuntimeError("recipe persistence failed")

    monkeypatch.setattr(operations, "save_recipe_draft", fail_save)
    response = TestClient(app).post(
        "/api/strategy/projects",
        json={
            "project_id": "failed-project-bundle",
            "name": "Failed Project Bundle",
            "confirm_save": True,
            "confirm_python_execution": True,
        },
    )

    assert response.status_code >= 400
    repository = StrategyRepository(database)
    try:
        assert repository.get_project("failed-project-bundle") is None
    finally:
        repository.close()
    assert operations.get_recipe_draft("failed-project-bundle") is None


def test_project_create_migrates_the_default_recipe_before_copying_it(
    tmp_path, monkeypatch
) -> None:
    database = tmp_path / "migrated-default-recipe.db"
    operations = OperationsStore(tmp_path)
    repository = StrategyRepository(database)
    try:
        repository.get_project("sdk-v1-default")
    finally:
        repository.close()
    legacy = _previous_raw_close_builtin_recipe(
        "rq.a_share_research",
        start="2021-01-01",
        end="2025-12-31",
        symbols=None,
    )
    operations.save_recipe_draft(
        "sdk-v1-default",
        legacy,
        selected_template_id="rq.a_share_research",
    )
    monkeypatch.setattr(strategy_service, "repository", lambda: StrategyRepository(database))
    monkeypatch.setattr(strategy_service, "operations_store", lambda: operations)

    response = TestClient(app).post(
        "/api/strategy/projects",
        json={
            "project_id": "etf-from-migrated-default",
            "name": "ETF from migrated default",
            "recipe_parameters": {"symbols": ["510300.SH"]},
            "confirm_save": True,
            "confirm_python_execution": True,
        },
    )

    assert response.status_code == 201, response.text
    default_recipe = operations.get_recipe_draft("sdk-v1-default")
    project_recipe = operations.get_recipe_draft("etf-from-migrated-default")
    assert default_recipe is not None
    assert project_recipe is not None
    assert "required_columns=(\"raw_open\", \"raw_high\", \"raw_low\", \"raw_close\")" in (
        default_recipe["source"]
    )
    assert "EXPLICIT_SYMBOL_ASSET_TYPES = ('CS', 'ETF')" in project_recipe["source"]
    assert "symbols: tuple[str, ...] | None = ('510300.SH',)" in project_recipe["source"]


def test_explicit_default_project_migration_creates_a_new_revision(
    tmp_path, monkeypatch
):
    database = tmp_path / "project-template-migration.db"
    repository = StrategyRepository(database)
    try:
        project = repository.clone_project("sdk-v1-default", "legacy-stock-project")
        source = project["draft_source"]
        fill = registered_function_source(source, entrypoint_id="fill_missing_market_state")
        source = source.replace(fill, "", 1)
        source, _ = replace_registered_function(
            source,
            entrypoint_id="research_universe",
            function_source='''@universe(id="legacy_universe")
def legacy_universe(context):
    return UniverseResult(symbols=context.universe)
''',
        )
        legacy = repository.update_draft(
            project["id"],
            source,
            expected_source_sha256=project["draft_source_sha256"],
        )
        old_revision = legacy["current_revision"]
        old_hash = repository.get_package(project["id"], old_revision)["source_sha256"]
    finally:
        repository.close()

    monkeypatch.setattr(strategy_service, "repository", lambda: StrategyRepository(database))
    response = TestClient(app).post(
        "/api/strategy/projects/legacy-stock-project/default-migration",
        json={
            "expected_source_sha256": legacy["draft_source_sha256"],
            "confirm_write": True,
            "confirm_python_execution": True,
        },
    )

    assert response.status_code == 200, response.text
    migrated = response.json()
    assert migrated["current_revision"] == old_revision + 1
    assert any(
        item["kind"] == "execution_data_fill"
        for item in migrated["inspection"]["entrypoints"]
    )
    assert 'eq("CS")' in migrated["strategy_source"]
    repository = StrategyRepository(database)
    try:
        assert repository.get_package(
            "legacy-stock-project", old_revision
        )["source_sha256"] == old_hash
    finally:
        repository.close()


def test_backtest_submission_is_runtime_only_and_returns_a_job(monkeypatch):
    client = TestClient(app)
    rejected_profile = client.post(
        "/api/backtests/jobs",
        json={
            "project_id": "project",
            "start_date": "2024-01-01",
            "end_date": "2024-12-31",
            "profile": "demo",
            "confirm_python_execution": True,
        },
    )
    assert rejected_profile.status_code == 422

    def submit(_request):
        return {"status": "queued", "id": "job-1"}

    monkeypatch.setattr(backtests_router, "submit_backtest_job", submit)
    accepted = client.post(
        "/api/backtests/jobs",
        json={
            "project_id": "project",
            "start_date": "2024-01-01",
            "end_date": "2024-12-31",
            "profile": "runtime",
            "confirm_python_execution": True,
        },
    )
    assert accepted.status_code == 202
    assert accepted.json() == {"status": "queued", "id": "job-1"}


def test_backtest_wait_endpoint_uses_a_bounded_server_wait(monkeypatch):
    observed = []

    def wait(job_id, timeout_seconds):
        observed.append((job_id, timeout_seconds))
        return {
            "status": "running",
            "id": job_id,
            "warnings": [],
            "research_valid": None,
            "attempted_trade_count": 0,
            "successful_trade_count": 0,
            "execution_data_fill_count": 0,
            "market_state_rejection_count": 0,
        }

    monkeypatch.setattr(backtests_router, "wait_backtest_job", wait)
    response = TestClient(app).get(
        "/api/backtests/jobs/job-1/wait?timeout_seconds=120"
    )

    assert response.status_code == 200
    assert response.json()["status"] == "running"
    assert observed == [("job-1", 30.0)]


def test_public_http_errors_remove_runtime_internals():
    value = _public_error_value(
        {
            "code": "FAILED",
            "message": "C:\\service\\release\\worker.py failed\nprivate stack",
            "traceback": "private traceback",
            "environment_sha256": "a" * 64,
        }
    )

    assert value == {"code": "FAILED", "message": "<internal-path> failed"}


def test_public_sync_job_is_status_only_and_sanitized():
    value = data_sync_service._public_job(
        {
            "id": "sync-1",
            "status": "failed",
            "error": "/srv/releases/worker.py failed\nprivate traceback",
            "request": {
                "project_id": "project",
                "symbols": ["A", "B"],
                "recipe_source": "secret source",
                "recipe_stdout": "secret output",
            },
        }
    )

    assert list(value)[:4] == ["status", "id", "error_code", "error_summary"]
    assert value["error_code"] == "DATA_SYNC_FAILED"
    assert value["error_summary"] == "<internal-path> failed"
    assert value["log_reference"] == "data-sync:sync-1"
    assert value["request"] == {
        "project_id": "project",
        "symbol_count": 2,
        "symbols_sample": ["A", "B"],
    }


def test_sdk_documentation_uses_one_versioned_guide() -> None:
    client = TestClient(app)

    catalog = client.get("/api/sdk-docs")
    assert catalog.status_code == 200
    assert catalog.json() == {
        "sdk_version": 1,
        "document": "docs/06_ALPHALAB_SDK_GUIDE.md",
        "topics": [
            {"id": "overview", "title": "SDK 总览"},
            {"id": "factor", "title": "因子 SDK"},
            {"id": "strategy", "title": "策略 SDK"},
            {"id": "data", "title": "数据配方 SDK"},
            {"id": "validation", "title": "验证 SDK"},
            {"id": "report", "title": "报告结果协议"},
        ],
    }

    factor = client.get("/api/sdk-docs/factor")
    assert factor.status_code == 200
    assert factor.json()["title"] == "因子 SDK"
    assert "context.history" in factor.json()["markdown"]
    assert "alphalab-sdk-topic" not in factor.json()["markdown"]
    assert client.get("/api/sdk-docs/unknown").status_code == 404


def test_data_sync_templates_expose_rq_and_python_sdk_contracts() -> None:
    response = TestClient(app).get("/api/data-sync/templates")

    assert response.status_code == 200
    payload = response.json()
    assert {item["id"] for item in payload["templates"]} == {
        "rq.a_share_daily",
        "rq.etf_daily",
        "rq.exchange_fund_daily",
        "rq.a_share_research",
    }
    assert payload["python_sdk"]["import"] == "alphalab.data_sdk.v1"


def test_data_sync_connection_probe_uses_selected_template(monkeypatch) -> None:
    class FakeRQ:
        def get_latest_trading_date(self, **kwargs):
            assert kwargs == {"market": "cn"}
            return "2026-08-24"

    class FakeClient:
        def connect(self):
            return FakeRQ()

    monkeypatch.setattr(
        data_sync_service.RQDataClient,
        "from_env",
        lambda: FakeClient(),
    )
    monkeypatch.setattr(data_sync_service, "version", lambda _name: "3.6.3")

    response = TestClient(app).post(
        "/api/data-sync/connection-test",
        json={"template_id": "rq.etf_daily"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "status": "connected",
        "connected": True,
        "template_id": "rq.etf_daily",
        "market": "cn",
        "instrument_types": ["ETF"],
        "latest_trading_date": "2026-08-24",
        "rqdatac_version": "3.6.3",
    }

    default_response = TestClient(app).post(
        "/api/data-sync/connection-test",
        json={},
    )
    assert default_response.status_code == 200
    assert default_response.json()["template_id"] == "rq.a_share_research"


def test_data_recipe_workspace_uses_python_as_the_template_and_parameter_source(
    tmp_path,
    monkeypatch,
) -> None:
    manager = SimpleNamespace(operations=OperationsStore(tmp_path))
    monkeypatch.setattr(data_sync_service, "get_job_manager", lambda: manager)
    monkeypatch.setattr(
        data_sync_service,
        "_recipe_bounds",
        lambda: {"start": "2021-08-25", "end": "2026-08-25"},
    )
    client = TestClient(app)

    workspace = client.get("/api/data-sync/recipes/sdk-v1-default")
    assert workspace.status_code == 200, workspace.text
    payload = workspace.json()
    assert payload["docs_url"] == ("https://www.ricequant.com/doc/rqdata/python/index-rqdatac")
    assert payload["draft"]["inspection"]["matched_template_id"] == "rq.a_share_research"
    assert payload["draft"]["selected_template_id"] == "rq.a_share_research"
    assert "@data_recipe" in payload["draft"]["source"]
    assert "@universe" not in payload["draft"]["source"]

    applied = client.post(
        "/api/data-sync/recipes/sdk-v1-default/templates/rq.etf_daily",
        json={
            "expected_source_sha256": payload["draft"]["source_sha256"],
            "confirm_write": True,
        },
    )
    assert applied.status_code == 200, applied.text
    assert "template='rq.etf_daily'" in applied.json()["source"]
    assert "rq.all_instruments(" in applied.json()["source"]
    assert "rq.get_price(" in applied.json()["source"]
    assert "RQSyncRequest" not in applied.json()["source"]
    assert applied.json()["selected_template_id"] == "rq.etf_daily"

    projected = client.patch(
        "/api/data-sync/recipes/sdk-v1-default/parameters",
        json={
            "start": "2022-01-01",
            "end": "2026-08-25",
            "symbols": ["510300.XSHG"],
            "expected_source_sha256": applied.json()["source_sha256"],
            "confirm_write": True,
        },
    )
    assert projected.status_code == 200, projected.text
    source = projected.json()["source"]
    assert "start: str = '2022-01-01'" in source
    assert "symbols: tuple[str, ...] | None = ('510300.XSHG',)" in source
    assert projected.json()["selected_template_id"] == "rq.etf_daily"

    custom = client.post(
        "/api/data-sync/recipes/sdk-v1-default/templates",
        json={"name": "ETF custom", "description": "Saved Python", "confirm_save": True},
    )
    assert custom.status_code == 200, custom.text
    assert custom.json()["kind"] == "custom"
    custom_id = custom.json()["id"]
    assert custom.json()["draft"]["selected_template_id"] == custom_id

    refreshed = client.get("/api/data-sync/recipes/sdk-v1-default")
    assert refreshed.status_code == 200, refreshed.text
    assert refreshed.json()["draft"]["selected_template_id"] == custom_id

    reset_to_builtin = client.post(
        "/api/data-sync/recipes/sdk-v1-default/templates/rq.a_share_daily",
        json={
            "expected_source_sha256": refreshed.json()["draft"]["source_sha256"],
            "confirm_write": True,
        },
    )
    assert reset_to_builtin.status_code == 200, reset_to_builtin.text
    assert reset_to_builtin.json()["selected_template_id"] == "rq.a_share_daily"

    switched_back = client.post(
        f"/api/data-sync/recipes/sdk-v1-default/templates/{custom_id}",
        json={
            "expected_source_sha256": reset_to_builtin.json()["source_sha256"],
            "confirm_write": True,
        },
    )
    assert switched_back.status_code == 200, switched_back.text
    assert switched_back.json()["selected_template_id"] == custom_id
    assert switched_back.json()["source_sha256"] == custom.json()["source_sha256"]


def test_system_default_recipe_upgrades_only_an_untouched_previous_default(
    tmp_path,
    monkeypatch,
) -> None:
    operations = OperationsStore(tmp_path)
    old_source = render_builtin_recipe(
        "rq.a_share_daily",
        start="2021-08-25",
        end="2026-08-25",
    )
    operations.save_recipe_draft(
        "sdk-v1-default",
        old_source,
        selected_template_id="rq.a_share_daily",
    )
    operations.save_recipe_draft(
        "custom-project",
        old_source,
        selected_template_id="rq.a_share_daily",
    )
    manager = SimpleNamespace(operations=operations)
    monkeypatch.setattr(data_sync_service, "get_job_manager", lambda: manager)
    monkeypatch.setattr(strategy_service, "get_project", lambda project_id: {"id": project_id})
    monkeypatch.setattr(
        data_sync_service,
        "_recipe_bounds",
        lambda: {"start": "2021-08-25", "end": "2026-08-25"},
    )

    upgraded = data_sync_service.recipe_workspace("sdk-v1-default")["draft"]
    preserved = data_sync_service.recipe_workspace("custom-project")["draft"]

    assert upgraded["selected_template_id"] == "rq.a_share_research"
    assert upgraded["inspection"]["matched_template_id"] == "rq.a_share_research"
    assert preserved["selected_template_id"] == "rq.a_share_daily"
    assert preserved["inspection"]["matched_template_id"] == "rq.a_share_daily"


def test_strategy_create_cst_edit_and_revision_confirmation(tmp_path, monkeypatch):
    database = tmp_path / "strategy.db"
    monkeypatch.setattr(strategy_service, "repository", lambda: StrategyRepository(database))
    monkeypatch.setattr(strategy_service, "operations_store", lambda: OperationsStore(tmp_path))
    client = TestClient(app)
    denied = client.post(
        "/api/strategy/projects",
        json={
            "project_id": "custom",
            "name": "Custom",
            "confirm_save": True,
            "confirm_python_execution": False,
        },
    )
    assert denied.status_code == 409
    created = client.post(
        "/api/strategy/projects",
        json={
            "project_id": "custom",
            "name": "Custom",
            "confirm_save": True,
            "confirm_python_execution": True,
        },
    )
    assert created.status_code == 201, created.text
    original_hash = created.json()["draft_source_sha256"]
    edited = client.post(
        "/api/strategy/projects/custom/edits",
        json={
            "operation": "parameter",
            "entrypoint_id": "momentum_20d",
            "parameter": "window",
            "value": 30,
            "expected_source_sha256": original_hash,
            "confirm_write": True,
            "confirm_python_execution": True,
        },
    )
    assert edited.status_code == 200, edited.text
    edited_source = edited.json()["project"]["draft_source"]
    assert 'Parameter(label="窗口", minimum=2, maximum=500, step=1)' in edited_source
    assert "] = 30" in edited_source
    assert edited.json()["project"]["draft_source_sha256"] != original_hash
    blended = client.post(
        "/api/strategy/projects/custom/edits",
        json={
            "operation": "factor_blend",
            "entrypoint_id": "monthly_momentum",
            "factor_weights": {"momentum_20d": 1.0},
            "normalization": "rank",
            "expected_source_sha256": edited.json()["project"]["draft_source_sha256"],
            "confirm_write": True,
            "confirm_python_execution": True,
        },
    )
    assert blended.status_code == 200, blended.text
    assert "context.combine_factors(" in blended.json()["project"]["draft_source"]
    saved = client.post(
        "/api/strategy/projects/custom/revisions",
        json={
            "expected_source_sha256": blended.json()["project"]["draft_source_sha256"],
            "confirm_save": True,
            "confirm_python_execution": True,
        },
    )
    assert saved.status_code == 201, saved.text
    assert saved.json()["revision"] == 3


def test_factor_template_catalog_and_install_use_the_strategy_draft(tmp_path, monkeypatch):
    database = tmp_path / "factor-templates.db"
    monkeypatch.setattr(strategy_service, "repository", lambda: StrategyRepository(database))
    monkeypatch.setattr(strategy_service, "operations_store", lambda: OperationsStore(tmp_path))
    client = TestClient(app)

    catalog = client.get("/api/strategy/factor-templates")
    assert catalog.status_code == 200, catalog.text
    templates = catalog.json()["templates"]
    assert len(templates) == 19
    assert {item["id"] for item in templates} >= {
        "liquidity_20d",
        "custom_factor",
        "momentum_60d",
        "range_volatility_20d",
        "roe",
        "rsi_14",
    }
    assert all(item["source"].startswith("@factor(") for item in templates)

    created = client.post(
        "/api/strategy/projects",
        json={
            "project_id": "template-project",
            "name": "Template Project",
            "confirm_save": True,
            "confirm_python_execution": True,
        },
    )
    assert created.status_code == 201, created.text
    assert "@factor" not in created.json()["strategy_source"]
    source_hash = created.json()["draft_source_sha256"]
    installed = client.post(
        "/api/strategy/projects/template-project/factor-templates/momentum_60d",
        json={
            "expected_source_sha256": source_hash,
            "confirm_write": True,
            "confirm_python_execution": True,
        },
    )
    assert installed.status_code == 200, installed.text
    assert installed.json()["factor"]["id"] == "momentum_60d"
    project = installed.json()["project"]
    assert "def momentum_60d(context" in project["draft_source"]
    assert "@factor" not in project["strategy_source"]
    assert {item["path"] for item in project["source_units"]} >= {
        "strategy.py",
        "factors/momentum_20d.py",
        "factors/momentum_60d.py",
    }
    assert {item["id"] for item in project["inspection"]["entrypoints"]} >= {
        "momentum_20d",
        "momentum_60d",
    }
    assert project["current_revision"] == 2
    assert project["dirty"] is False

    blended = client.post(
        "/api/strategy/projects/template-project/edits",
        json={
            "operation": "factor_blend",
            "entrypoint_id": "monthly_momentum",
            "factor_weights": {"momentum_20d": 0.6, "momentum_60d": 0.4},
            "normalization": "rank",
            "expected_source_sha256": project["draft_source_sha256"],
            "confirm_write": True,
            "confirm_python_execution": True,
        },
    )
    assert blended.status_code == 200, blended.text
    project = blended.json()["project"]
    assert "'momentum_60d': 0.4" in project["draft_source"]

    duplicate = client.post(
        "/api/strategy/projects/template-project/factor-templates/momentum_60d",
        json={
            "expected_source_sha256": project["draft_source_sha256"],
            "confirm_write": True,
            "confirm_python_execution": True,
        },
    )
    assert duplicate.status_code == 200, duplicate.text
    assert duplicate.json()["factor"]["id"] == "momentum_60d_2"
    assert duplicate.json()["factor"]["function"] == "momentum_60d_2"
    assert duplicate.json()["factor"]["label"] == "60 日动量（副本 2）"
    project = duplicate.json()["project"]
    assert "def momentum_60d_2(context" in project["draft_source"]
    assert "factors/momentum_60d_2.py" in {item["path"] for item in project["source_units"]}

    denied = client.post(
        "/api/strategy/projects/template-project/factor-templates/roe",
        json={
            "expected_source_sha256": project["draft_source_sha256"],
            "confirm_write": False,
            "confirm_python_execution": True,
        },
    )
    assert denied.status_code == 409

    saved = client.post(
        "/api/strategy/projects/template-project/revisions",
        json={
            "expected_source_sha256": project["draft_source_sha256"],
            "confirm_save": True,
            "confirm_python_execution": True,
        },
    )
    assert saved.status_code == 201, saved.text
    assert saved.json()["revision"] == 4


def test_strategy_and_factor_source_routes_keep_authoring_files_separate(tmp_path, monkeypatch):
    database = tmp_path / "separate-source-routes.db"
    monkeypatch.setattr(strategy_service, "repository", lambda: StrategyRepository(database))
    monkeypatch.setattr(strategy_service, "operations_store", lambda: OperationsStore(tmp_path))
    client = TestClient(app)
    created = client.post(
        "/api/strategy/projects",
        json={
            "project_id": "separate-source-routes",
            "name": "Separate Source Routes",
            "confirm_save": True,
            "confirm_python_execution": True,
        },
    )
    assert created.status_code == 201, created.text
    project = created.json()

    strategy_source = project["strategy_source"].replace("top_n: int = 10", "top_n: int = 4")
    updated = client.put(
        "/api/strategy/projects/separate-source-routes/draft",
        json={
            "source": strategy_source,
            "expected_source_sha256": project["draft_source_sha256"],
            "confirm_write": True,
            "confirm_python_execution": True,
        },
    )
    assert updated.status_code == 200, updated.text
    project = updated.json()
    assert "top_n: int = 4" in project["strategy_source"]
    assert "@factor" not in project["strategy_source"]
    assert "def momentum_20d(" in project["draft_source"]

    added = client.post(
        "/api/strategy/projects/separate-source-routes/factors",
        json={
            "source": '@factor(id="close_level")\ndef close_level(context):\n    return context.current("close")\n',
            "expected_source_sha256": project["draft_source_sha256"],
            "confirm_write": True,
            "confirm_python_execution": True,
        },
    )
    assert added.status_code == 201, added.text
    assert added.json()["factor"]["id"] == "close_level"
    project = added.json()["project"]
    assert "@factor" not in project["strategy_source"]
    assert "def close_level(context)" in project["draft_source"]
    assert "factors/close_level.py" in {item["path"] for item in project["source_units"]}


def test_visual_settings_batch_is_one_atomic_source_edit(tmp_path, monkeypatch):
    database = tmp_path / "visual-settings.db"
    monkeypatch.setattr(strategy_service, "repository", lambda: StrategyRepository(database))
    monkeypatch.setattr(strategy_service, "operations_store", lambda: OperationsStore(tmp_path))
    client = TestClient(app)
    created = client.post(
        "/api/strategy/projects",
        json={
            "project_id": "visual-settings-project",
            "name": "Visual Settings",
            "confirm_save": True,
            "confirm_python_execution": True,
        },
    )
    assert created.status_code == 201, created.text
    edits = [
        {
            "operation": "schedule",
            "entrypoint_id": "monthly_momentum",
            "frequency": "weekly",
            "selector": "last_trading_day",
            "at": "close",
        },
        {
            "operation": "factor_blend",
            "entrypoint_id": "monthly_momentum",
            "factor_weights": {"momentum_20d": 0.75},
            "normalization": "zscore",
        },
        {
            "operation": "parameter",
            "entrypoint_id": "monthly_momentum",
            "parameter": "top_n",
            "value": 3,
        },
    ]

    preview = client.post(
        "/api/strategy/projects/visual-settings-project/edits/preview",
        json={
            "edits": edits,
            "expected_source_sha256": created.json()["draft_source_sha256"],
        },
    )
    assert preview.status_code == 200, preview.text
    assert "Weekly.last_trading_day" in preview.json()["source"]
    assert "monthly_momentum" in preview.json()["source"]
    assert "@factor" not in preview.json()["source"]
    unchanged = client.get("/api/strategy/projects/visual-settings-project").json()
    assert unchanged["draft_source_sha256"] == created.json()["draft_source_sha256"]

    updated = client.post(
        "/api/strategy/projects/visual-settings-project/edits",
        json={
            "operation": "batch",
            "edits": edits,
            "expected_source_sha256": created.json()["draft_source_sha256"],
            "confirm_write": True,
            "confirm_python_execution": True,
        },
    )
    assert updated.status_code == 200, updated.text
    project = updated.json()["project"]
    signal = next(
        item for item in project["inspection"]["entrypoints"] if item["id"] == "monthly_momentum"
    )
    assert signal["metadata"]["schedule"]["frequency"] == "weekly"
    assert signal["metadata"]["factor_blend"]["weights"] == {"momentum_20d": 0.75}
    assert signal["metadata"]["factor_blend"]["normalization"] == "zscore"
    assert next(item for item in signal["parameters"] if item["name"] == "top_n")["default"] == 3
    assert project["current_revision"] == 2
    assert project["dirty"] is False

    rejected = client.post(
        "/api/strategy/projects/visual-settings-project/edits",
        json={
            "operation": "batch",
            "edits": [
                {
                    "operation": "schedule",
                    "entrypoint_id": "monthly_momentum",
                    "frequency": "daily",
                    "selector": "every",
                    "at": "open",
                },
                {
                    "operation": "parameter",
                    "entrypoint_id": "monthly_momentum",
                    "parameter": "missing_parameter",
                    "value": 1,
                },
            ],
            "expected_source_sha256": project["draft_source_sha256"],
            "confirm_write": True,
            "confirm_python_execution": True,
        },
    )
    assert rejected.status_code == 422
    unchanged = client.get("/api/strategy/projects/visual-settings-project").json()
    assert unchanged["draft_source_sha256"] == project["draft_source_sha256"]

    empty = client.post(
        "/api/strategy/projects/visual-settings-project/edits",
        json={
            "operation": "batch",
            "edits": [],
            "confirm_write": True,
            "confirm_python_execution": True,
        },
    )
    assert empty.status_code == 422


def test_legacy_authoring_routes_are_not_mounted():
    client = TestClient(app)
    for path in (
        "/api/pipeline/projects",
        "/api/factor-research/library",
        "/api/python-lab/capabilities",
    ):
        assert client.get(path).status_code == 404


def test_complete_sdk_backtest_persists_revision_hash_and_manifest(tmp_path, monkeypatch):
    database = tmp_path / "runs.db"
    monkeypatch.setattr(strategy_service, "repository", lambda: StrategyRepository(database))
    monkeypatch.setattr(strategy_service, "ResultStore", lambda: ResultStore(database))
    result = strategy_service.run_project_backtest(
        "sdk-v1-default", "2025-01-01", "2025-03-31", "demo", 1
    )
    assert result["revision"] == 1
    assert result["source_sha256"]
    assert result["provenance"]["benchmark"]["source"] == "prepared_instrument_master"
    assert len(result["provenance"]["benchmark"]["symbols"]) == 300
    store = ResultStore(database)
    try:
        record = store.get_backtest_record(result["id"])
    finally:
        store.close()
    assert record is not None
    assert record["strategy_project_id"] == "sdk-v1-default"
    assert record["strategy_revision"] == 1
    assert record["strategy_source_sha256"] == result["source_sha256"]
    assert record["strategy_manifest_json"]
    assert record["run_diagnostics_json"]
    monkeypatch.setattr(result_service, "ResultStore", lambda: ResultStore(database))
    detail = result_service.get_backtest(result["id"])
    assert detail is not None
    assert "events" not in detail["run_diagnostics"]
    assert "execution_summary" in detail["run_diagnostics"]
    assert "signal_evidence" in detail["run_diagnostics"]
    assert all(
        "scores" not in row
        for row in detail["run_diagnostics"]["signal_evidence"]["rows"]
    )
    frozen_portfolio = next(
        item for item in detail["strategy_manifest"] if item["kind"] == "portfolio"
    )
    assert frozen_portfolio["parameters"]["max_weight"] == 0.1
    analysis = backtest_analytics_service.analyze_record(detail)
    assert analysis["strategy_snapshot"]["strategy_type"] == "sdk_v1"
    assert analysis["strategy_snapshot"]["revision"] == 1
    assert analysis["strategy_snapshot"]["source_sha256"] == result["source_sha256"]
