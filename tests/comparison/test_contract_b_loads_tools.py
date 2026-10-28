import json
from pathlib import Path
from apps.api.services.comparison import create_adapter, Config


def test_b_loads_tool_jsons_and_endpoints_match():
    base = Path(__file__).resolve().parents[2] / "integrations" / "conexus" / "alphalab-research-agent" / "tools"
    p1 = base / "Project-Files.tool.json"
    p2 = base / "Project-Run.tool.json"
    assert p1.exists()
    assert p2.exists()
    t1 = json.loads(p1.read_text(encoding='utf-8'))
    t2 = json.loads(p2.read_text(encoding='utf-8'))
    assert t1.get('toolName') == 'alphalab_project_files'
    assert t2.get('toolName') == 'alphalab_project_run'
    adapter = create_adapter(Config.B)
    adapter.setup(type('T', (), {'task_id':'x','category':'y'})(), type('L', (), {})(), {}, True)
    eps = adapter.get_financial_entry_points()
    # must have common endpoints used by tools
    ep_set = set((e.get('endpoint'), e.get('method')) for e in eps)
    assert ('/api/backtests/jobs', 'POST') in ep_set
    assert ('/api/strategy/projects', 'GET') in ep_set
