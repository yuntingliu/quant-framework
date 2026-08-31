from __future__ import annotations

import json

from alphalab.cli import main
from alphalab.devtools import doctor_report


def test_dev_doctor_reports_readiness_without_secret_values(monkeypatch) -> None:
    monkeypatch.setenv("RQ_USER", "doctor-user")
    monkeypatch.setenv("RQ_PASSWORD", "doctor-secret")
    monkeypatch.setenv("RQ_HOST", "doctor-host")

    report = doctor_report()

    assert report["status"] in {"ready", "degraded"}
    assert report["checks"]["demo_data"]["symbol_count"] == 300
    assert report["checks"]["rq"] == {
        "status": "configured",
        "configured": True,
        "missing": [],
    }
    assert report["checks"]["runtime_execution"]["required_datasets"] == [
        "rq.instruments",
        "rq.bars",
        "rq.paused",
    ]
    serialized = json.dumps(report)
    assert "doctor-secret" not in serialized
    assert "doctor-user" not in serialized
    assert "doctor-host" not in serialized


def test_dev_doctor_cli(capsys) -> None:
    exit_code = main(["dev", "doctor"])
    payload = json.loads(capsys.readouterr().out)

    assert exit_code in {0, 1}
    assert payload["checks"]["python"]["status"] == "ready"
    assert set(payload["checks"]["ports"]) == {"8000", "5173"}
