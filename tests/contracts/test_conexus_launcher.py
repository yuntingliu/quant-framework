"""Exercise the launcher in child processes without starting a Conexus service."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SHELL = shutil.which("pwsh") or shutil.which("powershell")
pytestmark = pytest.mark.skipif(not SHELL or not shutil.which("node"), reason="PowerShell and Node required")


def _launch(tmp_path, *, layout="workspace", token="test-admin-credential-12345", env_file=False):
    root = tmp_path / "Conexus"
    entry, dist = {
        "workspace": ("apps/web/server-dist/apps/web/server/entrypoints/web-host.js", "apps/web/dist"),
        "legacy": ("backend/dist/web-host.js", "dist/web"),
    }[layout]
    for name, content in [(entry, 'throw new Error("Check must not execute the server");'), (f"{dist}/index.html", "fixture")]:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    env = {key: value for key, value in os.environ.items() if not key.upper().startswith("CONEXUS_")}
    command = [
        str(SHELL), "-NoProfile", "-ExecutionPolicy", "Bypass",
        "-File", str(ROOT / "scripts/start_conexus_web.ps1"),
        "-ConexusRoot", str(root), "-ProjectRoot", str(tmp_path), "-Check",
    ]
    if env_file:
        path = tmp_path / "conexus.env"
        path.write_text(f"CONEXUS_WEB_TOKEN={json.dumps(token)}\n", encoding="utf-8")
        command += ["-EnvFile", str(path)]
    elif token:
        env["CONEXUS_WEB_TOKEN"] = token
    return subprocess.run(command, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)


@pytest.mark.parametrize("layout", ["workspace", "legacy"])
@pytest.mark.parametrize("env_file", [False, True])
def test_launcher_checks_supported_layout_without_running_or_leaking_token(tmp_path, layout, env_file):
    result = _launch(tmp_path, layout=layout, env_file=env_file)
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["layout"] == layout
    assert payload["check"] == "files_and_configuration_only"
    assert payload["administratorTokenConfigured"] is True
    assert "test-admin-credential-12345" not in result.stdout + result.stderr
    assert not (tmp_path / "Conexus/.conexus-web").exists()


@pytest.mark.parametrize("token", [None, "too-short", "not-a-valid-token!with-symbol"])
def test_launcher_rejects_missing_or_invalid_admin_configuration(tmp_path, token):
    result = _launch(tmp_path, token=token)
    assert result.returncode != 0
    assert "Set a persistent CONEXUS_WEB_TOKEN" in result.stderr


def test_legacy_host_helper_explains_incompatible_checkout_without_writing(tmp_path):
    env = {key: value for key, value in os.environ.items() if not key.upper().startswith(("CONEXUS_", "ALPHALAB_"))}
    env.update(CONEXUS_ROOT=str(tmp_path), CONEXUS_PROJECT_ROOT=str(tmp_path))
    result = subprocess.run(
        ["node", str(ROOT / "scripts/host_conexus_research_harness.mjs"), "--check"],
        env=env, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=15,
    )
    assert result.returncode != 0
    assert "incompatible with the legacy hosting helper" in result.stderr
    assert list(tmp_path.iterdir()) == []
