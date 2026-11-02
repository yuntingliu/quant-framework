"""Fault replay for Agent task lifecycle: every case drives one failure through the real routes.

The Host is simulated at the HTTP boundary because these failures are about what the
API does when an answer is lost, late, duplicated or wrong. The real Host's side of
the same contract is exercised in tests/api/test_local_agent_integration.py.
"""
from __future__ import annotations

import asyncio
import json
import threading
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from apps.api.routers import backtests, conexus
from apps.api.services import agent_run_service, backtest_job_service
from apps.api.services.agent_conversation_service import (
    LOST_RUN_READS,
    SUBMISSION_STALE_SECONDS,
    AgentConversationStore,
)

RUN = {"id": "run-1", "status": "running", "createdAt": "2026-09-10T00:00:00Z", "slug": "alphalab-research-agent", "version": 1}


def sse(*events: dict) -> bytes:
    return "".join(f"id: {item['sequence']}\ndata: {json.dumps(item)}\n\n" for item in events).encode()


def event(sequence: int, kind: str = "run.started", status: str = "running", **payload) -> dict:
    return {"sequence": sequence, "runId": "run-1", "at": f"2026-09-10T00:00:{sequence:02d}Z", "type": kind, "status": status, **payload}


class FakeHost:
    """What the local Host does, plus switches for the ways an exchange can go wrong."""

    def __init__(self) -> None:
        self.run = deepcopy(RUN)
        self.posts: list[httpx.Request] = []
        self.events: list[dict] = []
        self.event_requests: list[str | None] = []
        self.create_failure: Exception | None = None
        self.create_status = 202
        self.lose_acknowledgement = False
        self.run_status = 200
        self.lookup: dict | None = {"known": False}
        self.nodes: list[dict] = []
        self.workspace_status = 200

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/runs") and request.method == "POST":
            if self.create_failure:
                raise self.create_failure
            self.posts.append(request)
            if self.create_status != 202:
                return httpx.Response(self.create_status, json={"error": {"code": "local_host_error", "message": "Local run queue is full."}})
            if self.lose_acknowledgement:
                self.lookup = {"known": True, "state": "accepted", "run": deepcopy(self.run), "accessToken": "run-secret"}
                raise httpx.ReadError("connection dropped after the Host accepted the Run")
            return httpx.Response(202, json={"run": deepcopy(self.run), "accessToken": "run-secret"})
        if "/api/alphalab/submissions/" in path:
            return httpx.Response(200, json=self.lookup)
        if path.endswith("/workspace"):
            return httpx.Response(self.workspace_status, json={"workspace": {"nodes": self.nodes}})
        if self.run_status != 200:
            return httpx.Response(self.run_status, json={"error": {"code": "local_host_error", "message": "Run not found or access denied."}})
        assert request.headers["authorization"] == "Bearer run-secret"
        if path.endswith("/events"):
            cursor = int(request.headers.get("last-event-id") or 0)
            self.event_requests.append(request.headers.get("last-event-id"))
            return httpx.Response(
                200, stream=httpx.ByteStream(sse(*[item for item in self.events if item["sequence"] > cursor])),
                headers={"content-type": "text/event-stream"},
            )
        if path.endswith("/cancel"):
            self.run.update(status="cancelled", completedAt="2026-09-10T00:01:00Z", error={"code": "run_cancelled", "message": "Run cancelled."})
        return httpx.Response(200, json={"run": deepcopy(self.run)})


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setenv("ALPHALAB_RUNTIME_DIR", str(tmp_path))
    monkeypatch.setenv("ALPHALAB_AGENT_MODE", "local")
    monkeypatch.setenv("CONEXUS_WEB_ORIGIN", "http://127.0.0.1:18787")
    monkeypatch.setenv("CONEXUS_PUBLICATION_SLUG", "alphalab-research-agent")
    monkeypatch.setenv("CONEXUS_PUBLICATION_WORKSPACE_TOKEN", "workspace-secret")
    monkeypatch.setattr(agent_run_service, "EVENT_DRAIN_SECONDS", 5.0)
    host = FakeHost()
    client_class = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: client_class(**kwargs, transport=httpx.MockTransport(host)))
    app = FastAPI()
    app.include_router(conexus.router)
    app.include_router(backtests.router)
    return TestClient(app), agent_run_service.conversation_store(), host


