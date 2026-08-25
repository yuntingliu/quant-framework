from __future__ import annotations

import pandas as pd
from fastapi.testclient import TestClient

from alphalab.pipeline import PipelineRepository
from alphalab.store import ResultStore
from dashboard.backend.main import app
from dashboard.backend.services import python_lab_service


class _FakeEngine:
    def get_symbols(self, pool="all"):
        assert pool == "etf"
        return ["510300", "513100"]

    def get_bars(self, symbols, start, end, **kwargs):
        rows = []
        for symbol in symbols:
            for index, day in enumerate(pd.date_range("2026-01-01", periods=8, freq="D")):
                rows.append(
                    {
                        "date": day,
                        "symbol": symbol,
                        "open": 10 + index,
                        "high": 11 + index,
                        "low": 9 + index,
                        "close": 10.5 + index,
                        "volume": 1000 + index,
                    }
                )
        return pd.DataFrame(rows)


def test_context_is_bounded_json_without_composed_source(monkeypatch):
    monkeypatch.setattr(python_lab_service, "_engine", lambda profile: _FakeEngine())
    monkeypatch.setattr(
        python_lab_service,
        "_profile_range",
        lambda profile: ("2026-01-01", "2026-01-08"),
    )
    project = {
        "id": "project-a",
        "name": "Project A",
        "revision": 3,
        "settings": {"universe": {"pool": "etf", "symbols": []}},
        "component_manifest": [{"stage": "selection"}],
        "source_sha256": "abc",
        "composed_source": "must not be injected",
    }
    context = python_lab_service.build_context(
        project,
        profile="demo",
        as_of_date="2026-01-08",
        lookback_days=5,
        requested_symbols=["510300"],
        max_rows=100,
    )
    assert context["dataset"]["rows"] == 5
    assert context["dataset"]["truncated"] is False
    assert context["project"]["source_sha256"] == "abc"
    assert "composed_source" not in context["project"]
    assert {row["symbol"] for row in context["market_bars"]} == {"510300"}


def test_api_reports_disabled_runtime_and_requires_execution_confirmation(monkeypatch):
    monkeypatch.setenv("ALPHALAB_PYTHON_LAB_RUNTIME", "disabled")
    client = TestClient(app)
    capabilities = client.get("/api/python-lab/capabilities")
    assert capabilities.status_code == 200
    assert capabilities.json()["available"] is False
    response = client.post(
        "/api/python-lab/runs",
        json={
            "project_id": "missing-project",
            "source": "def run(context):\n    return {}\n",
            "confirm_python_execution": False,
        },
    )
    assert response.status_code == 403
    assert "confirm_python_execution" in response.json()["detail"]


def test_store_persists_run_hashes_output_and_promotion_audit(tmp_path):
    database = tmp_path / "lab.db"
    repository = PipelineRepository(database)
    try:
        project_id = repository.list_projects()[0]["id"]
    finally:
        repository.close()
    store = ResultStore(database)
    try:
        run_id = store.create_python_lab_run(
            project_id=project_id,
            profile="demo",
            runtime_kind="trusted_local",
            source="def run(context):\n    return {}\n",
            source_sha256="source-hash",
            context={"contract_version": 1},
            context_sha256="context-hash",
        )
        store.finish_python_lab_run(run_id, status="succeeded", output={"ok": True})
        promotion_id = store.record_python_lab_promotion(
            run_id,
            kind="factor",
            target_id="lab_factor",
            applied_to_project=True,
            project_revision=2,
        )
        record = store.get_python_lab_run(run_id)
    finally:
        store.close()
    assert record is not None
    assert record["output"] == {"ok": True}
    assert record["source_sha256"] == "source-hash"
    assert record["promotions"] == [
        {
            "id": promotion_id,
            "kind": "factor",
            "target_id": "lab_factor",
            "applied_to_project": 1,
            "project_revision": 2,
            "created_at": record["promotions"][0]["created_at"],
        }
    ]
