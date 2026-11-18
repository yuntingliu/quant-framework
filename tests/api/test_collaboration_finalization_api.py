from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from alphalab.collaboration import CollaborationError
from alphalab.collaboration import EvidenceStore
from alphalab.collaboration import ControlStore
from apps.api.services import collaboration_storage_service as storage
from apps.api.routers import conexus


def test_task_store_paths_are_deterministic_and_task_scoped(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "RUNTIME_DIR", tmp_path)
    control_root, evidence_root = storage.task_store_roots("task-123")
    assert control_root == tmp_path / "collaboration" / "tasks" / "task-123" / "control"
    assert evidence_root == tmp_path / "collaboration" / "tasks" / "task-123" / "evidence"


@pytest.mark.parametrize("task_id", ["", "../escape", "a/b", "..", "task id", "x" * 129])
def test_invalid_task_id_is_rejected_before_path_construction(tmp_path, monkeypatch, task_id):
    monkeypatch.setattr(storage, "RUNTIME_DIR", tmp_path)
    with pytest.raises(CollaborationError) as exc:
        storage.task_store_roots(task_id)
    assert exc.value.code == "INVALID_CONTRACT"


def test_create_and_reopen_task_stores_uses_one_canonical_evidence_database(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "RUNTIME_DIR", tmp_path)
    control = storage.create_task_control_store("task-123")
    assert isinstance(control, ControlStore)
    assert isinstance(control.evidence, EvidenceStore)
    reopened_control, evidence = storage.open_existing_task_stores("task-123")
    assert reopened_control.path == control.path
    assert evidence.root == control.evidence.root


def test_api_lookup_does_not_create_missing_task_databases(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "RUNTIME_DIR", tmp_path)
    with pytest.raises(CollaborationError) as exc:
        storage.open_existing_task_stores("unknown-task")
    assert exc.value.code == "TASK_MISMATCH"
    assert not (tmp_path / "collaboration").exists()


def test_task_store_rejects_symlinked_task_directory(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "RUNTIME_DIR", tmp_path)
    base = tmp_path / "collaboration" / "tasks"
    base.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    try:
        (base / "task-123").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("Directory symlinks are unavailable on this Windows account.")
    with pytest.raises(CollaborationError) as exc:
        storage.task_store_roots("task-123")
    assert exc.value.code == "ACCESS_DENIED"


def _client():
    app = FastAPI()
    app.include_router(conexus.router)
    return TestClient(app)


def test_finalize_route_uses_task_stores_and_returns_committed_receipt(monkeypatch):
    control, evidence = object(), object()
    seen = {}

    def open_stores(task_id):
        seen["task_id"] = task_id
        return control, evidence

    async def finalize(**kwargs):
        seen["finalize"] = kwargs
        return {"status": "committed", "receipt": {"receipt_id": "receipt-1"}}

    monkeypatch.setattr(conexus, "open_existing_task_stores", open_stores)
    monkeypatch.setattr(conexus, "finalize_collaboration_task", finalize)

    response = _client().post(
        "/api/conexus/collaboration/tasks/task-123/finalize",
        json={"idempotency_key": "delivery-1"},
    )
    assert response.status_code == 200
    assert response.json() == {
        "publication": {"status": "committed", "receipt": {"receipt_id": "receipt-1"}}
    }
    assert seen["task_id"] == "task-123"
    assert seen["finalize"] == {
        "task_id": "task-123",
        "idempotency_key": "delivery-1",
        "control_store": control,
        "evidence_store": evidence,
    }


def test_finalize_route_returns_accepted_for_pending_reconciliation(monkeypatch):
    monkeypatch.setattr(conexus, "open_existing_task_stores", lambda _task: (object(), object()))

    async def pending(**_kwargs):
        return {"status": "pending", "intent_id": "intent-1"}

    monkeypatch.setattr(conexus, "finalize_collaboration_task", pending)
    response = _client().post(
        "/api/conexus/collaboration/tasks/task-123/finalize",
        json={"idempotency_key": "delivery-1"},
    )
    assert response.status_code == 202
    assert response.json()["publication"]["status"] == "pending"


def test_finalize_route_rejects_extra_caller_supplied_approval_or_paths():
    response = _client().post(
        "/api/conexus/collaboration/tasks/task-123/finalize",
        json={
            "idempotency_key": "delivery-1",
            "review": {"status": "pass"},
            "control_root": "C:/untrusted",
        },
    )
    assert response.status_code == 422


@pytest.mark.parametrize(
    ("code", "status"),
    [
        ("TASK_MISMATCH", 404),
        ("ACCESS_DENIED", 403),
        ("FINALIZATION_BLOCKED", 409),
        ("STALE_CANDIDATE", 409),
        ("STORAGE_UNAVAILABLE", 503),
        ("INVALID_CONTRACT", 422),
    ],
)
def test_finalize_route_maps_collaboration_errors_without_leaking_paths(monkeypatch, code, status):
    monkeypatch.setattr(conexus, "open_existing_task_stores", lambda _task: (object(), object()))

    async def fail(**_kwargs):
        raise CollaborationError(code)

    monkeypatch.setattr(conexus, "finalize_collaboration_task", fail)
    response = _client().post(
        "/api/conexus/collaboration/tasks/task-123/finalize",
        json={"idempotency_key": "delivery-1"},
    )
    assert response.status_code == status
    assert response.json()["detail"]["code"] == code
    assert "C:/" not in response.text


def test_finalize_route_inherits_workstation_basic_auth(monkeypatch):
    from apps.api.auth import HttpBasicAuthMiddleware

    def missing_task(_task_id):
        raise CollaborationError("TASK_MISMATCH")

    monkeypatch.setattr(conexus, "open_existing_task_stores", missing_task)
    monkeypatch.setenv("ALPHALAB_WEB_AUTH_ENABLED", "true")
    monkeypatch.setenv("ALPHALAB_WEB_USERNAME", "member2")
    monkeypatch.setenv("ALPHALAB_WEB_PASSWORD", "test-password")
    app = FastAPI()
    app.add_middleware(HttpBasicAuthMiddleware)
    app.include_router(conexus.router)
    client = TestClient(app)

    response = client.post(
        "/api/conexus/collaboration/tasks/task-123/finalize",
        json={"idempotency_key": "delivery-1"},
    )
    assert response.status_code == 401
    response = client.post(
        "/api/conexus/collaboration/tasks/task-123/finalize",
        auth=("member2", "test-password"),
        json={"idempotency_key": "delivery-1"},
    )
    # The authenticated request proceeds to task lookup and is rejected only
    # because this test has not provisioned a task store.
    assert response.status_code == 404
