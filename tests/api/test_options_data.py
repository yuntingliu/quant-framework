"""Saved export boundaries and auditable quarantine retrieval without vendor access."""

import json
from zipfile import ZipFile

import pandas as pd
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from alphalab.dataio.errors import MissingDataError
from apps.api.routers import options
from apps.api.services import options_data_service as service

app = FastAPI()
app.include_router(options.router)
client = TestClient(app)


def archive(root, name, manifest):
    target = root / "artifacts" / "exports" / name
    target.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(target, "w") as z:
        z.writestr("manifest.json", json.dumps(manifest))
    return target


def manifest(start="2015-02-09", rows=100):
    return {
        "main_period": [start, "2026-10-23"],
        "datasets": {"rq.option_snapshots": {"rows": rows}},
    }


def test_export_manifest_validation_order_and_http_download(tmp_path, monkeypatch):
    full = archive(tmp_path, "ETF_Options_Data_20150209-20261023.zip", manifest())
    archive(tmp_path, "ETF_Options_Data_20230103-20261023.zip", manifest("2023-01-03", 20))
    full.with_suffix(".zip.sha256").write_text("a" * 64 + "  " + full.name)
    for index, invalid in enumerate(
        [
            [],
            None,
            {"main_period": None},
            {"datasets": []},
            {**manifest(), "datasets": {"rq.option_snapshots": None}},
        ]
    ):
        archive(tmp_path, f"ETF_Options_Data_20150209-20261023_00000{index}.zip", invalid)
    monkeypatch.setattr(service, "REPO_ROOT", tmp_path)
    response = client.get("/api/options/exports")
    assert response.status_code == 200
    rows = response.json()["archives"]
    assert [r["rows"] for r in rows] == [100, 20]
    assert rows[0]["sha256"] == "a" * 64
    response = client.get(f"/api/options/exports/{full.name}", headers={"range": "bytes=0-3"})
    assert response.status_code == 206 and response.content == b"PK\x03\x04"
    assert full.name in response.headers["content-disposition"]
    assert response.headers["x-content-type-options"] == "nosniff"


@pytest.mark.parametrize(
    "name",
    [
        "../secret.zip",
        "C:/secret.zip",
        "secret.zip",
        "ETF_Options_Data_20150209-20261023.zip/other",
    ],
)
def test_export_rejects_unmanaged_names(tmp_path, name):
    with pytest.raises(ValueError):
        service.archive_path(name, tmp_path)


def test_export_missing_managed_file_is_404(tmp_path, monkeypatch):
    monkeypatch.setattr(service, "REPO_ROOT", tmp_path)
    assert (
        client.get("/api/options/exports/ETF_Options_Data_20150209-20261023.zip").status_code == 404
    )
    with pytest.raises(MissingDataError):
        service.archive_path("ETF_Options_Data_20150209-20261023.zip", tmp_path)


def test_quarantine_preserves_raw_values_and_total_when_limited(tmp_path):
    target = tmp_path / "rq/option_snapshots/underlying=510050.XSHG/2026-10-23.parquet"
    target.parent.mkdir(parents=True)
    base = {
        "date": "2026-10-23",
        "underlying": "510050.XSHG",
        "maturity_date": "2026-11-25",
        "option_type": "C",
        "strike_price": 3.0,
        "contract_multiplier": 10220.0,
        "open": 0.1,
        "high": 0.2,
        "low": 0.1,
        "close": 0.3,
        "volume": 10.0,
        "quote_quarantine": "source_quote_invalid",
        "quote_quarantine_reason": "outside range",
    }
    pd.DataFrame(
        [{**base, "order_book_id": "10001"}, {**base, "order_book_id": "10002"}]
    ).to_parquet(target, index=False)
    result = service.quarantine(root=tmp_path, limit=1)
    assert result["total"] == 2 and len(result["rows"]) == 1
    assert result["rows"][0]["close"] == 0.3
    assert result["rows"][0]["contract_name_zh"] and result["rows"][0]["contract_name_en"]
    assert service.quarantine(root=tmp_path, underlying="159915.XSHE")["total"] == 0
    with pytest.raises(ValueError):
        service.quarantine(root=tmp_path, underlying="../secret")
