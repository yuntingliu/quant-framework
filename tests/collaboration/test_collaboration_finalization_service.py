"""Tests for the trusted Member 2 collaboration finalization entry point.

These tests isolate the coordinator boundary: real task/review state is
represented by strict fakes, while HTTP and Local Host operations are replaced
with controlled async functions. Real Local Host delivery is covered by the live-host
acceptance suite.
"""

from __future__ import annotations

import copy

import pytest

from alphalab.collaboration import CollaborationError
from apps.api.services import collaboration_finalization_service as finalization


CANDIDATE = {
    "candidate_id": "candidate-1",
    "revision": 2,
    "sha256": "a" * 64,
}
MANIFEST = {
    "manifest_id": "manifest-2",
    "revision": 2,
    "sha256": "b" * 64,
}
DRAFT_REPORT = {
    "artifact_id": "report",
    "sha256": "c" * 64,
    "locator": "",
}
PUBLICATION_SHA256 = "d" * 64
OUTPUTS = {
    "summary": "Reviewed result",
    "decisionNotebook": {
        "classification": "reviewed",
        "baseCase": "Reviewed result",
        "riskCase": "Synthetic risk case",
        "nextAction": "Inspect evidence",
        "candidateExpressions": [],
    },
    "workspaceResult": {
        "version": 1,
        "requestId": "request-1",
        "kind": "document",
        "reportId": "report-1",
        "title": "Reviewed report",
        "sources": [],
        "columns": [],
        "rows": [],
    },
    "workspaceCommands": {
        "version": 1,
        "requestId": "request-1",
        "commands": [],
    },
}


def approved_state():
    return {
        "status": "completed",
        "reason": "finish_root",
        "version": 7,
        "candidate": copy.deepcopy(CANDIDATE),
        "manifest": copy.deepcopy(MANIFEST),
        "correction": None,
        "children": [
            {
                "status": "completed",
                "needs_parent_escalation": False,
                "review": {
                    "status": "pass",
                    "candidate": copy.deepcopy(CANDIDATE),
                    "manifest": copy.deepcopy(MANIFEST),
                    "submission_id": "submission-2",
                },
            }
        ],
    }


class FakeControlStore:
    def __init__(self, state=None, publication_error=None, publication=None):
        self.state = state or approved_state()
        self.publication_error = publication_error
        self.publication = publication
        self.view_calls = []
        self.publication_calls = []

    def request(self, task_id, operation):
        self.view_calls.append((task_id, operation))
        assert operation == "view"
        return copy.deepcopy(self.state)

    def get_publication(self, task_id, *, intent_id=None, idempotency_key=None):
        self.publication_calls.append((task_id, intent_id, idempotency_key))
        if self.publication_error:
            raise self.publication_error
        if self.publication is None:
            raise CollaborationError("TASK_MISMATCH")
        return copy.deepcopy(self.publication)


class FakeEvidenceStore:
    def __init__(self, *, manifest_ref=None):
        self.manifest_ref = copy.deepcopy(manifest_ref or MANIFEST)
        self.calls = []

    def candidate_context(self, task_id, candidate):
        self.calls.append((task_id, copy.deepcopy(candidate)))
        return {
            "manifest_ref": copy.deepcopy(self.manifest_ref),
            "research_package": {
                "task_id": task_id,
                "draft_report": copy.deepcopy(DRAFT_REPORT),
                "publication": {
                    "artifact_id": "publication",
                    "sha256": PUBLICATION_SHA256,
                    "locator": "",
                },
            },
            "evidence": {
                "publication": {
                    "sha256": PUBLICATION_SHA256,
                    "content": {"outputs": copy.deepcopy(OUTPUTS)},
                }
            },
        }


@pytest.fixture
def stores():
    return FakeControlStore(), FakeEvidenceStore()


