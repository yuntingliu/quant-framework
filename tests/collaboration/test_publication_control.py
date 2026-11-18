from __future__ import annotations

import copy
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from alphalab.collaboration import (
    CollaborationError,
    ControlStore,
    encode_json,
    sha256,
)
from scripts.prepare_collaboration_host import prepare

ROOT = Path(__file__).resolve().parents[2]
EXAMPLES = json.loads(
    (ROOT / "tests/fixtures/collaboration/contracts/examples.json").read_text(
        encoding="utf-8"
    )
)
SCOPES = ["model.invoke", "evidence.read", "review.submit", "review.delegate"]


def _approve(store, fixture):
    task_id = fixture["task_id"]
    delegation = fixture["delegation"]
    review = fixture["review"]
    store.request(task_id, "begin")
    store.request(task_id, "start", delegation=delegation)

    raw = encode_json(review).decode("utf-8")
    identity = {
        "role": "reviewer",
        "task_id": task_id,
        "node_id": delegation["node_id"],
        "child_task_id": delegation["child_task_id"],
        "attempt_id": delegation["attempt_id"],
    }
    store.request(
        task_id,
        "submit",
        child_task_id=delegation["child_task_id"],
        submission_id=review["submission_id"],
        payload_json=raw,
        sha256=sha256(raw.encode("utf-8")),
        identity=identity,
    )
    store.request(
        task_id,
        "finish_child",
        child_task_id=delegation["child_task_id"],
        status="done",
    )
    store.request(task_id, "finish_root", status="done")


def _valid_final_request(store, fixture, *, idempotency_key="delivery-1"):
    task_id = fixture["task_id"]
    state = store.request(task_id, "view")
    context = store.evidence.candidate_context(task_id, state["candidate"])
    package = context["research_package"]
    publication_ref = package["publication"]
    publication = context["evidence"][publication_ref["artifact_id"]]
    outputs = copy.deepcopy(publication["content"]["outputs"])

    request = copy.deepcopy(EXAMPLES["messages"]["final_output_request"])
    request.update(
        task_id=task_id,
        request_id=outputs["workspaceResult"]["requestId"],
        idempotency_key=idempotency_key,
        expected_task_version=state["version"],
        candidate=state["candidate"],
        manifest=state["manifest"],
        review_submission_id=state["children"][-1]["review"]["submission_id"],
        correction_response_id=None,
        draft_report=package["draft_report"],
        payload_sha256=publication["sha256"],
        outputs=outputs,
    )
    return request, copy.deepcopy(request["destination"])


@pytest.fixture
def approved_publication(tmp_path):
    fixture = prepare(tmp_path)
    store = ControlStore(tmp_path / "control", tmp_path / "evidence")
    store.register(
        fixture["task_id"],
        fixture["candidate"],
        {"scopes": SCOPES, "operations": []},
    )
    _approve(store, fixture)
    return store, fixture


def test_prepare_publication_persists_one_version_bound_intent(approved_publication):
    store, fixture = approved_publication
    request, destination = _valid_final_request(store, fixture)
    before = store.request(fixture["task_id"], "view")

    intent = store.prepare_publication(
        fixture["task_id"], request, destination=destination
    )

    assert intent["status"] == "pending"
    assert intent["request"]["candidate"] == before["candidate"]
    assert intent["request"]["manifest"] == before["manifest"]
    assert intent["request"]["expected_task_version"] == before["version"]
    assert store.get_publication(
        fixture["task_id"], intent_id=intent["intent_id"]
    ) == intent
    # Publication bookkeeping must not change the reviewed task version.
    assert store.request(fixture["task_id"], "view")["version"] == before["version"]


def test_identical_idempotency_retry_returns_original_intent(approved_publication):
    store, fixture = approved_publication
    request, destination = _valid_final_request(store, fixture)

    first = store.prepare_publication(
        fixture["task_id"], request, destination=destination
    )
    second = store.prepare_publication(
        fixture["task_id"], request, destination=destination
    )

    assert second["intent_id"] == first["intent_id"]
    assert second["request_sha256"] == first["request_sha256"]


def test_reusing_idempotency_key_for_changed_request_is_rejected(approved_publication):
    store, fixture = approved_publication
    request, destination = _valid_final_request(store, fixture)
    store.prepare_publication(fixture["task_id"], request, destination=destination)

    changed = copy.deepcopy(request)
    changed["request_id"] = "different-request"
    with pytest.raises(CollaborationError) as error:
        store.prepare_publication(
            fixture["task_id"], changed, destination=destination
        )
    assert error.value.code == "DUPLICATE_CONFLICT"


