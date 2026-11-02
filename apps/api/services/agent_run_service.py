"""Recover published Runs and deliver chat replies independently of browser lifetime."""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import time
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import quote

import httpx

from alphalab.utils.paths import RUNTIME_DIR
from apps.api.services.agent_conversation_service import (
    LOST_RUN_READS,
    TERMINAL_RUN_STATES,
    AgentConversationStore,
)
from apps.api.services.agent_lifecycle import describe_stop, elapsed_ms, task_phase
from apps.api.services.workspace_output_service import validate_workspace_delivery

logger = logging.getLogger(__name__)

AGENT_RUN_HEADER = "x-alphalab-agent-run"
# Answers that mean the Host does not know this Run, rather than that it is unreachable.
RUN_UNKNOWN_STATUSES = frozenset({401, 403, 404, 410})
EVENT_DRAIN_SECONDS = 1.0
TRACE_RETENTION_DEFAULT_DAYS = 30
_PRUNE_INTERVAL_SECONDS = 3600.0
_last_prune = 0.0


def host_submission_key(key: str) -> str:
    """Conversation identifiers are client text; the Host only needs a stable opaque key."""
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def web_origin() -> str:
    return os.getenv("CONEXUS_WEB_ORIGIN", "http://127.0.0.1:3000").rstrip("/")


def publication_slug() -> str:
    value = os.getenv("CONEXUS_PUBLICATION_SLUG", "alphalab-research-agent").strip().lower()
    return value if re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", value) else "alphalab-research-agent"


def run_scope() -> str:
    # A local Host chooses a new port on restart, but owns the same durable workspace.
    origin = "local" if os.getenv("ALPHALAB_AGENT_MODE") == "local" else web_origin()
    return f"{origin}/{publication_slug()}"


def conversation_store() -> AgentConversationStore:
    configured = os.getenv("ALPHALAB_RUNTIME_DIR", "").strip()
    root = Path(configured).expanduser() if configured else RUNTIME_DIR
    return AgentConversationStore(root / "app" / "agent-conversations.sqlite3")


def run_headers(run_id: str) -> dict[str, str]:
    row = conversation_store().get_run(run_scope(), run_id)
    return {"authorization": f"Bearer {row['access_token']}"} if row else {}


def _delivery(run: dict, workspace: dict) -> tuple[list[dict], dict | None, dict]:
    changes = run.get("nodeChanges") or {}
    changed = set(changes.get("created", [])) | set(changes.get("updated", []))
    artifacts = []
    superseded: list[str] = []
    omitted: list[str] = []
    checkpoint = {"version": 1, "runId": run["id"], "updatedAt": run.get("completedAt") or run["createdAt"]}
    remaining = 1_000_000
    for node in workspace.get("nodes", []):
        if node.get("id") not in changed:
            continue
        if node.get("updatedByRunId") != run["id"]:
            superseded.append(node["id"])
            continue
        values = node.get("values") or {}
        common = {
            "id": f"{run['id']}:{node['id']}", "runId": run["id"], "title": node.get("label", ""),
            "createdAt": checkpoint["updatedAt"], "producerNodeId": node["id"],
        }
        if node.get("type") in {"note", "document"} and node.get("description") == "AlphaLab quantitative report" and isinstance(values.get("content"), str):
            artifact = {**common, "kind": "document", "outputKey": "reportDocument", "content": {"markdown": values["content"]}}
        else:
            artifact = {**common, "kind": "node", "content": {"node": {
                key: node[key] for key in ("id", "type", "label", "description", "values") if key in node
            }}}
        size = len(json.dumps(artifact, ensure_ascii=False).encode("utf-8"))
        if size <= remaining and len(artifacts) < 40:
            artifacts.append(artifact)
            remaining -= size
        else:
            omitted.append(node["id"])
        key = {
            "alphalab-decision-notebook-v1": "decisionNotebook",
            "alphalab-workspace-result-v1": "workspaceResult",
        }.get(node["id"])
        data = values.get("data")
        if key and isinstance(data, dict):
            if "customType" in data and isinstance(data.get("data"), dict):
                data = data["data"]
            if len(json.dumps(data, ensure_ascii=False).encode("utf-8")) <= 60_000:
                checkpoint[key] = data
    # Record what could not be attached, so "delivered" never silently means "partly delivered".
    present = {node.get("id") for node in workspace.get("nodes", [])}
    reconciliation = {
        "expected": sorted(changed),
        "delivered": sorted(item["producerNodeId"] for item in artifacts),
        "superseded": sorted(superseded),
        "omittedBySizeLimit": sorted(omitted),
        "missingFromWorkspace": sorted(item for item in changed if item not in present),
    }
    return artifacts, checkpoint if len(checkpoint) > 3 else None, reconciliation


