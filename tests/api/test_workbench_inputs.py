from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from alphalab.dataio.runtime import OperationsStore
from apps.api.main import app
from apps.api.services import data_service, data_sync_service, strategy_service


def test_recipe_can_extend_past_local_market_coverage(tmp_path, monkeypatch):
    class Today(date):
        @classmethod
        def today(cls):
            return cls(2026, 9, 9)

    catalog = SimpleNamespace(status=lambda _: {
        "status": "ready", "date_start": "2021-01-01", "date_end": "2026-08-24",
    })
    for service in (data_sync_service, strategy_service):
        monkeypatch.setattr(service, "date", Today)
        monkeypatch.setattr(service, "DataCatalog", lambda: catalog)
    monkeypatch.setattr(strategy_service, "get_project", lambda _: {"id": "date-test"})
    manager = SimpleNamespace(operations=OperationsStore(tmp_path))
    monkeypatch.setattr(data_sync_service, "get_job_manager", lambda: manager)
    client = TestClient(app)

    workspace = client.get("/api/data-sync/recipes/date-test").json()
    assert workspace["bounds"] == {"start": "2021-01-01", "end": "2026-09-09"}
    # Connection probing must not be required to extend acquisition dates.
    for end, status in (("2026-09-07", 200), ("2026-09-09", 200), ("2026-09-10", 422)):
        response = client.patch("/api/data-sync/recipes/date-test/parameters", json={
            "start": "2026-08-24", "end": end, "confirm_write": True,
        })
        assert response.status_code == status, response.text
        if status == 200:
            parameters = {item["name"]: item["default"] for item in response.json()["inspection"]["parameters"]}
            assert parameters["end"] == end

    # Reloading preserves the user's saved range, while the allowed range stays current.
    reloaded = client.get("/api/data-sync/recipes/date-test").json()
    assert reloaded["bounds"]["end"] == "2026-09-09"


@pytest.fixture
def market_client(monkeypatch):
    symbols = ["000001.SZ", "600000.SH", "920001.BJ"]

    class Engine:
        def get_symbols(self):
            return symbols

        def get_bars(self, requested, *_args, **_kwargs):
            assert set(requested) <= set(symbols)
            return pd.DataFrame({"date": ["2026-08-24"], "symbol": requested, "close": [10.0]})

        def get_fundamentals(self, requested, *_args, **_kwargs):
            assert requested == ["000001.SZ"]
            return pd.DataFrame({"symbol": requested, "available_date": ["2026-08-24"], "roe": [0.1]})

    monkeypatch.setattr(data_service, "create_runtime_engine", Engine)
    monkeypatch.setattr(data_service, "_profile_range", lambda _: ("2026-08-01", "2026-08-24"))
    monkeypatch.setattr(data_service, "research_value_fields", lambda *_: ("roe",))
    return TestClient(app)


@pytest.mark.parametrize("value,expected", [
    ("000001.SZ", "000001.SZ"), (" 000001.xshe ", "000001.SZ"),
    ("600000.XSHG", "600000.SH"), ("920001.XBEI", "920001.BJ"),
    ("000001", "000001.SZ"),
])
def test_market_bars_accept_framework_and_rq_codes(market_client, value, expected):
    response = market_client.get("/api/data/market/bars", params={"symbol": value})
    assert response.status_code == 200, response.text
    assert response.json()["symbol"] == expected
    assert response.json()["rows"][0]["symbol"] == expected


def test_fundamentals_deduplicate_symbol_aliases(market_client):
    response = market_client.get("/api/data/fundamentals", params={
        "symbols": ["000001.XSHE", "000001.SZ"], "fields": ["roe"],
    })
    assert response.status_code == 200, response.text
    assert response.json()["symbols"] == ["000001.SZ"]
    assert response.json()["returned_rows"] == 1


@pytest.mark.parametrize("endpoint,key", [("/market/bars", "symbol"), ("/fundamentals", "symbols")])
def test_invalid_symbol_returns_input_error_before_accessing_data(monkeypatch, endpoint, key):
    def unexpected_data_access():
        pytest.fail("Invalid input should not open the data engine")

    monkeypatch.setattr(data_service, "create_runtime_engine", unexpected_data_access)
    response = TestClient(app).get(f"/api/data{endpoint}", params={key: "000001.BAD"})
    assert response.status_code == 422
    assert "证券代码格式不支持" in response.json()["detail"]
    assert "000001.XSHE" in response.json()["detail"]


def test_valid_but_missing_symbol_returns_missing_data_message(market_client):
    response = market_client.get("/api/data/market/bars", params={"symbol": "000002.XSHE"})
    assert response.status_code == 404
    assert "先同步数据" in response.json()["detail"]