@pytest.mark.anyio
async def test_approved_task_builds_request_and_calls_publication_once(monkeypatch, stores):
    control, evidence = stores
    workspace = {"revision": 12, "nodes": []}
    destination = {
        "report_id": "report-1",
        "mode": "create",
        "expected_workspace_revision": 12,
        "expected_report_revision": None,
    }
    calls = {}

    async def fetch_snapshot(**kwargs):
        calls["snapshot_kwargs"] = kwargs
        return workspace

    def make_destination(actual_workspace, report_id):
        assert actual_workspace is workspace
        assert report_id == "report-1"
        return destination

    async def publish(**kwargs):
        calls["publish_kwargs"] = kwargs
        return {"task_id": "task-1", "status": "committed", "receipt": "receipt-1"}

    monkeypatch.setattr(finalization, "discover_local_host_origin", lambda _path=None: "http://127.0.0.1:8787")
    monkeypatch.setattr(finalization, "fetch_local_workspace_snapshot", fetch_snapshot)
    monkeypatch.setattr(finalization, "destination_from_workspace", make_destination)
    monkeypatch.setattr(finalization, "publish_final_output", publish)

    result = await finalization.finalize_collaboration_task(
        task_id="task-1",
        idempotency_key="delivery-1",
        control_store=control,
        evidence_store=evidence,
        connection_file="private/connection.json",
        host_origin="http://127.0.0.1:8787",
        publication_secret="test-secret",
    )

    assert result["status"] == "committed"
    assert calls["snapshot_kwargs"]["connection_file"] == "private/connection.json"
    kwargs = calls["publish_kwargs"]
    request = kwargs["request"]
    assert kwargs["destination"] == destination
    assert kwargs["host_origin"] == "http://127.0.0.1:8787"
    assert kwargs["publication_secret"] == "test-secret"
    assert request["task_id"] == "task-1"
    assert request["expected_task_version"] == 7
    assert request["candidate"] == CANDIDATE
    assert request["manifest"] == MANIFEST
    assert request["review_submission_id"] == "submission-2"
    assert request["payload_sha256"] == PUBLICATION_SHA256
    assert request["outputs"] == OUTPUTS
    assert request["destination"] == destination


@pytest.mark.anyio
async def test_task_without_passing_review_is_rejected_before_host_access(monkeypatch, stores):
    control, evidence = stores
    control.state["children"][-1]["review"]["status"] = "findings"

    async def unexpected_fetch(**kwargs):
        pytest.fail("Host must not be contacted for an unapproved task")

    monkeypatch.setattr(finalization, "fetch_local_workspace_snapshot", unexpected_fetch)

    with pytest.raises(CollaborationError) as exc:
        await finalization.finalize_collaboration_task(
            task_id="task-1", idempotency_key="delivery-1",
            control_store=control, evidence_store=evidence,
        )
    assert exc.value.code == "FINALIZATION_BLOCKED"


@pytest.mark.anyio
async def test_committed_idempotency_key_returns_original_receipt_without_host_access(monkeypatch, stores):
    control, evidence = stores
    receipt = {"receipt_id": "original-receipt"}
    control.publication = {"status": "committed", "receipt": receipt}

    async def unexpected_fetch(**kwargs):
        pytest.fail("Committed replay must not fetch a new workspace snapshot")

    monkeypatch.setattr(finalization, "fetch_local_workspace_snapshot", unexpected_fetch)

    result = await finalization.finalize_collaboration_task(
        task_id="task-1", idempotency_key="delivery-1",
        control_store=control, evidence_store=evidence,
    )
    assert result == receipt
    assert evidence.calls == []


@pytest.mark.anyio
async def test_pending_intent_reconciles_without_constructing_new_destination(monkeypatch, stores):
    control, evidence = stores
    control.publication = {"status": "pending", "intent_id": "intent-1"}
    calls = {}

    async def reconcile(**kwargs):
        calls.update(kwargs)
        return {"status": "pending", "intent_id": "intent-1"}

    async def unexpected_fetch(**kwargs):
        pytest.fail("Pending delivery must reconcile its original intent first")

    monkeypatch.setattr(finalization, "discover_local_host_origin", lambda _path=None: "http://127.0.0.1:8787")
    monkeypatch.setattr(finalization, "reconcile_publication", reconcile)
    monkeypatch.setattr(finalization, "fetch_local_workspace_snapshot", unexpected_fetch)

    result = await finalization.finalize_collaboration_task(
        task_id="task-1", idempotency_key="delivery-1",
        control_store=control, evidence_store=evidence,
        host_origin="http://127.0.0.1:8787", publication_secret="secret",
    )
    assert result == {"status": "pending", "intent_id": "intent-1"}
    assert calls["task_id"] == "task-1"
    assert calls["idempotency_key"] == "delivery-1"
    assert calls["host_origin"] == "http://127.0.0.1:8787"
    assert calls["publication_secret"] == "secret"