async def record_snapshot(run: dict, client: httpx.AsyncClient | None = None) -> None:
    store = conversation_store()
    scope = run_scope()
    row = store.get_run(scope, run["id"])
    if row is None:
        return
    changes = run.get("nodeChanges") or {}
    has_artifacts = bool(changes.get("created") or changes.get("updated"))
    terminal = run["status"] in TERMINAL_RUN_STATES
    # Commit the reply first: a transient workspace outage must not lose the text.
    store.record_run(scope, run, delivered=terminal and not has_artifacts)
    if not terminal or row["delivered"] or not has_artifacts:
        return
    if client is None:
        async with httpx.AsyncClient(timeout=5, trust_env=False) as owned:
            await record_snapshot(run, owned)
        return
    try:
        response = await client.get(
            f"{web_origin()}/api/public/harnesses/{quote(publication_slug(), safe='')}/workspace",
            headers={"Authorization": f"Bearer {os.getenv('CONEXUS_PUBLICATION_WORKSPACE_TOKEN', '').strip()}"},
        )
        response.raise_for_status()
        workspace = validate_workspace_delivery(response.json()["workspace"])
        artifacts, checkpoint, reconciliation = _delivery(run, workspace)
        store.record_run(
            scope, run, artifacts=artifacts, checkpoint=checkpoint, delivered=True, reconciliation=reconciliation,
        )
    except (httpx.HTTPError, ValueError, KeyError):
        # Keep the durable delivery pending so the background worker can enrich it later.
        logger.warning("Agent reply saved; workspace artifacts will be retried")
        store.add_trace(scope, run["id"], "delivery.retrying")


def workspace_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {os.getenv('CONEXUS_PUBLICATION_WORKSPACE_TOKEN', '').strip()}"}


async def resolve_submission(key: str, client: httpx.AsyncClient | None = None) -> str:
    """Settle an attempt whose outcome is unknown, without ever repeating it blindly.

    Returns ``accepted`` when the Host has the Run, ``not_started`` when the Host can
    say it never saw the request, and ``unconfirmed`` when nobody can tell.
    """

    store = conversation_store()
    scope = run_scope()
    row = store.get_submission(scope, key)
    if row is None or row["state"] == "accepted":
        return "accepted" if row else "not_started"
    if os.getenv("ALPHALAB_AGENT_MODE") != "local":
        # Only the bundled local Host answers this question.
        return "unconfirmed"
    if client is None:
        async with httpx.AsyncClient(timeout=5, trust_env=False) as owned:
            return await resolve_submission(key, owned)
    try:
        response = await client.get(
            f"{web_origin()}/api/alphalab/submissions/{host_submission_key(key)}", headers=workspace_headers(),
        )
        response.raise_for_status()
        answer = response.json()
        if answer.get("known") is True and answer.get("state") == "accepted":
            run, token = answer["run"], answer["accessToken"]
        elif answer.get("known") is False:
            store.mark_submission(scope, key, "not_started")
            return "not_started"
        else:
            return "unconfirmed"
    except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError):
        return "unconfirmed"
    conversation = json.loads(row["conversation_json"] or "{}")
    if not conversation:
        return "unconfirmed"
    # The Run has been executing since the Host accepted it, so re-stamp the user turn with it.
    for message in conversation.get("messages", []):
        message["runId"] = run["id"]
    store.attach_run(scope, run, token, conversation, submission_key=key)
    await record_snapshot(run, client)
    return "accepted"


