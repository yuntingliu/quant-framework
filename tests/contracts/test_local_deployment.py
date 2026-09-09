from __future__ import annotations

import os
from pathlib import Path

from fastapi.testclient import TestClient

from alphalab import ResultStore, StrategyRepository
from alphalab.cli import main
from dashboard.backend.main import app


def test_default_repositories_use_isolated_local_state():
    expected = Path(os.environ["ALPHALAB_APP_DATA_DIR"]) / "alphalab.db"
    store = ResultStore()
    repository = StrategyRepository()
    try:
        assert store.path == expected
        assert repository.path == expected
        assert repository.get_project("sdk-v1-default") is not None
    finally:
        repository.close()
        store.close()


def test_default_editor_mirror_uses_isolated_runtime():
    expected = Path(os.environ["ALPHALAB_RUNTIME_DIR"]) / "editor"
    response = TestClient(app).post(
        "/api/python-editor/documents",
        json={"kind": "strategy", "document_id": "deployment-test", "source": "x = 1\n"},
    )
    assert response.status_code == 200
    target = expected / "strategy/deployment-test/strategy.py"
    assert response.json()["uri"] == target.as_uri()
    assert target.read_text(encoding="utf-8") == "x = 1\n"


def test_serve_requires_frontend_build(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("alphalab.devtools.REPO_ROOT", tmp_path)
    assert main(["dev", "serve"]) == 2
    assert "npm --prefix dashboard/frontend run build" in capsys.readouterr().err


def test_serve_uses_existing_backend(tmp_path, monkeypatch):
    monkeypatch.setenv("ALPHALAB_AGENT_MODE", "off")
    index = tmp_path / "dashboard/frontend/dist/index.html"
    index.parent.mkdir(parents=True)
    index.write_text("<html></html>", encoding="utf-8")
    monkeypatch.setattr("alphalab.devtools.REPO_ROOT", tmp_path)
    calls = []
    monkeypatch.setattr("uvicorn.run", lambda *args, **kwargs: calls.append((args, kwargs)))
    assert main(["dev", "serve", "--port", "8100", "--agent", "off"]) == 0
    assert calls == [(("dashboard.backend.main:app",), {"host": "127.0.0.1", "port": 8100})]
