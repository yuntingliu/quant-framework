"""The shared vocabulary for how far an Agent task got and why it stopped.

Pure functions only. Storage lives in ``agent_conversation_service`` and network
recovery in ``agent_run_service``.
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any

TERMINAL_RUN_STATES = frozenset({"completed", "blocked", "failed", "cancelled"})

# A Host cannot move a Run backwards, but a slow read can arrive after a newer one.
_STATUS_RANK = {"queued": 0, "running": 1}
_TERMINAL_RANK = 2


def status_rank(status: str | None) -> int:
    return _TERMINAL_RANK if status in TERMINAL_RUN_STATES else _STATUS_RANK.get(str(status), 0)


# Raised by this application when the Host no longer recognizes a Run.
RUN_RECORD_LOST = "run_record_lost"
RUN_RECORD_LOST_MESSAGE = (
    "The Agent Host no longer recognizes this task, so its progress cannot be confirmed "
    "and it will not be continued. Check the workspace and background jobs before sending "
    "the request again."
)

_RETRY_NEW_RUN = "new_run"
_RETRY_CHECK_FIRST = "check_first"
_RETRY_CHANGE_REQUEST = "change_request"
_RETRY_FIX_SETUP = "fix_setup"

_CATEGORIES: dict[str, tuple[str, str]] = {
    "cancelled": (_RETRY_NEW_RUN, "Cancelled before it finished; its workspace changes were discarded."),
    "host_interrupted": (
        _RETRY_NEW_RUN,
        "The Agent Host stopped during this task. Nothing was replayed and its workspace "
        "changes were discarded. Send the request again to start over.",
    ),
    "lost": (_RETRY_CHECK_FIRST, RUN_RECORD_LOST_MESSAGE),
    "timeout": (_RETRY_NEW_RUN, "The task exceeded its time limit. Narrow the request or send it again."),
    "provider": (
        _RETRY_NEW_RUN,
        "The model provider failed or returned an unusable response. Check the provider, then send the request again.",
    ),
    "configuration": (
        _RETRY_FIX_SETUP,
        "The Agent is not configured to run this. Check Model providers and the Agent release.",
    ),
    "input": (_RETRY_CHANGE_REQUEST, "The request could not be accepted as written. Change it and send it again."),
    "agent_blocked": (
        _RETRY_CHANGE_REQUEST,
        "The Agent could not complete this request and said why. Resolve what it reported, then ask again.",
    ),
    "internal": (_RETRY_CHECK_FIRST, "The task failed unexpectedly. Check its trace before sending it again."),
}

# Every code the pinned Conexus snapshot can raise is classified on purpose.
# tests/api/test_agent_lifecycle.py scans the snapshot, so an upgrade that adds a
# code fails until it is placed here.
_CODE_CATEGORIES: dict[str, str] = {
    "run_cancelled": "cancelled",
    "host_restarted": "host_interrupted",
    RUN_RECORD_LOST: "lost",
    "run_timed_out": "timeout",
    **dict.fromkeys(
        (
            "model_provider_error", "invalid_model_response", "invalid_payload", "missing_choices",
            "missing_message", "invalid_content", "invalid_tool_calls", "malformed_tool_call",
        ),
        "provider",
    ),
    **dict.fromkeys(
        (
            "model_not_allowed", "missing_model", "release_checksum_mismatch", "missing_tool_adapter",
            "missing_node_adapter", "missing_mcp_adapter", "missing_permission_adapter",
            "missing_isolation_adapter", "missing_dependency_adapter", "inline_tool_runtime_forbidden",
            "invalid_node_agent_route", "unsupported_agent_node", "unsupported_exposure_target",
            "exposure_target_not_found", "reserved_tool_name", "legacy_agent_tools_field",
            "invalid_agent_runtime_config", "duplicate_node_id", "unsupported_harness",
            "unsupported_agent_context",
        ),
        "configuration",
    ),
    "invalid_run_input": "input",
    **dict.fromkeys(
        (
            "local_run_failed", "local_host_error", "hosted_execution_failed", "invalid_host_tool_result",
            "recursive_tool_cycle", "recursive_node_cycle",
        ),
        "internal",
    ),
}


def classify_code(code: str | None) -> str:
    return _CODE_CATEGORIES.get(str(code), "internal")


def describe_stop(run: dict[str, Any]) -> dict[str, Any] | None:
    """Explain why a Run stopped. A Run that has not stopped, or succeeded, has no stop."""

    status = run.get("status")
    if status not in TERMINAL_RUN_STATES or status == "completed":
        return None
    error = run.get("error") if isinstance(run.get("error"), dict) else {}
    code = error.get("code")
    if status == "blocked":
        category = "agent_blocked"
    elif code:
        category = classify_code(code)
    elif status == "cancelled":
        # The Host cancels queued Runs without an error record, including on shutdown.
        category = "cancelled"
    else:
        category = "internal"
    retry, remediation = _CATEGORIES[category]
    return {
        "category": category,
        "code": str(code) if code else None,
        "retry": retry,
        "remediation": remediation,
        "resumable": False,
    }


def task_phase(run: dict[str, Any], *, delivered: bool = True) -> str:
    """Where the task is, as one word a UI and a benchmark can both rely on."""

    status = run.get("status")
    if status in TERMINAL_RUN_STATES:
        return str(status) if delivered else "delivering"
    if run.get("pendingInteraction"):
        return "awaiting_input"
    return "running" if status == "running" else "queued"


def elapsed_ms(start: str | None, end: str | None) -> int | None:
    if not start or not end:
        return None
    try:
        first = datetime.fromisoformat(str(start).replace("Z", "+00:00"))
        last = datetime.fromisoformat(str(end).replace("Z", "+00:00"))
        return max(0, int((last - first).total_seconds() * 1000))
    except (TypeError, ValueError):
        return None


_SECRET_PATTERNS = (
    (re.compile(r"(?i)\bbearer\s+[a-z0-9._~+/=-]+"), "Bearer <redacted>"),
    (re.compile(r"\bsk-[A-Za-z0-9_-]{8,}"), "<redacted>"),
    (re.compile(r"(?i)\b(api[_-]?key|token|password|secret)(\s*[=:]\s*)[^\s,;&\"']+"), r"\1\2<redacted>"),
    (re.compile(r"\b[a-fA-F0-9]{32,64}\b"), "<redacted>"),
    (re.compile(r"[A-Za-z]:\\[^\s\"']+"), "<internal-path>"),
    (re.compile(r"(^|\s)/(?:[^/\s]+/)+[^\s\"']*"), r"\1<internal-path>"),
)


def sanitize_text(value: Any, limit: int = 280) -> str:
    """Keep a diagnostic readable without keeping what must not be exported."""

    text = str(value or "").split("\n", 1)[0]
    for pattern, replacement in _SECRET_PATTERNS:
        text = pattern.sub(replacement, text)
    return text[:limit]


_COMMAND = re.compile(r"^[a-z][a-z_]*(?:\.[a-z_]+)*$")


def _tool_call_entry(call: Any) -> dict[str, Any] | None:
    if not isinstance(call, dict):
        return None
    function = call.get("function") if isinstance(call.get("function"), dict) else {}
    entry: dict[str, Any] = {"callId": str(call.get("id") or ""), "name": str(function.get("name") or "")}
    try:
        arguments = json.loads(function.get("arguments") or "{}")
    except (TypeError, ValueError):
        return entry
    # The operation name is safe to keep and is what correlates a call to its effect.
    # Arguments are not: they can hold research source and report content.
    if isinstance(arguments, dict):
        nested = arguments.get("input") if isinstance(arguments.get("input"), dict) else arguments
        command = nested.get("command")
        if isinstance(command, str) and _COMMAND.match(command):
            entry["command"] = command
        node_id = arguments.get("node_id")
        if isinstance(node_id, str) and len(node_id) <= 200:
            entry["nodeId"] = node_id
    return entry


def trace_entry(event: dict[str, Any]) -> tuple[str, dict[str, Any]] | None:
    """Reduce one Host event to the sanitized record kept for it."""

    event_type = str(event.get("type") or "")
    if event_type == "run.message":
        message = event.get("message") if isinstance(event.get("message"), dict) else {}
        if message.get("role") == "assistant":
            calls = [item for item in map(_tool_call_entry, message.get("tool_calls") or []) if item]
            return "model.turn", {"contentChars": len(str(message.get("content") or "")), "toolCalls": calls}
        if message.get("role") == "tool":
            detail: dict[str, Any] = {"callId": str(message.get("tool_call_id") or ""), "name": str(message.get("name") or "")}
            try:
                result = json.loads(message.get("content") or "{}")
            except (TypeError, ValueError):
                result = {}
            if isinstance(result, dict):
                if isinstance(result.get("success"), bool):
                    detail["success"] = result["success"]
                if result.get("success") is False and result.get("message"):
                    detail["message"] = sanitize_text(result["message"])
            return "tool.result", detail
        return None
    if event_type.startswith("run.interaction_"):
        interaction = event.get("interaction") if isinstance(event.get("interaction"), dict) else {}
        # The question and its answer are the user's research content, not diagnostics.
        return event_type, {"interactionId": str(interaction.get("id") or "")}
    if event_type.startswith("run."):
        return event_type, {"status": str(event.get("status") or "")}
    return None


__all__ = [
    "RUN_RECORD_LOST",
    "RUN_RECORD_LOST_MESSAGE",
    "TERMINAL_RUN_STATES",
    "classify_code",
    "describe_stop",
    "elapsed_ms",
    "sanitize_text",
    "status_rank",
    "task_phase",
    "trace_entry",
]
