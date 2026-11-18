"""Static safeguards for opt-in paid reviewer smoke tests.

Actual model quality requires an explicitly enabled live run.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / 'scripts/run_member2_review.mjs'


def test_live_requires_explicit_paid_opt_in():
    code = RUNNER.read_text(encoding='utf-8')
    assert "args.includes('--confirm-paid')" in code
    assert "request.identity.role === 'reviewer' && live" in code
    assert 'pinConfiguredModelProvider' in code
    assert 'provider.complete(request)' in code


def test_blocked_completion_has_blocker():
    code = RUNNER.read_text(encoding='utf-8')
    assert "status === 'blocked' ? { blocker:" in code


def test_scripted_permission_probe_available():
    code = RUNNER.read_text(encoding='utf-8')
    assert "args.includes('--probe-reviewer-write')" in code
    assert "makeCall('edit', { operations: [] })" in code

def test_production_runner_has_bounded_execution():
    production_runner = (
        ROOT / "scripts/run_member2_production_review.mjs"
    )

    code = production_runner.read_text(encoding="utf-8")

    assert "diagnosticMaxIterations: 12" in code
    assert "diagnosticModelInput: true" in code

    assert "max_model_calls: 10" in code
    assert "max_tool_calls: 18" in code
    assert "wall_time_ms: 90000" in code
    assert "max_output_tokens: 2048" in code

    assert "args.includes('--confirm-paid')" in code
    assert "publication_performed: false" in code