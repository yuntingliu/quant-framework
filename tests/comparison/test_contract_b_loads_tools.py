import json

import pytest

from apps.api.services.comparison import Config, create_adapter
from apps.api.services.comparison.adapters import OpenCodeAdapter, extract_tool_endpoints

# Parsed from the request definitions in the two Conexus tool JSONs. If a tool changes the
# AlphaLab endpoints it calls, this list (and therefore this test) must change with it.
EXPECTED_ENDPOINTS = {
    ("GET", "/api/agent/context"),
    ("POST", "/api/agent/data-tools/{arg}/invoke"),
    ("POST", "/api/backtests/batches"),
    ("POST", "/api/backtests/compare"),
    ("POST", "/api/backtests/jobs"),
    ("GET", "/api/backtests/jobs/{arg}"),
    ("GET", "/api/backtests/jobs/{arg}/wait"),
    ("GET", "/api/backtests/{arg}/events"),
    ("GET", "/api/backtests/{arg}/summary"),
    ("GET", "/api/backtests/{arg}/{arg}"),
    ("POST", "/api/conexus/outputs/prepare"),
    ("GET", "/api/data-sync/jobs"),
    ("GET", "/api/data-sync/jobs/{arg}"),
    ("POST", "/api/data-sync/jobs/{arg}/cancel"),
    ("GET", "/api/data-sync/recipes/{arg}"),
    ("PUT", "/api/data-sync/recipes/{arg}"),
    ("POST", "/api/data-sync/recipes/{arg}/jobs"),
    ("POST", "/api/data-sync/recipes/{arg}/plan"),
    ("GET", "/api/data/factors/returns"),
    ("GET", "/api/data/fundamentals"),
    ("GET", "/api/data/market/bars"),
    ("POST", "/api/market/custom-risk-factor/evaluate"),
    ("GET", "/api/strategy/factor-templates"),
    ("GET", "/api/strategy/projects"),
    ("POST", "/api/strategy/projects"),
    ("DELETE", "/api/strategy/projects/{arg}"),
    ("GET", "/api/strategy/projects/{arg}"),
    ("POST", "/api/strategy/projects/{arg}/default-migration"),
    ("PUT", "/api/strategy/projects/{arg}/draft"),
    ("POST", "/api/strategy/projects/{arg}/edits"),
    ("GET", "/api/strategy/projects/{arg}/entrypoints/{arg}/source"),
    ("POST", "/api/strategy/projects/{arg}/factor-templates/{arg}"),
    ("POST", "/api/strategy/projects/{arg}/factors"),
    ("POST", "/api/strategy/projects/{arg}/factors/{arg}/research"),
    ("POST", "/api/strategy/projects/{arg}/factors/{arg}/{arg}"),
    ("PUT", "/api/strategy/projects/{arg}/metadata"),
    ("POST", "/api/strategy/projects/{arg}/preview"),
    ("POST", "/api/strategy/projects/{arg}/strategies"),
    ("PUT", "/api/strategy/projects/{arg}/strategies/{arg}/name"),
    ("GET", "/api/validation/projects/{arg}"),
    ("PUT", "/api/validation/projects/{arg}"),
}


def as_pairs(entry_points):
    return {(entry["method"], entry["endpoint"]) for entry in entry_points}


def test_tool_files_exist_and_declare_the_expected_tools(tool_files):
    assert tool_files["Project-Files.tool.json"].is_file()
    assert tool_files["Project-Run.tool.json"].is_file()
    files = json.loads(tool_files["Project-Files.tool.json"].read_text(encoding="utf-8"))
    run = json.loads(tool_files["Project-Run.tool.json"].read_text(encoding="utf-8"))
    assert files["toolName"] == "alphalab_project_files"
    assert run["toolName"] == "alphalab_project_run"


def test_parser_matches_the_real_request_definitions():
    parsed = as_pairs(extract_tool_endpoints())

    assert parsed == EXPECTED_ENDPOINTS, (
        f"missing: {sorted(EXPECTED_ENDPOINTS - parsed)}; unexpected: {sorted(parsed - EXPECTED_ENDPOINTS)}"
    )
    assert all(endpoint.startswith("/api/") for _method, endpoint in parsed)
    assert {method for method, _endpoint in parsed} <= {"GET", "POST", "PUT", "PATCH", "DELETE"}


def test_config_b_uses_exactly_the_endpoints_from_the_source_tools(sample_task, dummy_limits):
    adapter = create_adapter(Config.B)
    adapter.setup(sample_task, dummy_limits, {}, use_doubles=True)

    assert as_pairs(adapter.get_financial_entry_points()) == as_pairs(extract_tool_endpoints())
    assert adapter.get_capabilities()["loads_tool_jsons"] is True


def test_config_c_uses_the_same_source_tool_endpoints(sample_task, dummy_limits):
    adapter = create_adapter(Config.C)
    adapter.setup(sample_task, dummy_limits, {}, use_doubles=True)

    assert as_pairs(adapter.get_financial_entry_points()) == as_pairs(extract_tool_endpoints())
    assert as_pairs(adapter.get_financial_entry_points()) == EXPECTED_ENDPOINTS


def test_config_a_exposes_no_financial_entry_points(sample_task, dummy_limits):
    adapter = create_adapter(Config.A)
    adapter.setup(sample_task, dummy_limits, {}, use_doubles=True)

    assert adapter.get_financial_entry_points() == []
    assert adapter.get_capabilities()["has_financial_tools"] is False


def test_parsing_a_missing_tool_file_fails_loudly(tmp_path, sample_task, dummy_limits):
    adapter = OpenCodeAdapter(Config.B, base_dir=tmp_path)

    with pytest.raises(FileNotFoundError):
        adapter.setup(sample_task, dummy_limits, {}, use_doubles=True)


def test_config_d_is_not_implemented():
    with pytest.raises(ValueError):
        create_adapter(Config.D)


def test_parser_ignores_unrelated_string_literals(tmp_path):
    tool = tmp_path / "Project-Files.tool.json"
    tool.write_text(json.dumps({"toolName": "x", "code": "const notARequest = '/api/nope';"}), encoding="utf-8")

    assert extract_tool_endpoints(("Project-Files.tool.json",), base_dir=tmp_path) == []


def test_parser_resolves_action_alternatives(tmp_path):
    code = "await call('/api/backtests/jobs/'+id+(command==='a'?'/wait':'/status'),{method:'PUT'})"
    tool = tmp_path / "Project-Run.tool.json"
    tool.write_text(json.dumps({"toolName": "x", "code": code}), encoding="utf-8")

    parsed = as_pairs(extract_tool_endpoints(("Project-Run.tool.json",), base_dir=tmp_path))

    assert parsed == {
        ("PUT", "/api/backtests/jobs/{arg}/wait"),
        ("PUT", "/api/backtests/jobs/{arg}/status"),
    }