@pytest.mark.anyio
async def test_stale_manifest_is_rejected_before_host_access(monkeypatch, stores):
    control, evidence = stores
    evidence.manifest_ref = {
        "manifest_id": "different-manifest",
        "revision": 99,
        "sha256": "e" * 64,
    }

    async def unexpected_fetch(**kwargs):
        pytest.fail("Host must not be contacted for stale evidence")

    monkeypatch.setattr(finalization, "fetch_local_workspace_snapshot", unexpected_fetch)

    with pytest.raises(CollaborationError) as exc:
        await finalization.finalize_collaboration_task(
            task_id="task-1", idempotency_key="delivery-1",
            control_store=control, evidence_store=evidence,
        )
    assert exc.value.code == "STALE_CANDIDATE"


@pytest.mark.anyio
async def test_local_host_connection_failure_prevents_publication(monkeypatch, stores):
    control, evidence = stores
    published = False

    async def failed_snapshot(**kwargs):
        raise CollaborationError("STORAGE_UNAVAILABLE")

    async def publish(**kwargs):
        nonlocal published
        published = True
        return {}

    monkeypatch.setattr(finalization, "fetch_local_workspace_snapshot", failed_snapshot)
    monkeypatch.setattr(finalization, "publish_final_output", publish)

    with pytest.raises(CollaborationError) as exc:
        await finalization.finalize_collaboration_task(
            task_id="task-1", idempotency_key="delivery-1",
            control_store=control, evidence_store=evidence,
        )
    assert exc.value.code == "STORAGE_UNAVAILABLE"
    assert published is False


@pytest.mark.anyio
async def test_rejected_intent_is_not_silently_retried(monkeypatch, stores):
    control, evidence = stores
    control.publication = {
        "status": "rejected",
        "failure": {"error_code": "VERSION_CONFLICT"},
    }

    async def unexpected_fetch(**kwargs):
        pytest.fail("Rejected intent must not create a new publication")

    monkeypatch.setattr(finalization, "fetch_local_workspace_snapshot", unexpected_fetch)

    with pytest.raises(CollaborationError) as exc:
        await finalization.finalize_collaboration_task(
            task_id="task-1", idempotency_key="delivery-1",
            control_store=control, evidence_store=evidence,
        )
    assert exc.value.code == "FINALIZATION_BLOCKED"


@pytest.mark.anyio
async def test_custom_connection_file_origin_is_reused_for_snapshot_and_publication(monkeypatch, stores):
    control, evidence = stores
    seen = {}
    origin = "http://127.0.0.1:9876"

    def discover(connection_file):
        seen["discover_file"] = connection_file
        return origin

    async def fetch_snapshot(**kwargs):
        seen["snapshot"] = kwargs
        return {"revision": 4, "nodes": []}

    def make_destination(workspace, report_id):
        return {
            "report_id": report_id, "mode": "create",
            "expected_workspace_revision": workspace["revision"],
            "expected_report_revision": None,
        }

    async def publish(**kwargs):
        seen["publish"] = kwargs
        return {"status": "committed"}

    monkeypatch.setattr(finalization, "discover_local_host_origin", discover)
    monkeypatch.setattr(finalization, "fetch_local_workspace_snapshot", fetch_snapshot)
    monkeypatch.setattr(finalization, "destination_from_workspace", make_destination)
    monkeypatch.setattr(finalization, "publish_final_output", publish)

    result = await finalization.finalize_collaboration_task(
        task_id="task-1",
        idempotency_key="delivery-custom-origin",
        control_store=control,
        evidence_store=evidence,
        connection_file="private/custom-connection.json",
    )

    assert result["status"] == "committed"
    assert seen["discover_file"] == "private/custom-connection.json"
    assert seen["snapshot"]["connection_file"] == "private/custom-connection.json"
    assert seen["publish"]["host_origin"] == origin


@pytest.mark.anyio
async def test_explicit_host_override_must_match_private_connection(monkeypatch, stores):
    control, evidence = stores
    monkeypatch.setattr(
        finalization, "discover_local_host_origin",
        lambda _path=None: "http://127.0.0.1:8787",
    )

    async def unexpected_fetch(**kwargs):
        pytest.fail("A mismatched explicit Host must be rejected before snapshot access")

    monkeypatch.setattr(finalization, "fetch_local_workspace_snapshot", unexpected_fetch)
    with pytest.raises(CollaborationError) as exc:
        await finalization.finalize_collaboration_task(
            task_id="task-1",
            idempotency_key="delivery-mismatch",
            control_store=control,
            evidence_store=evidence,
            host_origin="http://127.0.0.1:9999",
        )
    assert exc.value.code == "ACCESS_DENIED"