def submit(client, message_id="user-1"):
    return client.post("/api/conexus/runs", json={
        "exposureId": "alphalab-research-agent", "input": {"request": "Research this"},
        "conversation": {"id": "chat-1", "title": "Research", "createdAt": "2026-09-10T00:00:00Z", "messageId": message_id, "message": "Research this"},
    })


def reconcile():
    asyncio.run(agent_run_service.reconcile_runs())


def replies(store):
    conversations = store.list_conversations()
    return [item for item in conversations[0]["messages"] if item["role"] == "assistant"] if conversations else []


def users(store):
    conversations = store.list_conversations()
    return [item for item in conversations[0]["messages"] if item["role"] == "user"] if conversations else []


def scope():
    return agent_run_service.run_scope()


def test_the_production_app_runs_the_recovery_worker(monkeypatch):
    # A reply must reach the conversation with no browser open. Tests that build their
    # own app cannot notice if the real one stops starting the worker.
    from apps.api.main import app

    ran = threading.Event()

    async def reconcile_once():
        ran.set()

    monkeypatch.setattr(agent_run_service, "reconcile_runs", reconcile_once)
    with TestClient(app):
        assert ran.wait(5)


# --- Submission: one message is at most one billed Run ---------------------------------


def test_duplicate_submission_after_success_returns_the_same_run_without_a_second_post(world):
    client, store, host = world
    first = submit(client)
    second = submit(client)
    assert first.status_code == 202 and second.status_code == 200
    assert second.json()["run"]["id"] == first.json()["run"]["id"] == "run-1"
    assert second.json()["replayed"] is True
    assert len(host.posts) == 1
    assert len(users(store)) == 1
    assert "accessToken" not in second.text and "run-secret" not in second.text


def test_every_forward_carries_a_stable_opaque_idempotency_key(world):
    client, _, host = world
    submit(client)
    key = host.posts[0].headers["idempotency-key"]
    assert key == agent_run_service.host_submission_key("chat-1:user-1")
    assert "chat-1" not in key


def test_lost_acknowledgement_is_not_resent_and_recovers_the_accepted_run(world):
    client, store, host = world
    host.lose_acknowledgement = True
    lost = submit(client)
    assert lost.status_code == 503 and lost.json()["code"] == "submission_unconfirmed"
    assert len(host.posts) == 1
    host.lose_acknowledgement = False
    retried = submit(client)
    assert retried.status_code == 200, retried.text
    assert retried.json()["run"]["id"] == "run-1"
    assert len(host.posts) == 1, "resolving a lost acknowledgement must not submit again"
    assert users(store)[0]["runId"] == "run-1"


def test_lost_acknowledgement_is_recovered_by_the_background_worker_without_a_browser(world):
    client, store, host = world
    host.lose_acknowledgement = True
    submit(client)
    reconcile()
    assert store.get_run(scope(), "run-1") is not None
    assert users(store)[0]["runId"] == "run-1"
    assert len(host.posts) == 1


def test_crash_mid_forward_is_recovered_once_the_attempt_is_stale(world):
    _, store, host = world
    turn = {"id": "chat-1", "title": "Research", "createdAt": "2026-09-10T00:00:00Z", "updatedAt": "2026-09-10T00:00:00Z",
            "messages": [{"id": "user-1", "role": "user", "content": "Research this", "createdAt": "2026-09-10T00:00:00Z", "runId": "pending"}]}
    past = datetime.now(timezone.utc) - timedelta(seconds=SUBMISSION_STALE_SECONDS + 1)
    store.claim_submission(scope(), "chat-1:user-1", turn, message_id="user-1", now=past)
    host.lookup = {"known": True, "state": "accepted", "run": deepcopy(RUN), "accessToken": "run-secret"}
    reconcile()
    assert store.get_run(scope(), "run-1") is not None
    assert store.get_submission(scope(), "chat-1:user-1")["state"] == "accepted"


