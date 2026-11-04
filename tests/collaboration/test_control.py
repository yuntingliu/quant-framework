from __future__ import annotations

import copy
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest

from alphalab.collaboration import CollaborationError, ControlStore, encode_json, sha256
from scripts.prepare_collaboration_host import prepare

SCOPES = ["model.invoke", "evidence.read", "review.submit", "review.delegate", "project.write"]


@pytest.fixture
def setup(tmp_path):
    fixture = prepare(tmp_path)
    store = ControlStore(tmp_path / "control", tmp_path / "evidence")
    store.register(fixture["task_id"], fixture["candidate"], {"scopes": SCOPES, "operations": []})
    store.request("task-1", "begin")
    return store, fixture


def submit(store, fixture, review=None, identity=None):
    review = review or fixture["review"]
    raw = json.dumps(review)
    d = fixture["delegation"]
    return store.request("task-1", "submit", child_task_id=d["child_task_id"],
                         submission_id=review["submission_id"], payload_json=raw, sha256=sha256(raw.encode()),
                         identity=identity or {"role": "reviewer", "task_id": "task-1", "node_id": "reviewer-1",
                                               "child_task_id": d["child_task_id"], "attempt_id": d["attempt_id"]})


def test_durable_delivery_replay_and_terminal_root(setup):
    store, fixture = setup
    child = store.request("task-1", "start", delegation=fixture["delegation"])
    assert child["scopes"] == ["evidence.read", "model.invoke", "review.submit"]
    receipt = submit(store, fixture)
    store.request("task-1", "finish_child", child_task_id=fixture["delegation"]["child_task_id"], status="done")
    assert store.request("task-1", "finish_root", status="done")["status"] == "completed"
    reopened = ControlStore(store.root, store.evidence.root)
    assert submit(reopened, fixture) == receipt
    assert reopened.request("task-1", "recover")["status"] == "completed"
    changed = {**fixture["review"], "reason": "different bytes"}
    with pytest.raises(CollaborationError, match="different content"):
        submit(reopened, fixture, changed)


def test_start_race_and_cumulative_limit(setup):
    store, fixture = setup

    def start(_):
        try:
            store.request("task-1", "start", delegation=fixture["delegation"])
            return "started"
        except CollaborationError as error:
            return error.code

    with ThreadPoolExecutor(2) as executor:
        assert sorted(executor.map(start, range(2))) == ["DUPLICATE_CHILD_TASK", "started"]
    d = fixture["delegation"]
    other = {**d, "child_task_id": "other", "attempt_id": "other", "round": 2}
    with pytest.raises(CollaborationError) as error:
        store.request("task-1", "start", delegation=other)
    assert error.value.code == "CHILD_LIMIT"
    store.request("task-1", "cancel_child", child_task_id=d["child_task_id"])
    for number in (2, 3):
        other = {**d, "child_task_id": f"child-{number}", "attempt_id": f"attempt-{number}-unique", "round": number}
        store.request("task-1", "start", delegation=other)
        store.request("task-1", "cancel_child", child_task_id=other["child_task_id"])
    with pytest.raises(CollaborationError) as error:
        store.request("task-1", "start", delegation={**other, "child_task_id": "fourth", "attempt_id": "fourth", "round": 4})
    assert error.value.code == "CHILD_LIMIT"


@pytest.mark.parametrize("patch,code", [
    ({"parent_run_id": "child-parent"}, "TASK_MISMATCH"),
    ({"node_id": "researcher-1"}, "TASK_MISMATCH"),
    ({"grant_id": "forged"}, "TASK_MISMATCH"),
    ({"policy_id": "forged"}, "TASK_MISMATCH"),
])
def test_delegation_binding(setup, patch, code):
    store, fixture = setup
    with pytest.raises(CollaborationError) as error:
        store.request("task-1", "start", delegation={**fixture["delegation"], **patch})
    assert error.value.code == code
    assert store.request("task-1", "view")["children"] == []


