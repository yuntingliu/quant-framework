from __future__ import annotations

import pandas as pd
from fastapi.testclient import TestClient

from alphalab import ResultStore
from alphalab.strategy import (
    StrategyRepository,
    TimingStrategyRepository,
)
from dashboard.backend.main import app
from dashboard.backend.routers import conexus, reports
from dashboard.backend.services import framework_service, research_service


def test_backend_smoke_endpoints():
    client = TestClient(app)
    assert client.get("/").json()["status"] == "ok"
    providers = client.get("/api/data/providers")
    assert providers.status_code == 200
    assert providers.json()["symbol_count"] == 300
    assert all(item["status"] == "ready" for item in providers.json()["datasets"].values())
    manifest = client.get("/api/data/manifest")
    assert manifest.status_code == 200
    assert manifest.json()["realtime"] == "not_configured"
    strategies = client.get("/api/strategies")
    assert strategies.status_code == 200
    assert {item["id"] for item in strategies.json()} >= {"momentum", "balanced"}
    assert {item["strategy_type"] for item in strategies.json()} == {
        "stock_selection",
        "market_timing",
    }
    assert client.get("/api/system/logs").status_code == 200
    health = client.get("/api/data-sync/health")
    assert health.status_code == 200
    assert health.json()["tools"]["status"] == "ready"
    assert health.json()["planner"]["status"] == "not_configured"
    catalog = client.get("/api/data-sync/catalog")
    assert catalog.status_code == 200
    assert catalog.json()["datasets"]


def test_optional_conexus_status_contract(monkeypatch):
    async def available(_path: str) -> dict:
        return {"ok": True}

    monkeypatch.setattr(conexus, "_fetch_json", available)
    response = TestClient(app).get("/api/conexus/status")
    assert response.status_code == 200
    assert response.json() == {
        "available": True,
        "mode": "published_harness",
        "publication": "alphalab-research-agent",
    }
    assert conexus._path_segment("../run/id") == "..%2Frun%2Fid"


def test_real_data_endpoints():
    client = TestClient(app)
    symbol_payload = client.get("/api/data/market/symbols").json()
    symbols = symbol_payload["symbols"]
    assert len(symbols) == 300
    assert [item["symbol"] for item in symbol_payload["instruments"]] == symbols
    assert all(set(item) == {"symbol", "name"} for item in symbol_payload["instruments"])
    assert all(item["name"] for item in symbol_payload["instruments"])
    instrument_names = {
        item["symbol"]: item["name"] for item in symbol_payload["instruments"]
    }
    assert instrument_names["600519.SH"] == "贵州茅台"
    bars = client.get(f"/api/data/market/bars?symbol={symbols[0]}&start=2026-01-01")
    assert bars.status_code == 200
    assert bars.json()["rows"]
    factors = client.get("/api/data/factors/returns")
    assert factors.status_code == 200
    assert factors.json()["names"] == ["MKT", "SMB", "HML", "MOM", "RMW", "rf"]
    assert client.get("/api/data/market/bars?symbol=NOT-A-SYMBOL").status_code == 404
    assert client.get("/api/data/market/symbols?profile=unknown").status_code == 422


def test_market_symbol_options_include_instrument_names(monkeypatch):
    class FakeEngine:
        def get_symbols(self):
            return ["000001.SZ", "600000.SH"]

        def get_latest_date(self):
            return "2026-08-14"

        def get_instruments(self, asof_date):
            assert asof_date == "2026-08-14"
            return pd.DataFrame(
                [
                    {"symbol": "000001.sz", "name": "平安银行"},
                    {"symbol": "600000.SH", "name": "浦发银行"},
                ]
            )

    monkeypatch.setattr(framework_service, "_engine", lambda _profile: FakeEngine())
    assert framework_service.market_symbol_options("demo") == [
        {"symbol": "000001.SZ", "name": "平安银行"},
        {"symbol": "600000.SH", "name": "浦发银行"},
    ]