def test_a_live_forward_is_never_treated_as_abandoned(world):
    client, store, host = world
    turn = {"id": "chat-1", "title": "Research", "createdAt": "2026-09-10T00:00:00Z", "updatedAt": "2026-09-10T00:00:00Z", "messages": []}
    store.claim_submission(scope(), "chat-1:user-1", turn, message_id="user-1")
    assert store.unresolved_submissions(scope()) == []
    duplicate = submit(client)
    assert duplicate.status_code == 409 and duplicate.json()["code"] == "submission_in_progress"
    assert not host.posts


def test_host_that_never_saw_the_request_allows_a_safe_retry_with_one_post(world):
    client, _, host = world
    host.create_failure = httpx.ReadError("dropped before the Host read it")
    assert submit(client).json()["code"] == "submission_unconfirmed"
    host.create_failure = None
    host.lookup = {"known": False}
    retried = submit(client)
    assert retried.status_code == 202, retried.text
    assert len(host.posts) == 1


def test_connection_refused_cannot_have_reached_the_host_so_retry_is_immediate(world):
    client, _, host = world
    host.create_failure = httpx.ConnectError("refused")
    refused = submit(client)
    assert refused.status_code == 503 and refused.json()["code"] == "not_configured"
    host.create_failure = None
    assert submit(client).status_code == 202
    assert len(host.posts) == 1


def test_definite_rejection_releases_the_message_for_retry(world):
    client, store, host = world
    host.create_status = 429
    assert submit(client).status_code == 429
    assert store.get_submission(scope(), "chat-1:user-1") is None
    host.create_status = 202
    assert submit(client).status_code == 202
    assert len(host.posts) == 2


def test_remote_host_that_cannot_be_asked_stays_unconfirmed_and_never_resends(world, monkeypatch):
    client, _, host = world
    monkeypatch.setenv("ALPHALAB_AGENT_MODE", "remote")
    host.lose_acknowledgement = True
    submit(client)
    host.lose_acknowledgement = False
    again = submit(client)
    assert again.status_code == 409 and again.json()["code"] == "submission_unconfirmed"
    assert len(host.posts) == 1


def test_a_reloaded_browser_learns_of_the_undecided_send_with_its_identity(world):
    client, _, host = world
    host.create_failure = httpx.ReadError("dropped")
    submit(client)
    pending = client.get("/api/conexus/conversations").json()["submissions"]
    assert pending[0]["messageId"] == "user-1" and pending[0]["conversationId"] == "chat-1"
    assert pending[0]["state"] == "unconfirmed" and pending[0]["message"] == "Research this"


def test_accepted_submissions_leave_no_pending_record_or_copy_of_the_message(world):
    client, store, _ = world
    submit(client)
    assert client.get("/api/conexus/conversations").json()["submissions"] == []
    assert store.get_submission(scope(), "chat-1:user-1")["conversation_json"] is None


def test_a_browser_is_only_told_about_sends_that_are_still_undecided(world):
    client, store, host = world
    host.create_failure = httpx.ReadError("the Host stopped answering")
    submit(client, "user-1")

    def open_ids():
        return [item["messageId"] for item in client.get("/api/conexus/conversations").json()["submissions"]]

    assert open_ids() == ["user-1"]

    # The Host answers that it never saw the send, so no warning is owed any more.
    reconcile()
    assert store.get_submission(scope(), "chat-1:user-1")["state"] == "not_started"
    assert open_ids() == []

    # Still undecided, then the user moves on and a later message in the conversation runs.
    store.mark_submission(scope(), "chat-1:user-1", "unconfirmed")
    host.lookup = None
    assert open_ids() == ["user-1"]
    host.create_failure = None
    submit(client, "user-2")
    assert open_ids() == []


# --- Recovery: never report success that did not happen ---------------------------------


def test_run_the_host_no_longer_knows_is_marked_lost_after_repeated_reads(world):
    client, store, host = world
    submit(client)
    host.run_status = 404
    for _ in range(LOST_RUN_READS - 1):
        reconcile()
        assert json.loads(store.get_run(scope(), "run-1")["snapshot_json"])["status"] == "running"
    reconcile()
    lost = json.loads(store.get_run(scope(), "run-1")["snapshot_json"])
    assert lost["status"] == "failed" and lost["error"]["code"] == "run_record_lost"
    assert replies(store)[0]["error"] is True
    assert not store.runs_to_reconcile(scope())
    session = client.get("/api/conexus/conversations").json()["runs"][0]["run"]["lifecycle"]
    assert session["stop"]["category"] == "lost" and session["stop"]["retry"] == "check_first"


