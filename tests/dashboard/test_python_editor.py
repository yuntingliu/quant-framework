from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from alphalab.strategy.builtins import DEFAULT_STRATEGY_SOURCE
from dashboard.backend.main import app
from dashboard.backend.services import python_editor_service


def test_python_editor_capabilities_are_fixed_local_tools() -> None:
    response = TestClient(app).get("/api/python-editor/capabilities")

    assert response.status_code == 200
    payload = response.json()
    assert set(payload["servers"]) == {"pyrefly", "ruff"}
    assert payload["servers"]["pyrefly"]["command"][-1] == "lsp"
    assert payload["servers"]["ruff"]["command"][-1] == "server"
    assert payload["workspace_uri"].startswith("file:")


def test_python_editor_mirror_stays_in_ignored_runtime_root(tmp_path, monkeypatch) -> None:
    mirror_root = tmp_path / "editor"
    monkeypatch.setattr(python_editor_service, "MIRROR_ROOT", mirror_root)

    payload = python_editor_service.mirror_document("strategy", "sample-project", "x = 1\n")

    target = Path(payload["uri"].removeprefix("file:///"))
    if not target.is_file():  # Windows file URIs keep the drive separator.
        target = mirror_root / "strategy" / "sample-project" / "strategy.py"
    assert target.read_text(encoding="utf-8") == "x = 1\n"
    with pytest.raises(ValueError):
        python_editor_service.mirror_document("strategy", "../escape", "x = 1\n")


def test_sdk_contract_diagnostics_are_editor_markers() -> None:
    valid = python_editor_service.source_diagnostics("strategy", DEFAULT_STRATEGY_SOURCE)
    invalid = python_editor_service.source_diagnostics(
        "strategy", DEFAULT_STRATEGY_SOURCE.replace("SDK_VERSION = 1", "SDK_VERSION = 2")
    )

    assert valid["valid"] is True
    assert invalid["valid"] is False
    assert invalid["diagnostics"][0]["severity"] == "error"
    assert invalid["diagnostics"][0]["source"] == "AlphaLab SDK"


def test_isolated_factor_document_has_function_level_diagnostics(tmp_path, monkeypatch) -> None:
    source = '''@factor(id="momentum")
def momentum(context, *, window: int = 20):
    return context.history("close", window=window)
'''
    valid = python_editor_service.source_diagnostics("factor", source)
    invalid = python_editor_service.source_diagnostics(
        "factor", "def momentum(context):\n    return 1\n"
    )
    monkeypatch.setattr(python_editor_service, "MIRROR_ROOT", tmp_path / "editor")
    mirrored = python_editor_service.mirror_document("factor", "project.factor.0", source)

    assert valid == {"valid": True, "diagnostics": []}
    assert invalid["valid"] is False
    assert "@factor" in invalid["diagnostics"][0]["message"]
    assert mirrored["uri"].endswith("/factor.py")


def test_lsp_stdio_framing_round_trip() -> None:
    message = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize"})

    async def exercise() -> None:
        reader = asyncio.StreamReader()
        payload = message.encode("utf-8")
        reader.feed_data(f"Content-Length: {len(payload)}\r\n\r\n".encode() + payload)
        reader.feed_eof()
        assert await python_editor_service.read_lsp_message(reader) == message

        class Writer:
            def __init__(self) -> None:
                self.value = b""

            def write(self, value: bytes) -> None:
                self.value += value

            async def drain(self) -> None:
                return None

        writer = Writer()
        await python_editor_service.write_lsp_message(writer, message)  # type: ignore[arg-type]
        assert writer.value.endswith(payload)
        assert writer.value.startswith(b"Content-Length:")

        with pytest.raises(ValueError, match="JSON object"):
            await python_editor_service.write_lsp_message(writer, "[]")  # type: ignore[arg-type]

    asyncio.run(exercise())