def test_competing_intent_is_rejected_while_first_is_pending(approved_publication):
    store, fixture = approved_publication
    request, destination = _valid_final_request(store, fixture)
    store.prepare_publication(fixture["task_id"], request, destination=destination)

    competing = copy.deepcopy(request)
    competing["idempotency_key"] = "delivery-2"
    with pytest.raises(CollaborationError) as error:
        store.prepare_publication(
            fixture["task_id"], competing, destination=destination
        )
    assert error.value.code == "INVALID_STATE"


def test_stale_task_version_is_rejected(approved_publication):
    store, fixture = approved_publication
    request, destination = _valid_final_request(store, fixture)
    request["expected_task_version"] -= 1

    with pytest.raises(CollaborationError) as error:
        store.prepare_publication(
            fixture["task_id"], request, destination=destination
        )
    assert error.value.code == "FINALIZATION_BLOCKED"


def test_destination_context_must_match_request(approved_publication):
    store, fixture = approved_publication
    request, destination = _valid_final_request(store, fixture)
    untrusted_destination = copy.deepcopy(destination)
    untrusted_destination["expected_workspace_revision"] += 1

    with pytest.raises(CollaborationError) as error:
        store.prepare_publication(
            fixture["task_id"], request, destination=untrusted_destination
        )
    assert error.value.code == "VERSION_CONFLICT"


def test_receipt_is_bound_to_intent_and_is_idempotent(approved_publication):
    store, fixture = approved_publication
    task_id = fixture["task_id"]
    request, destination = _valid_final_request(store, fixture)
    intent = store.prepare_publication(task_id, request, destination=destination)
    report_revision = (
        1 if destination["mode"] == "create"
        else destination["expected_report_revision"] + 1
    )
    receipt = {
        "task_id": task_id,
        "intent_id": intent["intent_id"],
        "request_id": request["request_id"],
        "idempotency_key": request["idempotency_key"],
        "request_sha256": intent["request_sha256"],
        "payload_sha256": request["payload_sha256"],
        "candidate": request["candidate"],
        "manifest": request["manifest"],
        "review_submission_id": request["review_submission_id"],
        "destination": destination,
        "workspace_revision": destination["expected_workspace_revision"] + 1,
        "report_revision": report_revision,
        "committed_at": datetime.now(timezone.utc).isoformat(),
    }

    first = store.record_publication_receipt(task_id, intent["intent_id"], receipt)
    second = store.record_publication_receipt(task_id, intent["intent_id"], receipt)

    assert first == receipt
    assert second == receipt
    assert store.get_publication(task_id, intent_id=intent["intent_id"])["status"] == "committed"


def test_wrong_receipt_revision_does_not_commit_intent(approved_publication):
    store, fixture = approved_publication
    task_id = fixture["task_id"]
    request, destination = _valid_final_request(store, fixture)
    intent = store.prepare_publication(task_id, request, destination=destination)
    receipt = {
        "task_id": task_id,
        "intent_id": intent["intent_id"],
        "request_id": request["request_id"],
        "idempotency_key": request["idempotency_key"],
        "request_sha256": intent["request_sha256"],
        "payload_sha256": request["payload_sha256"],
        "candidate": request["candidate"],
        "manifest": request["manifest"],
        "review_submission_id": request["review_submission_id"],
        "destination": destination,
        "workspace_revision": destination["expected_workspace_revision"] + 2,
        "report_revision": 1,
        "committed_at": datetime.now(timezone.utc).isoformat(),
    }

    with pytest.raises(CollaborationError) as error:
        store.record_publication_receipt(task_id, intent["intent_id"], receipt)
    assert error.value.code == "VERSION_CONFLICT"
    assert store.get_publication(task_id, intent_id=intent["intent_id"])["status"] == "pending"


def test_abort_requires_confirmed_non_commit_and_is_audited(approved_publication):
    store, fixture = approved_publication
    task_id = fixture["task_id"]
    request, destination = _valid_final_request(store, fixture)
    intent = store.prepare_publication(task_id, request, destination=destination)

    with pytest.raises(CollaborationError) as error:
        store.abort_publication(
            task_id,
            intent["intent_id"],
            error_code="VERSION_CONFLICT",
            reason="Destination changed before commit.",
        )
    assert error.value.code == "INVALID_CONTRACT"

    rejected = store.abort_publication(
        task_id,
        intent["intent_id"],
        error_code="VERSION_CONFLICT",
        reason="Destination changed before commit.",
        confirmed_not_committed=True,
    )
    assert rejected["status"] == "rejected"
    assert store.get_publication(task_id, intent_id=intent["intent_id"])["failure"]["error_code"] == "VERSION_CONFLICT"