def test_a_transient_unknown_answer_does_not_accumulate_toward_lost(world):
    client, store, host = world
    submit(client)
    for _ in range(LOST_RUN_READS * 2):
        host.run_status = 404
        reconcile()
        host.run_status = 200
        reconcile()
    assert json.loads(store.get_run(scope(), "run-1")["snapshot_json"])["status"] == "running"


def test_host_outage_never_becomes_a_terminal_state(world, monkeypatch):
    client, store, host = world
    submit(client)
    host.run_status = 503
    for _ in range(LOST_RUN_READS * 2):
        reconcile()
    assert json.loads(store.get_run(scope(), "run-1")["snapshot_json"])["status"] == "running"


def test_terminal_states_are_monotonic_even_against_a_later_success(world):
    client, store, host = world
    submit(client)
    host.run_status = 404
    for _ in range(LOST_RUN_READS):
        reconcile()
    host.run_status = 200
    host.run.update(status="completed", summary="Late success")
    assert client.get("/api/conexus/runs/run-1").status_code == 200
    assert json.loads(store.get_run(scope(), "run-1")["snapshot_json"])["status"] == "failed"
    assert len(replies(store)) == 1


def test_a_slow_read_cannot_move_a_running_task_back_to_queued(world):
    client, store, _ = world
    submit(client)
    store.record_run(scope(), {**RUN, "status": "queued"})
    assert json.loads(store.get_run(scope(), "run-1")["snapshot_json"])["status"] == "running"


def test_host_restart_is_explained_as_interrupted_and_not_resumable(world):
    client, store, host = world
    submit(client)
    host.run.update(status="cancelled", completedAt="2026-09-10T00:01:00Z",
                    error={"code": "host_restarted", "message": "The previous Host stopped during this run."})
    run = client.get("/api/conexus/runs/run-1").json()["run"]
    assert run["lifecycle"]["stop"] == {
        "category": "host_interrupted", "code": "host_restarted", "retry": "new_run",
        "remediation": run["lifecycle"]["stop"]["remediation"], "resumable": False,
    }
    assert "Nothing was replayed" in run["lifecycle"]["stop"]["remediation"]


def test_browser_read_of_an_unknown_run_serves_the_authoritative_record(world):
    client, _, host = world
    submit(client)
    host.run_status = 404
    response = client.get("/api/conexus/runs/run-1")
    assert response.status_code == 200 and response.json()["run"]["status"] == "running"


def test_cancelling_a_run_the_host_lost_reports_lost_rather_than_cancelled(world):
    client, store, host = world
    submit(client)
    host.run_status = 404
    run = client.post("/api/conexus/runs/run-1/cancel").json()["run"]
    assert run["status"] == "failed" and run["error"]["code"] == "run_record_lost"
    assert len(replies(store)) == 1


def test_cancel_during_host_outage_does_not_claim_cancellation(world, monkeypatch):
    client, store, host = world
    submit(client)
    host.run_status = 503
    assert client.post("/api/conexus/runs/run-1/cancel").status_code == 503
    assert json.loads(store.get_run(scope(), "run-1")["snapshot_json"])["status"] == "running"


# --- Trace: how far did it get, and is the record complete? ------------------------------


def test_event_replay_and_duplicates_produce_one_stable_record(world):
    client, store, host = world
    submit(client)
    host.events = [event(1, "run.queued", "queued"), event(2)]
    reconcile()
    before = store.trace_entries(scope(), "run-1")
    assert store.record_events(scope(), "run-1", host.events) is False
    assert store.trace_entries(scope(), "run-1") == before
    assert [item["upstreamSequence"] for item in before if item["upstreamSequence"]] == [1, 2]


