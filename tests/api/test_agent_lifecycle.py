from __future__ import annotations

import re
from pathlib import Path

import pytest

from apps.api.services.agent_lifecycle import (
    _CATEGORIES,
    _CODE_CATEGORIES,
    describe_stop,
    sanitize_text,
    task_phase,
    trace_entry,
)

ROOT = Path(__file__).resolve().parents[2]
CORE = [ROOT / "vendor/conexus/packages/runtime-core/src", ROOT / "vendor/conexus/apps/local-host/src"]
RAISED = re.compile(r"HostedRuntimeError\((?:[^()]|\([^()]*\))*?,\s*['\"]([a-z_]+)['\"]\s*,?\s*\)", re.S)


def core_sources():
    for root in CORE:
        for path in root.rglob("*"):
            if path.suffix in {".ts", ".mjs"} and ".test." not in path.name:
                yield path.read_text(encoding="utf-8")


def test_every_code_the_pinned_core_raises_has_a_deliberate_category():
    raised = {code for source in core_sources() for code in RAISED.findall(source)}
    assert raised, "the scan found nothing, so it is no longer checking anything"
    assert not raised - set(_CODE_CATEGORIES), "classify these before upgrading the core"


def test_no_classified_code_is_invented():
    text = "\n".join(core_sources())
    unknown = {code for code in _CODE_CATEGORIES if f"'{code}'" not in text and f'"{code}"' not in text}
    assert unknown == {"run_record_lost"}, "only the code this application raises itself may be absent from the core"


def test_every_category_has_remediation_and_never_promises_resumption():
    for category in set(_CODE_CATEGORIES.values()) | {"agent_blocked"}:
        retry, remediation = _CATEGORIES[category]
        assert retry and remediation


@pytest.mark.parametrize(("run", "category"), [
    ({"status": "cancelled", "error": {"code": "run_cancelled"}}, "cancelled"),
    ({"status": "cancelled"}, "cancelled"),
    ({"status": "cancelled", "error": {"code": "host_restarted"}}, "host_interrupted"),
    ({"status": "failed", "error": {"code": "model_provider_error"}}, "provider"),
    ({"status": "failed", "error": {"code": "something_new"}}, "internal"),
    ({"status": "failed"}, "internal"),
    ({"status": "blocked"}, "agent_blocked"),
])
def test_stop_category(run, category):
    stop = describe_stop(run)
    assert stop["category"] == category and stop["resumable"] is False


@pytest.mark.parametrize("status", ["queued", "running", "completed"])
def test_active_and_successful_runs_have_no_stop(status):
    assert describe_stop({"status": status}) is None


@pytest.mark.parametrize(("run", "delivered", "phase"), [
    ({"status": "queued"}, False, "queued"),
    ({"status": "running"}, False, "running"),
    ({"status": "running", "pendingInteraction": {"id": "q"}}, False, "awaiting_input"),
    ({"status": "completed"}, False, "delivering"),
    ({"status": "completed"}, True, "completed"),
    ({"status": "failed"}, True, "failed"),
])
def test_phase(run, delivered, phase):
    assert task_phase(run, delivered=delivered) == phase


def test_sanitize_removes_what_must_not_be_exported():
    cleaned = sanitize_text("C:\\Users\\a\\x.db /home/a/b.db Bearer abc.def sk-abcdefgh123 password=hunter2 " + "f" * 64 + " ok\nsecond line")
    for forbidden in ("Users", "/home", "abc.def", "sk-abc", "hunter2", "f" * 64, "second line"):
        assert forbidden not in cleaned
    assert cleaned.endswith("ok") and len(sanitize_text("x" * 999)) == 280


def test_trace_entry_keeps_the_operation_but_not_its_arguments():
    kind, detail = trace_entry({"type": "run.message", "message": {"role": "assistant", "content": "secret", "tool_calls": [
        {"id": "1", "function": {"name": "use", "arguments": '{"node_id":"n","input":{"command":"files.write","args":{"content":"secret"}}}'}},
        {"id": "2", "function": {"name": "use", "arguments": "{not json"}},
    ]}})
    assert kind == "model.turn" and "secret" not in str(detail)
    assert detail["toolCalls"][0] == {"callId": "1", "name": "use", "command": "files.write", "nodeId": "n"}
    assert detail["contentChars"] == 6 and detail["toolCalls"][1] == {"callId": "2", "name": "use"}