def _sse_blocks(buffer: str) -> tuple[list[dict], str]:
    events = []
    while "\n\n" in buffer:
        block, buffer = buffer.split("\n\n", 1)
        data = "\n".join(line[5:].lstrip() for line in block.split("\n") if line.startswith("data:"))
        if not data:
            continue
        try:
            value = json.loads(data)
        except ValueError:
            continue
        if isinstance(value, dict):
            events.append(value)
    return events, buffer


async def drain_events(row: dict, client: httpx.AsyncClient, *, terminal: bool) -> None:
    """Copy new Host events into the trace, resuming from the stored cursor.

    Independent of any browser. A stream that closes early or times out loses
    nothing: the next pass resumes from the last sequence that was committed.
    """

    store = conversation_store()
    scope = run_scope()
    run_id = row["run_id"]

    async def read() -> bool:
        headers = {
            "Accept": "text/event-stream", "Authorization": f"Bearer {row['access_token']}",
            "Last-Event-ID": str(row.get("event_cursor") or 0),
        }
        url = f"{web_origin()}/api/public/runs/{quote(run_id, safe='')}/events"
        async with client.stream("GET", url, headers=headers) as response:
            if response.status_code != 200:
                return response.status_code in RUN_UNKNOWN_STATUSES
            buffer = ""
            async for chunk in response.aiter_text():
                events, buffer = _sse_blocks(f"{buffer}{chunk}".replace("\r\n", "\n"))
                if events and store.record_events(scope, run_id, events):
                    return False
        return True

    try:
        ended_without_terminal = await asyncio.wait_for(read(), timeout=EVENT_DRAIN_SECONDS)
    except (asyncio.TimeoutError, httpx.HTTPError):
        return
    if ended_without_terminal and terminal:
        # A finished Run has no more events to offer, so a missing terminal event stays missing.
        store.close_trace(scope, run_id, incomplete_reason="host_events_unavailable")


def mark_run_lost(run_id: str) -> dict | None:
    store = conversation_store()
    scope = run_scope()
    snapshot = store.lost_run_snapshot(scope, run_id)
    if snapshot is None:
        return None
    store.record_run(scope, snapshot, delivered=True)
    store.add_trace(scope, run_id, "run.lost", {"reads": LOST_RUN_READS})
    return snapshot


def _prune_due() -> None:
    global _last_prune
    moment = time.monotonic()
    if _last_prune and moment - _last_prune < _PRUNE_INTERVAL_SECONDS:
        return
    _last_prune = moment
    configured = os.getenv("ALPHALAB_AGENT_TRACE_RETENTION_DAYS", "").strip()
    days = int(configured) if configured.isdigit() and int(configured) > 0 else TRACE_RETENTION_DEFAULT_DAYS
    conversation_store().prune_records(run_scope(), retention_days=days)