def test_early_stream_closure_resumes_from_the_committed_cursor(world):
    client, store, host = world
    submit(client)
    host.events = [event(1, "run.queued", "queued")]
    reconcile()
    host.events += [event(2), event(3, "run.completed", "completed")]
    host.run.update(status="completed", summary="Done", completedAt="2026-09-10T00:00:03Z")
    reconcile()
    assert host.event_requests[-1] == "1", "the second pass must not re-read what was already stored"
    assert [item["upstreamSequence"] for item in store.trace_entries(scope(), "run-1") if item["upstreamSequence"]] == [1, 2, 3]
    assert client.get("/api/conexus/runs/run-1/trace").json()["complete"] is True
    assert not store.runs_to_reconcile(scope())


def test_evicted_events_are_reported_rather_than_implying_a_complete_trace(world):
    client, store, host = world
    submit(client)
    host.events = [event(5), event(6, "run.completed", "completed")]
    host.run.update(status="completed", summary="Done")
    reconcile()
    trace = client.get("/api/conexus/runs/run-1/trace").json()
    assert trace["complete"] is False
    gap = next(item for item in trace["entries"] if item["kind"] == "events.gap")
    assert gap["detail"] == {"afterSequence": 0, "resumedAtSequence": 5}


def test_finished_run_whose_events_are_gone_closes_as_incomplete_and_stops_polling(world):
    client, store, host = world
    submit(client)
    host.run.update(status="completed", summary="Done")
    reconcile()
    assert not store.runs_to_reconcile(scope())
    trace = client.get("/api/conexus/runs/run-1/trace").json()
    assert trace["complete"] is False and any(item["kind"] == "trace.incomplete" for item in trace["entries"])


def test_trace_correlates_a_task_through_tools_jobs_and_delivered_outputs(world, monkeypatch):
    client, store, host = world
    monkeypatch.setattr(backtests, "submit_backtest_job", lambda _request: {"id": "job-9", "status": "queued", "deduplicated": False})
    monkeypatch.setattr(agent_run_service, "_job_state", lambda job: {**job, "status": "succeeded", "backtestId": "job-9"})
    submit(client)
    created = client.post(
        "/api/backtests/jobs", headers={"X-AlphaLab-Agent-Run": "run-1"},
        json={"project_id": "p1", "start_date": "2025-01-01", "end_date": "2025-06-30", "profile": "runtime", "confirm_python_execution": True},
    )
    assert created.status_code == 202, created.text
    call = {"id": "call-1", "function": {"name": "use", "arguments": json.dumps({"node_id": "alphalab-project-run", "input": {"command": "backtest.run", "args": {"project_id": "p1"}}})}}
    host.events = [
        event(1, "run.queued", "queued"), event(2),
        event(3, "run.message", message={"role": "assistant", "content": "Starting.", "tool_calls": [call]}),
        event(4, "run.message", message={"role": "tool", "tool_call_id": "call-1", "name": "use", "content": json.dumps({"success": True})}),
        event(5, "run.completed", "completed"),
    ]
    host.run.update(status="completed", summary="Report ready", startedAt="2026-09-10T00:00:02Z", completedAt="2026-09-10T00:00:05Z",
                    nodeChanges={"created": ["report-1", "gone-1"], "updated": []})
    host.nodes = [{"id": "report-1", "type": "note", "label": "R", "description": "AlphaLab quantitative report", "updatedByRunId": "run-1", "values": {"content": "# R"}}]
    reconcile()
    trace = client.get("/api/conexus/runs/run-1/trace").json()
    assert trace["messageId"] == "user-1" and trace["conversationId"] == "chat-1"
    assert trace["status"] == "completed" and trace["phase"] == "completed" and trace["complete"] is True
    assert trace["timing"]["elapsedMs"] == 5000
    tool = trace["tools"][0]
    assert tool["callId"] == "call-1" and tool["command"] == "backtest.run" and tool["success"] is True
    assert tool["elapsedMs"] == 1000
    assert trace["jobs"] == [{"kind": "backtest", "id": "job-9", "owned": True, "status": "succeeded", "backtestId": "job-9"}]
    assert trace["delivery"]["delivered"] == ["report-1"]
    assert trace["delivery"]["missingFromWorkspace"] == ["gone-1"]
    assert trace["usage"]["available"] is False and trace["usage"]["modelTurns"] == 1