def test_fundamentals_endpoint_is_bounded_and_point_in_time():
    client = TestClient(app)
    response = client.get(
        "/api/data/fundamentals",
        params=[
            ("symbols", "000002.SZ"),
            ("fields", "ep"),
            ("fields", "roe"),
            ("start_quarter", "2025q1"),
            ("end_quarter", "2026q2"),
            ("asof_date", "2026-07-10"),
            ("limit", "20"),
        ],
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["profile"] == "demo"
    assert payload["fields"] == ["ep", "roe"]
    assert payload["returned_rows"] <= 20
    assert all(row["available_date"] <= "2026-07-10" for row in payload["rows"])


def test_agent_data_tool_bridge_describes_invokes_and_guards_mutation():
    client = TestClient(app)
    described = client.get("/api/agent/data-tools")
    assert described.status_code == 200
    payload = described.json()
    assert payload["count"] == 6
    assert {tool["name"] for tool in payload["tools"]} == {
        "data.catalog",
        "data.status",
        "data.plan_sync",
        "data.run_sync",
        "data.validate",
        "data.query",
    }

    status = client.post(
        "/api/agent/data-tools/data.status/invoke",
        json={"input": {}},
    )
    assert status.status_code == 200
    assert status.json()["result"]["total"] == 6

    guarded = client.post(
        "/api/agent/data-tools/data.run_sync/invoke",
        json={"input": {}, "confirm": False},
    )
    assert guarded.status_code == 409


def test_market_analytics_endpoints():
    client = TestClient(app)
    endpoints = (
        "/api/market/kpi",
        "/api/market/cumulative-returns",
        "/api/market/factor-stats",
        "/api/market/drawdowns",
        "/api/market/annual-returns",
        "/api/market/volatility",
        "/api/market/correlation",
    )
    for endpoint in endpoints:
        response = client.get(endpoint)
        assert response.status_code == 200, response.text
        assert response.json()["profile"] == "demo"

    cumulative = client.get("/api/market/cumulative-returns").json()
    assert cumulative["dates"]
    assert set(cumulative["series"]) == {"MKT", "SMB", "HML"}
    assert client.get("/api/market/kpi?profile=runtime").status_code == 503
    assert client.get("/api/market/kpi?start=2026-01-01&end=2025-01-01").status_code == 422
    assert client.get("/api/market/correlation?factors=UNKNOWN").status_code == 422


def test_factor_research_library_and_expression_guardrails():
    client = TestClient(app)
    library = client.get("/api/factor-research/library")
    assert library.status_code == 200
    assert {item["name"] for item in library.json()["factors"]} >= {
        "momentum_20d",
        "roe",
    }
    rejected = client.post(
        "/api/factor-research/evaluate",
        json={
            "name": "unsafe",
            "source": "expression",
            "expression": "__import__('os').system('whoami')",
            "start_date": "2022-01-01",
            "end_date": "2024-01-01",
        },
    )
    assert rejected.status_code == 422


def test_custom_market_risk_factor_endpoint_and_guardrails():
    client = TestClient(app)
    accepted = client.post(
        "/api/market/custom-risk-factor/evaluate",
        json={
            "name": "market_style_blend",
            "expression": "0.75 * MKT + 0.25 * SMB - rf",
            "profile": "demo",
            "start_date": "2022-01-01",
            "end_date": "2024-01-01",
        },
    )
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["dependencies"] == ["MKT", "SMB", "rf"]
    assert accepted.json()["summary"]["observations"] > 0

    rejected = client.post(
        "/api/market/custom-risk-factor/evaluate",
        json={
            "name": "unsafe",
            "expression": "__import__('os').system('whoami')",
        },
    )
    assert rejected.status_code == 422


def test_agent_reports_are_durable_and_provenance_bound(tmp_path, monkeypatch):
    database = tmp_path / "reports.db"
    monkeypatch.setattr(reports, "ResultStore", lambda: ResultStore(database))
    client = TestClient(app)
    result = {
        "version": 1,
        "requestId": "request-report-1",
        "kind": "document",
        "title": "Factor review",
        "markdown": "# Factor review\n\nEvidence-backed result.",
        "sources": ["alphalab_evaluate_factor"],
    }
    created = client.post(
        "/api/reports",
        json={"profile": "demo", "result": result},
    )
    assert created.status_code == 201, created.text
    payload = created.json()
    assert payload["requestId"] == result["requestId"]
    assert payload["profile"] == "demo"
    assert payload["provenance"]["data"]["aggregate_sha256"]
    assert payload["provenance"]["code"]["source_sha256"]
    assert payload["provenance"]["strategy_sha256"] is None

    listed = client.get("/api/reports")
    assert listed.status_code == 200
    assert listed.json()["items"][0]["requestId"] == result["requestId"]
    detail = client.get("/api/reports/request-report-1")
    assert detail.status_code == 200
    assert detail.json()["markdown"] == result["markdown"]


def test_data_sync_plan_and_submit_contract(monkeypatch):
    client = TestClient(app)
    plan = client.post(
        "/api/data-sync/plan",
        json={
            "source": "rq",
            "datasets": ["bars", "fundamentals"],
            "symbols": ["000001.SZ"],
            "start": "2024-01-01",
            "end": "2025-01-01",
        },
    )
    assert plan.status_code == 200
    assert plan.json()["writes_are_local"] is True
    assert plan.json()["symbol_count"] == 1

    monkeypatch.setattr(
        "dashboard.backend.services.data_sync_service.submit",
        lambda request: {
            "id": "job-1",
            "source": "rq",
            "status": "queued",
            "request": request.model_dump(mode="json"),
        },
    )
    submitted = client.post(
        "/api/data-sync/jobs",
        json={"source": "rq", "datasets": ["bars"], "symbols": ["000001.SZ"]},
    )
    assert submitted.status_code == 202
    assert submitted.json()["status"] == "queued"


def test_backtest_signal_and_paper_endpoints(tmp_path, monkeypatch):
    monkeypatch.setattr(framework_service, "ResultStore", lambda: ResultStore(tmp_path / "alphalab.db"))

    client = TestClient(app)
    response = client.post(
        "/api/backtests/run",
        json={
            "strategy_id": "momentum",
            "start_date": "2023-01-01",
            "end_date": "2023-12-31",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["strategy_id"] == "momentum"
    assert payload["returns"]
    assert payload["weights_count"] > 0
    assert payload["metrics"]["n_periods"] > 0

    detail = client.get(f"/api/backtests/{payload['id']}")
    assert detail.status_code == 200
    assert detail.json()["returns"]
    assert detail.json()["weights"]
    assert detail.json()["executions"]
    assert detail.json()["provenance"]["data"]["aggregate_sha256"]
    assert isinstance(detail.json()["provenance"]["code"]["dirty"], bool)
    assert detail.json()["provenance"]["code"]["source_sha256"]

    analysis = client.get(f"/api/backtests/{payload['id']}/analysis")
    assert analysis.status_code == 200
    assert analysis.json()["equity_curve"]
    assert analysis.json()["benchmark_equity_curve"]
    assert analysis.json()["excess_equity_curve"]
    assert analysis.json()["benchmark_coverage"] > 0.8
    assert analysis.json()["has_execution_audit"] is True
    assert analysis.json()["strategy_snapshot"]["name"] == "momentum"
    assert analysis.json()["holdings"]
    robustness = client.get(f"/api/backtests/{payload['id']}/robustness")
    assert robustness.status_code == 200
    assert robustness.json()["status"] in {
        "research_candidate",
        "watch",
        "weak",
        "invalid",
    }

    second = client.post(
        "/api/backtests/run",
        json={
            "strategy_id": "value",
            "start_date": "2023-01-01",
            "end_date": "2023-12-31",
        },
    )
    assert second.status_code == 200
    comparison = client.post(
        "/api/backtests/compare",
        json={"ids": [payload["id"], second.json()["id"]]},
    )
    assert comparison.status_code == 200
    assert len(comparison.json()["series"]) == 2
    assert client.post("/api/backtests/compare", json={"ids": [payload["id"]]}).status_code == 422
    assert (
        client.post(
            "/api/backtests/compare",
            json={"ids": [payload["id"], "does-not-exist"]},
        ).status_code
        == 404
    )

    signal = client.post("/api/signals/generate", json={"strategy_id": "balanced", "persist": True})
    assert signal.status_code == 200
    assert len(signal.json()["targets"]) == 10
    assert signal.json()["selection"]["selected_count"] == 10
    assert signal.json()["selection"]["rows"][0]["selected"] is True

    symbol = next(iter(signal.json()["targets"]))
    order = client.post(
        "/api/paper/orders",
        json={"symbol": symbol, "action": "buy", "quantity": 100},
    )
    assert order.status_code == 200
    assert order.json()["status"] == "filled"
    invalid_lot = client.post(
        "/api/paper/orders",
        json={"symbol": symbol, "action": "buy", "quantity": 50},
    )
    assert invalid_lot.status_code == 422
    assert client.get("/api/paper/orders").json()
    account = client.get("/api/paper/account?profile=demo")
    assert account.status_code == 200
    assert account.json()["account"]["equity"] > 0
    preview = client.post(
        "/api/paper/rebalance/preview",
        json={"strategy_id": "balanced", "profile": "demo"},
    )
    assert preview.status_code == 200, preview.text
    assert preview.json()["risk_status"] in {"ready", "blocked"}


def test_strategy_validation_contract():
    client = TestClient(app)
    strategy = client.get("/api/strategies/value")
    assert strategy.status_code == 200
    payload = strategy.json()
    assert payload["built_in"] is True
    assert payload["editable"] is False
    assert payload["config"]["name"] == "value"
    selection = client.post(
        "/api/strategies/selection-preview",
        json={"config": payload["config"], "profile": "demo"},
    )
    assert selection.status_code == 200, selection.text
    selection_payload = selection.json()
    assert selection_payload["id"] is None
    assert selection_payload["selection"]["selected_count"] == (
        payload["config"]["selection"]["n_stocks"]
    )
    assert selection_payload["selection"]["rows"][0]["factor_scores"]

    validated = client.post(
        "/api/strategies/validate",
        json={"yaml": payload["yaml"]},
    )
    assert validated.status_code == 200
    validation = validated.json()
    assert validation["valid"] is True
    assert validation["config"] == payload["config"]
    assert any(check["code"] == "factor_weight_total" for check in validation["checks"])

    structured = client.post(
        "/api/strategies/validate",
        json={"config": payload["config"]},
    )
    assert structured.status_code == 200
    assert structured.json()["normalized_yaml"]


def test_python_strategy_drafts_validate_and_research_through_api():
    client = TestClient(app)
    selection_config = {
        "strategy_type": "stock_selection",
        "name": "python_selection_draft",
        "universe": {
            "symbols": ["600519.SH", "002594.SZ", "000858.SZ"],
            "min_history_days": 20,
        },
        "factors": [],
        "selection": {"n_stocks": 2},
        "portfolio": {"max_weight": 0.5},
        "implementation": {
            "kind": "python",
            "entrypoint": "generate",
            "timeout_seconds": 5.0,
        },
    }
    selection_source = (
        "def generate(context):\n"
        "    symbols = sorted(row['symbol'] for row in context['candidates'])[:2]\n"
        "    return {'weights': {symbol: 0.5 for symbol in symbols}}\n"
    )
    validation = client.post(
        "/api/strategies/validate",
        json={"config": selection_config, "python_source": selection_source},
    )
    assert validation.status_code == 200, validation.text
    assert validation.json()["valid"] is True
    assert validation.json()["python_source_sha256"]

    preview = client.post(
        "/api/strategies/selection-preview",
        json={
            "config": selection_config,
            "python_source": selection_source,
            "profile": "demo",
        },
    )
    assert preview.status_code == 200, preview.text
    assert preview.json()["selection"]["selected_count"] == 2
    assert preview.json()["diagnostics"]["implementation"] == "python"

    timing_config = {
        "strategy_type": "market_timing",
        "name": "python_timing_draft",
        "signals": [],
        "position": {"min_exposure": 0.2, "max_exposure": 0.8},
        "implementation": {
            "kind": "python",
            "entrypoint": "generate",
            "timeout_seconds": 5.0,
        },
    }
    timing_source = (
        "def generate(context):\n"
        "    return {'market_exposure': 0.5}\n"
    )
    research = client.post(
        "/api/strategies/timing-research",
        json={
            "config": timing_config,
            "python_source": timing_source,
            "profile": "demo",
            "start_date": "2024-01-01",
            "end_date": "2025-12-31",
        },
    )
    assert research.status_code == 200, research.text
    assert research.json()["series"]
    assert research.json()["diagnostics"]["python"]["source_sha256"]


def test_saved_python_strategy_runs_with_source_snapshot(tmp_path, monkeypatch):
    class LocalSelectionRepository(StrategyRepository):
        def __init__(self):
            super().__init__(tmp_path / "selection")

    class LocalTimingRepository(TimingStrategyRepository):
        def __init__(self):
            super().__init__(tmp_path / "timing")

    database = tmp_path / "python-strategy.db"
    monkeypatch.setattr(framework_service, "StrategyRepository", LocalSelectionRepository)
    monkeypatch.setattr(framework_service, "TimingStrategyRepository", LocalTimingRepository)
    monkeypatch.setattr(framework_service, "ResultStore", lambda: ResultStore(database))
    monkeypatch.setattr(research_service, "ResultStore", lambda: ResultStore(database))
    client = TestClient(app)
    source = (
        "def generate(context):\n"
        "    symbols = sorted(row['symbol'] for row in context['candidates'])[:2]\n"
        "    return {'weights': {symbol: 0.5 for symbol in symbols}}\n"
    )
    config = {
        "strategy_type": "stock_selection",
        "name": "python_saved",
        "universe": {
            "symbols": ["600519.SH", "002594.SZ", "000858.SZ"],
            "min_history_days": 20,
        },
        "factors": [],
        "selection": {"n_stocks": 2},
        "portfolio": {"max_weight": 0.5},
        "implementation": {
            "kind": "python",
            "entrypoint": "generate",
            "timeout_seconds": 5.0,
        },
    }
    validated = client.post(
        "/api/strategies/validate",
        json={"config": config, "python_source": source},
    )
    assert validated.status_code == 200, validated.text
    saved = client.put(
        "/api/strategies/python_saved",
        json={
            "yaml": validated.json()["normalized_yaml"],
            "python_source": source,
        },
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["python_source"] == source

    backtest = client.post(
        "/api/backtests/run",
        json={
            "strategy_id": "python_saved",
            "start_date": "2025-01-01",
            "end_date": "2025-04-30",
            "profile": "demo",
        },
    )
    assert backtest.status_code == 200, backtest.text
    provenance = backtest.json()["provenance"]
    assert provenance["strategy_python"]["source"] == source
    assert provenance["strategy_python"]["sha256"] == provenance["strategy_python_sha256"]

    research_run = research_service.ResearchRunManager().run_now(
        {
            "strategy_id": "python_saved",
            "profile": "demo",
            "start_date": "2025-01-01",
            "end_date": "2025-04-30",
            "account_id": "paper",
        }
    )
    assert research_run["status"] == "succeeded"
    strategy_step = next(
        step for step in research_run["steps"] if step["name"] == "strategy_validate"
    )
    assert strategy_step["detail"]["implementation"] == "python"
    assert strategy_step["detail"]["python_source_sha256"] == provenance[
        "strategy_python_sha256"
    ]


def test_timing_strategy_research_and_backtest_contract(tmp_path, monkeypatch):
    database = tmp_path / "timing.db"
    def factory():
        return ResultStore(database)

    monkeypatch.setattr(framework_service, "ResultStore", factory)
    monkeypatch.setattr(research_service, "ResultStore", factory)
    client = TestClient(app)
    strategy = client.get("/api/strategies/timing_trend")
    assert strategy.status_code == 200
    payload = strategy.json()
    assert payload["strategy_type"] == "market_timing"
    assert payload["signals"] == ["trend", "momentum"]

    validation = client.post(
        "/api/strategies/validate",
        json={"config": payload["config"]},
    )
    assert validation.status_code == 200
    assert validation.json()["valid"] is True
    assert validation.json()["strategy_type"] == "market_timing"

    research = client.post(
        "/api/strategies/timing-research",
        json={
            "config": payload["config"],
            "profile": "demo",
            "start_date": "2023-01-01",
            "end_date": "2025-12-31",
        },
    )
    assert research.status_code == 200, research.text
    assert research.json()["series"]
    assert research.json()["signals"]
    assert 0 <= research.json()["diagnostics"]["latest_exposure"] <= 1

    backtest = client.post(
        "/api/backtests/run",
        json={
            "strategy_id": "timing_trend",
            "start_date": "2023-01-01",
            "end_date": "2025-12-31",
            "profile": "demo",
        },
    )
    assert backtest.status_code == 200, backtest.text
    result = backtest.json()
    assert result["strategy_type"] == "market_timing"
    assert result["execution"]["latest_exposure"] >= 0

    analysis = client.get(f"/api/backtests/{result['id']}/analysis")
    assert analysis.status_code == 200, analysis.text
    assert analysis.json()["strategy_snapshot"]["strategy_type"] == "market_timing"
    assert len(analysis.json()["holdings"]) == result["metrics"]["n_periods"]
    assert any(
        row["top_holdings"]
        and row["top_holdings"][0]["symbol"] == "MARKET_EXPOSURE"
        for row in analysis.json()["holdings"]
    )
    robustness = client.get(f"/api/backtests/{result['id']}/robustness")
    assert robustness.status_code == 200, robustness.text

    run = research_service.ResearchRunManager().run_now(
        {
            "strategy_id": "timing_trend",
            "profile": "demo",
            "start_date": "2023-01-01",
            "end_date": "2025-12-31",
            "account_id": "paper",
        }
    )
    assert run["status"] == "succeeded"
    assert run["result"]["signal_id"] is None
    assert run["result"]["preview_id"] is None
    assert run["result"]["paper_execution"] == "not_applicable_for_market_timing"
    strategy_step = next(
        step for step in run["steps"] if step["name"] == "strategy_validate"
    )
    assert strategy_step["detail"]["strategy_type"] == "market_timing"
    assert strategy_step["detail"]["implementation"] == "configured"
    assert strategy_step["detail"]["python_source_sha256"] is None
    assert next(step for step in run["steps"] if step["name"] == "signal")["status"] == "succeeded"
    assert next(step for step in run["steps"] if step["name"] == "risk_preview")["status"] == "skipped"


def test_deterministic_research_run_stops_before_paper_execution(
    tmp_path,
    monkeypatch,
):
    database = tmp_path / "research.db"

    def factory():
        return ResultStore(database)

    monkeypatch.setattr(framework_service, "ResultStore", factory)
    monkeypatch.setattr(research_service, "ResultStore", factory)
    manager = research_service.ResearchRunManager()

    result = manager.run_now(
        {
            "strategy_id": "value",
            "profile": "demo",
            "start_date": "2023-01-01",
            "end_date": "2023-12-31",
            "account_id": "paper",
        }
    )

    assert result["status"] == "succeeded"
    assert all(step["status"] == "succeeded" for step in result["steps"])
    assert result["result"]["paper_execution"] == "awaiting_user_confirmation"
    store = ResultStore(database)
    try:
        assert store.list_orders(account_id="paper").empty
    finally:
        store.close()


def test_deterministic_research_rejects_missing_python_source_before_backtest(
    tmp_path,
    monkeypatch,
):
    database = tmp_path / "python-research-validation.db"

    def factory():
        return ResultStore(database)

    backtest_calls = []
    monkeypatch.setattr(research_service, "ResultStore", factory)
    monkeypatch.setattr(
        research_service,
        "get_strategy_template",
        lambda _strategy_id: {
            "id": "python_missing",
            "strategy_type": "stock_selection",
            "yaml": (
                "strategy_type: stock_selection\n"
                "name: python_missing\n"
                "factors: []\n"
                "implementation:\n"
                "  kind: python\n"
                "  entrypoint: generate\n"
                "  timeout_seconds: 5\n"
            ),
            "python_source": None,
        },
    )
    monkeypatch.setattr(
        research_service,
        "run_strategy_backtest",
        lambda *args, **kwargs: backtest_calls.append((args, kwargs)),
    )

    result = research_service.ResearchRunManager().run_now(
        {
            "strategy_id": "python_missing",
            "profile": "demo",
            "start_date": "2023-01-01",
            "end_date": "2023-12-31",
            "account_id": "paper",
        }
    )

    assert result["status"] == "failed"
    validation_step = next(
        step for step in result["steps"] if step["name"] == "strategy_validate"
    )
    assert validation_step["status"] == "failed"
    assert "python_source must not be empty" in validation_step["detail"]["error"]
    assert backtest_calls == []