async def reconcile_runs() -> None:
    if os.getenv("ALPHALAB_AGENT_MODE") == "off":
        return
    store = conversation_store()
    scope = run_scope()
    _prune_due()
    rows = store.runs_to_reconcile(scope)
    unresolved = store.unresolved_submissions(scope)
    if not rows and not unresolved:
        return
    semaphore = asyncio.Semaphore(4)
    async with httpx.AsyncClient(timeout=5, trust_env=False) as client:
        async def settle(row):
            async with semaphore:
                await resolve_submission(row["submission_key"], client)

        async def reconcile(row):
            async with semaphore:
                try:
                    snapshot = json.loads(row["snapshot_json"])
                    if snapshot["status"] not in TERMINAL_RUN_STATES:
                        response = await client.get(
                            f"{web_origin()}/api/public/runs/{quote(row['run_id'], safe='')}",
                            headers={"Authorization": f"Bearer {row['access_token']}"},
                        )
                        if response.status_code in RUN_UNKNOWN_STATUSES:
                            # Declared only after repeated answers, because this cannot be undone.
                            if store.note_missing_read(scope, row["run_id"]) >= LOST_RUN_READS:
                                mark_run_lost(row["run_id"])
                                store.close_trace(scope, row["run_id"], incomplete_reason="run_record_lost")
                            return
                        response.raise_for_status()
                        store.reset_missing_reads(scope, row["run_id"])
                        snapshot = response.json()["run"]
                    if not row["delivered"]:
                        await record_snapshot(snapshot, client)
                    if row["trace_open"]:
                        await drain_events(row, client, terminal=snapshot["status"] in TERMINAL_RUN_STATES)
                except (httpx.HTTPError, ValueError, KeyError):
                    # Availability failures never turn an active Run into a made-up terminal state.
                    pass
        await asyncio.gather(*(settle(row) for row in unresolved), *(reconcile(row) for row in rows))


def _job_state(job: dict) -> dict:
    kind = job["kind"]
    try:
        if kind == "backtest":
            from apps.api.services.backtest_job_service import get_backtest_job

            current = get_backtest_job(job["id"])
        else:
            from apps.api.services import data_sync_service

            current = data_sync_service.job(job["id"])
    except Exception:  # A job store outage must not take the task's own record down with it.
        logger.warning("Could not read a linked background job", exc_info=False)
        current = None
    public = {"kind": kind, "id": job["id"], "owned": job["owned"], "status": (current or {}).get("status", "unknown")}
    if current and current.get("backtest_id"):
        public["backtestId"] = current["backtest_id"]
    return public


JOB_ACTIVE_STATES = frozenset({"queued", "running", "unknown"})


def link_job(run_id: str | None, kind: str, job: dict | None) -> None:
    """Record which Run asked for this job. An absent or unknown Run links nothing."""

    job_id = (job or {}).get("id") or (job or {}).get("job_id")
    if run_id and job_id:
        conversation_store().link_job(run_scope(), run_id, kind, str(job_id), owned=not job.get("deduplicated", False))


def cancel_run_jobs(run_id: str) -> list[dict]:
    """Stop queued work this Run owns and report honestly on the rest.

    A running backtest cannot be interrupted safely, and a job shared with another
    requester is not this Run's to stop. Both are reported, never hidden.
    """

    store = conversation_store()
    scope = run_scope()
    outcomes = []
    for job in store.run_jobs(scope, run_id):
        state = _job_state(job)
        outcome = "already_finished"
        if state["status"] in JOB_ACTIVE_STATES:
            outcome = "still_running"
            if job["owned"]:
                outcome = _cancel_job(job, state["status"])
        outcomes.append({**state, "outcome": outcome})
        store.add_trace(
            scope, run_id, "job.cancellation", {"kind": job["kind"], "jobId": job["id"], "outcome": outcome},
            dedupe=f"job-cancel:{job['kind']}:{job['id']}:{outcome}",
        )
    return outcomes


def _cancel_job(job: dict, status: str) -> str:
    if job["kind"] == "backtest":
        from apps.api.services.backtest_job_service import cancel_queued_backtest_job

        return "cancelled" if status == "queued" and cancel_queued_backtest_job(job["id"]) else "still_running"
    from apps.api.services import data_sync_service

    current = data_sync_service.cancel(job["id"]) or {}
    # Sync cancellation is cooperative: a running job stops at its next batch.
    return "cancelled" if current.get("status") == "cancelled" else "cancelling"