def test_trace_and_browser_payloads_hold_no_credentials_paths_or_research_content(world):
    client, store, host = world
    submit(client)
    secret_call = {"id": "c", "function": {"name": "use", "arguments": json.dumps({"input": {"command": "files.write", "args": {"content": "PRIVATE-STRATEGY"}}})}}
    host.events = [
        event(1, "run.message", message={"role": "assistant", "content": "PRIVATE-REASONING", "tool_calls": [secret_call]}),
        event(2, "run.message", message={"role": "tool", "tool_call_id": "c", "name": "use", "content": json.dumps({
            "success": False, "message": "open /Users/alice/secret.db failed with Bearer abc123 sk-livekey12345 token=hunter2 " + "a" * 40})}),
        event(3, "run.interaction_requested", interaction={"id": "q1", "question": "PRIVATE-QUESTION"}),
    ]
    reconcile()
    text = client.get("/api/conexus/runs/run-1/trace").text + client.get("/api/conexus/conversations").text.replace("Research this", "")
    for forbidden in ("run-secret", "workspace-secret", "PRIVATE-", "/Users/alice", "abc123", "sk-livekey", "hunter2", "a" * 40):
        assert forbidden not in text, forbidden
    trace = client.get("/api/conexus/runs/run-1/trace").json()
    assert trace["tools"][0]["command"] == "files.write"
    assert "failed" in trace["tools"][0]["message"]


def test_unknown_run_has_no_trace(world):
    client, _, _ = world
    assert client.get("/api/conexus/runs/nope/trace").status_code == 404


def test_retention_removes_finished_traces_but_never_open_work(world):
    client, store, host = world
    submit(client)
    host.events = [event(1)]
    reconcile()
    later = datetime(2030, 1, 1, tzinfo=timezone.utc)
    assert store.prune_records(scope(), retention_days=30, now=later) == 0, "a running task keeps its trace"
    host.events += [event(2, "run.completed", "completed")]
    host.run.update(status="completed", summary="Done", completedAt="2026-09-10T00:01:00Z")
    reconcile()
    assert store.prune_records(scope(), retention_days=30, now=later) > 0
    assert store.trace_entries(scope(), "run-1") == []
    assert replies(store), "retention applies to diagnostics, not to the conversation"


def test_retention_keeps_a_finished_task_still_owed_its_delivery_or_events(world):
    client, store, host = world
    submit(client)
    host.events = [event(1)]
    host.run.update(status="completed", summary="Report", completedAt="2026-09-10T00:01:00Z", nodeChanges={"created": ["report-1"], "updated": []})
    host.workspace_status = 503
    reconcile()
    assert store.get_run(scope(), "run-1")["delivered"] == 0
    later = datetime(2030, 1, 1, tzinfo=timezone.utc)
    assert store.prune_records(scope(), retention_days=30, now=later) == 0
    assert store.trace_entries(scope(), "run-1")


