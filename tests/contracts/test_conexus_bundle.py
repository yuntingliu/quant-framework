from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BUNDLE = ROOT / "integrations" / "conexus" / "alphalab-research-agent"
TOOLS = BUNDLE / "tools"


def _load(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict), path
    return value


def test_conexus_bundle_is_complete_and_contains_no_private_state():
    json_files = sorted(BUNDLE.rglob("*.json"))
    assert len(json_files) == 12

    forbidden = re.compile(
        r"(?:[A-Za-z]:\\Users\\|/Users/|PRIVATE KEY|RQ_PASSWORD=|"
        r"IBKR|ib_async|Interactive Brokers|RQ_SSH_|tunnel)",
        re.IGNORECASE,
    )
    for path in json_files:
        text = path.read_text(encoding="utf-8")
        _load(path)
        assert forbidden.search(text) is None, path

    assert all(
        path.relative_to(ROOT).parts[:3] == (
            "integrations",
            "conexus",
            "alphalab-research-agent",
        )
        for path in json_files
    )


def test_conexus_tools_match_barebone_profiles_and_guardrails():
    tools = {_load(path)["toolName"]: _load(path) for path in sorted(TOOLS.glob("*.json"))}
    assert set(tools) == {
        "alphalab_get_workspace_context",
        "alphalab_get_strategy",
        "alphalab_get_market_bars",
        "alphalab_get_backtest",
        "alphalab_run_backtest",
        "alphalab_generate_signal",
    }

    profile_tools = {
        "alphalab_get_workspace_context",
        "alphalab_get_market_bars",
        "alphalab_run_backtest",
        "alphalab_generate_signal",
    }
    for name, tool in tools.items():
        assert tool["runtime"] == "node"
        assert tool["permissions"] == {"network": "alphalab-api"}
        assert "shell" not in tool["code"].lower()
        if name in profile_tools:
            profile = tool["inputSchema"]["properties"]["profile"]
            assert profile == {
                "type": "string",
                "enum": ["demo", "runtime"],
                "default": "demo",
            }
            assert "profile" in tool["code"]

    assert tools["alphalab_run_backtest"]["sideEffects"] == "write"
    assert tools["alphalab_generate_signal"]["sideEffects"] == "write"
    assert all(
        tool["sideEffects"] == "read"
        for name, tool in tools.items()
        if name not in {"alphalab_run_backtest", "alphalab_generate_signal"}
    )


def test_conexus_registration_keeps_runtime_and_rq_environment_separate():
    register = (ROOT / "scripts" / "register_conexus_research_harness.mjs").read_text(
        encoding="utf-8"
    )
    start = (ROOT / "scripts" / "start_conexus_web.ps1").read_text(encoding="utf-8")

    assert "integrations/conexus/alphalab-research-agent" in register
    assert ".conexus" in register
    assert "workspace/harnesses" not in register
    assert "Join-Path $ProjectRoot '.env'" not in start