def run_lifecycle(run: dict) -> dict | None:
    """The authoritative progress record a browser renders. Holds no credentials or content."""

    store = conversation_store()
    scope = run_scope()
    row = store.get_run(scope, run.get("id", ""))
    if row is None:
        return None
    jobs = [_job_state(job) for job in store.run_jobs(scope, row["run_id"])]
    delivered = bool(row["delivered"])
    return {
        "phase": task_phase(run, delivered=delivered),
        "stop": describe_stop(run),
        "delivery": "delivered" if delivered else "pending",
        "jobs": jobs,
        "activeJobs": sum(1 for job in jobs if job["status"] in JOB_ACTIVE_STATES),
    }


def decorate_run(run: dict) -> dict:
    lifecycle = run_lifecycle(run)
    return {**run, "lifecycle": lifecycle} if lifecycle else run


def stored_run(run_id: str) -> dict | None:
    row = conversation_store().get_run(run_scope(), run_id)
    return json.loads(row["snapshot_json"]) if row else None


def run_trace(run_id: str) -> dict | None:
    """One sanitized account of a task, from the submitted message to its delivered outputs."""

    store = conversation_store()
    scope = run_scope()
    row = store.get_run(scope, run_id)
    if row is None:
        return None
    run = json.loads(row["snapshot_json"])
    entries = store.trace_entries(scope, run_id)
    submission = store.submission_for_run(scope, run_id) or {}
    return {
        "version": 1,
        "runId": run_id,
        "conversationId": row["conversation_id"],
        "messageId": submission.get("message_id"),
        "status": run.get("status"),
        "phase": task_phase(run, delivered=bool(row["delivered"])),
        "stop": describe_stop(run),
        "timing": {
            "createdAt": run.get("createdAt"), "startedAt": run.get("startedAt"),
            "completedAt": run.get("completedAt"),
            "elapsedMs": elapsed_ms(run.get("createdAt"), run.get("completedAt")),
            "provenance": "host_timestamps",
        },
        "usage": {
            "available": False,
            "modelTurns": sum(1 for entry in entries if entry["kind"] == "model.turn"),
            "provenance": "The pinned Host discards provider token usage, so only model turns are counted.",
        },
        "tools": _tool_calls(entries),
        "jobs": [_job_state(job) for job in store.run_jobs(scope, run_id)],
        "delivery": {
            "state": "delivered" if row["delivered"] else "pending",
            **next((entry["detail"] for entry in entries if entry["kind"] == "delivery.reconciled"), {}),
        },
        "complete": not row["trace_open"] and not any(
            entry["kind"] in {"events.gap", "trace.truncated", "trace.incomplete"} for entry in entries
        ),
        "entries": entries,
    }


def _tool_calls(entries: list[dict]) -> list[dict]:
    """Pair each requested tool call with its result. Calls run one at a time, so the
    previous record's timestamp is when a call started."""

    calls: dict[str, dict] = {}
    previous_at = None
    for entry in entries:
        detail = entry["detail"]
        if entry["kind"] == "model.turn":
            for call in detail.get("toolCalls", []):
                calls.setdefault(call["callId"], {**call, "state": "requested", "requestedAt": entry["at"]})
        elif entry["kind"] == "tool.result":
            call = calls.setdefault(detail.get("callId", ""), {"callId": detail.get("callId", ""), "name": detail.get("name", "")})
            call.update(
                state="finished", finishedAt=entry["at"], elapsedMs=elapsed_ms(previous_at, entry["at"]),
                **{key: detail[key] for key in ("success", "message") if key in detail},
            )
        previous_at = entry["at"]
    return list(calls.values())


@asynccontextmanager
async def run_recovery_lifespan(_app):
    async def recover():
        while True:
            try:
                await reconcile_runs()
            except Exception:
                logger.warning("Agent conversation recovery will retry", exc_info=False)
            await asyncio.sleep(2)

    worker = asyncio.create_task(recover())
    try:
        yield
    finally:
        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)