def test_upgraded_store_gains_columns_without_chasing_old_runs(tmp_path):
    import sqlite3

    path = tmp_path / "old.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.executescript("""CREATE TABLE agent_conversation_runs (scope TEXT NOT NULL, run_id TEXT NOT NULL,
            conversation_id TEXT NOT NULL, access_token TEXT NOT NULL, snapshot_json TEXT NOT NULL,
            delivered INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (scope, run_id));
            INSERT INTO agent_conversation_runs VALUES ('s', 'old', 'c', 't', '{"id":"old","status":"completed"}', 1);""")
    upgraded = AgentConversationStore(path)
    assert upgraded.runs_to_reconcile("s") == []
    assert upgraded.get_run("s", "old")["trace_open"] == 0


# --- Background jobs: cancellation reports what is actually true ---------------------------


def link(store, job_id, *, owned=True, kind="backtest"):
    assert store.link_job(scope(), "run-1", kind, job_id, owned=owned)


def test_job_link_ignores_unknown_and_finished_runs(world):
    client, store, host = world
    assert store.link_job(scope(), "nope", "backtest", "j", owned=True) is False
    submit(client)
    host.run.update(status="completed", summary="Done")
    client.get("/api/conexus/runs/run-1")
    assert store.link_job(scope(), "run-1", "backtest", "j", owned=True) is False


def test_cancel_stops_queued_owned_work_and_reports_what_keeps_running(world, monkeypatch):
    client, store, _ = world
    submit(client)
    statuses = {"queued-owned": "queued", "running-owned": "running", "queued-shared": "queued", "finished": "succeeded"}
    cancelled = []
    monkeypatch.setattr(agent_run_service, "_job_state", lambda job: {**job, "status": statuses[job["id"]]})
    monkeypatch.setattr(backtest_job_service, "cancel_queued_backtest_job", lambda job_id: cancelled.append(job_id) or True)
    link(store, "queued-owned")
    link(store, "running-owned")
    link(store, "queued-shared", owned=False)
    link(store, "finished")
    payload = client.post("/api/conexus/runs/run-1/cancel").json()
    assert payload["run"]["status"] == "cancelled"
    assert {item["id"]: item["outcome"] for item in payload["jobs"]} == {
        "queued-owned": "cancelled", "running-owned": "still_running",
        "queued-shared": "still_running", "finished": "already_finished",
    }
    assert cancelled == ["queued-owned"], "work another requester shares is not this Run's to stop"
    kinds = [item["detail"]["outcome"] for item in store.trace_entries(scope(), "run-1") if item["kind"] == "job.cancellation"]
    assert sorted(kinds) == ["already_finished", "cancelled", "still_running", "still_running"]


def test_lifecycle_counts_running_jobs_after_a_cancelled_run_so_the_ui_can_say_so(world, monkeypatch):
    client, store, _ = world
    submit(client)
    monkeypatch.setattr(agent_run_service, "_job_state", lambda job: {**job, "status": "running"})
    link(store, "running-owned")
    run = client.post("/api/conexus/runs/run-1/cancel").json()["run"]
    assert run["lifecycle"]["stop"]["category"] == "cancelled"
    assert run["lifecycle"]["activeJobs"] == 1


def test_duplicate_cancel_is_stable(world, monkeypatch):
    client, store, _ = world
    submit(client)
    monkeypatch.setattr(agent_run_service, "_job_state", lambda job: {**job, "status": "running"})
    link(store, "running-owned")
    first = client.post("/api/conexus/runs/run-1/cancel").json()
    second = client.post("/api/conexus/runs/run-1/cancel").json()
    assert first["jobs"] == second["jobs"]
    assert len(replies(store)) == 1
    assert len([item for item in store.trace_entries(scope(), "run-1") if item["kind"] == "job.cancellation"]) == 1


def test_a_worker_that_already_read_queued_cannot_start_a_cancelled_job(tmp_path):
    from alphalab import ResultStore

    store = ResultStore(tmp_path / "jobs.db")
    try:
        job_id = store.create_backtest_job({"project_id": "p"})
        assert store.cancel_queued_backtest_job(job_id, "Cancelled") is True
        assert store.start_backtest_job(job_id, "Running") is False
        assert store.get_backtest_job(job_id)["status"] == "interrupted"
    finally:
        store.close()


def test_job_manager_cancels_only_work_that_has_not_started(tmp_path):
    from threading import Event

    release, started = Event(), Event()

    def runner(*_args, **_kwargs):
        started.set()
        release.wait(10)
        return {"backtest_id": "x", "id": "x", "metrics": {}}

    manager = backtest_job_service.BacktestJobManager(Path(tmp_path) / "jobs.db", runner=runner)
    request = {"project_id": "p", "start_date": "2025-01-01", "end_date": "2025-02-01", "profile": "runtime", "revision": 1}
    try:
        running = manager.submit(request)
        assert started.wait(10)
        queued = manager.submit({**request, "revision": 2})
        assert queued["deduplicated"] is False
        assert manager.submit({**request, "revision": 2})["deduplicated"] is True
        assert manager.cancel_queued(running["id"]) is False, "a running job is left to finish"
        assert manager.cancel_queued(queued["id"]) is True
        assert manager.cancel_queued(queued["id"]) is False
        stopped = manager.get(queued["id"])
        assert stopped["status"] == "interrupted" and stopped["error_code"] == "AGENT_RUN_CANCELLED"
    finally:
        release.set()
        manager.shutdown(wait=True)
    assert manager.get(queued["id"])["status"] == "interrupted", "the worker must not revive a cancelled job"