@pytest.mark.parametrize("mode", ["cancel", "recover", "timeout"])
def test_late_submission_never_revives_terminal_attempt(setup, mode):
    store, fixture = setup
    store.request("task-1", "start", delegation=fixture["delegation"])
    if mode == "timeout":
        # Controlled clock transition in the isolated ledger; no real sleep.
        with store._connect() as db:
            state = json.loads(db.execute("SELECT payload FROM controls").fetchone()[0])
            state["deadline"] = 0
            db.execute("UPDATE controls SET payload=?", (encode_json(state),))
    else:
        store.request("task-1", mode)
    with pytest.raises(CollaborationError) as error:
        submit(store, fixture)
    assert error.value.code == "INVALID_STATE"
    state = store.request("task-1", "view")
    assert state["status"] in {"cancelled", "blocked", "timed_out"}
    assert state["children"][0]["review"] is None
    store.request("task-1", "event", event={"kind": "model_settled", "role": "reviewer", "late": True, "usage": {"total_tokens": 5}})
    assert store.request("task-1", "events")[-1]["usage"]["total_tokens"] == 5


def test_rejected_result_has_no_partial_receipt_and_escalation_cannot_pass(setup):
    store, fixture = setup
    d = fixture["delegation"]
    store.request("task-1", "start", delegation=d)
    for patch in ({"task_id": "other"}, {"child_task_id": "other"}, {"attempt_id": "other"},
                  {"candidate": {**fixture["candidate"], "revision": 99}}):
        with pytest.raises(CollaborationError):
            submit(store, fixture, {**fixture["review"], **patch})
    with pytest.raises(CollaborationError):
        submit(store, fixture, identity={"role": "researcher"})
    assert store.request("task-1", "view")["children"][0]["review"] is None
    store.request("task-1", "escalate", child_task_id=d["child_task_id"])
    with pytest.raises(CollaborationError) as error:
        submit(store, fixture)
    assert error.value.code == "FINALIZATION_BLOCKED"
    blocked = copy.deepcopy(fixture["review"])
    blocked.update(status="blocked", reason="Needs parent clarification")
    submit(store, fixture, blocked)
    store.request("task-1", "finish_child", child_task_id=d["child_task_id"], status="done")
    with pytest.raises(CollaborationError):
        store.request("task-1", "finish_root", status="done")


def test_child_deadline_and_policy_cannot_widen_on_restart(setup):
    store, fixture = setup
    with pytest.raises(CollaborationError):
        store.register("task-1", fixture["candidate"], {"scopes": SCOPES + ["secret.write"], "operations": []})
    expired = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    with pytest.raises(CollaborationError):
        store.request("task-1", "start", delegation={**fixture["delegation"], "deadline": expired})
    assert store.request("task-1", "view")["children"] == []


def test_candidate_change_invalidates_previous_approval_without_resetting_rounds(setup):
    store, fixture = setup
    d = fixture["delegation"]
    store.request("task-1", "start", delegation=d)
    submit(store, fixture)
    store.request("task-1", "finish_child", child_task_id=d["child_task_id"], status="done")
    package = store.evidence.candidate("task-1", fixture["candidate"])
    package["revision"] = 2
    package["assumptions"].append("A new candidate requires another review.")
    next_candidate = store.evidence.save_candidate(package, expected_revision=1)
    store.request("task-1", "candidate", candidate=next_candidate)
    with pytest.raises(CollaborationError) as error:
        store.request("task-1", "finish_root", status="done")
    assert error.value.code == "FINALIZATION_BLOCKED"
    with pytest.raises(CollaborationError):
        store.request("task-1", "candidate", candidate=fixture["candidate"])
    assert len(store.request("task-1", "view")["children"]) == 1
    store.request("task-1", "start", delegation={**d, "child_task_id": "next", "attempt_id": "next", "round": 2, "candidate": next_candidate})
