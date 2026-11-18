from __future__ import annotations

import copy

import pytest

from alphalab.collaboration import (
    CollaborationError,
    ControlStore,
    encode_json,
    sha256,
)
from scripts.prepare_collaboration_acceptance import prepare

from tests.collaboration.correction_fixture import (
    prepare_corrected_candidate,
    make_correction,
)


SCOPES = [
    "model.invoke",
    "evidence.read",
    "review.submit",
    "review.delegate",
]


@pytest.fixture
def correction_setup(tmp_path):
    fixture = prepare(tmp_path, "seeded-formula")
    version = fixture["versions"][0]

    store = ControlStore(
        tmp_path / "control",
        tmp_path / "evidence",
    )

    store.register(
        fixture["task_id"],
        version["candidate"],
        {
            "scopes": SCOPES,
            "operations": [],
        },
    )

    store.request(fixture["task_id"], "begin")

    return store, fixture


def submit_initial_review(store, fixture):
    task_id = fixture["task_id"]
    version = fixture["versions"][0]
    delegation = version["delegation"]
    review = version["review"]

    store.request(
        task_id,
        "start",
        delegation=delegation,
    )

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

    return review


def make_second_review(fixture, new_candidate, new_manifest):
    first = fixture["versions"][0]
    original_delegation = first["delegation"]
    original_review = first["review"]

    # Create a distinct reviewer attempt for round 2.
    delegation = copy.deepcopy(original_delegation)
    delegation.update({
        "child_task_id": "review-2",
        "attempt_id": "attempt-2",
        "round": 2,
        "candidate": new_candidate,
        "manifest": new_manifest,
    })

    # Construct a passing review of the corrected candidate.
    review = copy.deepcopy(original_review)
    review.update({
        "submission_id": "submission-2",
        "child_task_id": delegation["child_task_id"],
        "attempt_id": delegation["attempt_id"],
        "round": 2,
        "candidate": new_candidate,
        "manifest": new_manifest,
        "status": "pass",
        "findings": [],
        "missing_evidence": [],
        "reason": None,
    })

    # Replace historical evidence citations with references
    # from the corrected manifest.
    # Keep the original review unchanged.
    return delegation, review


def submit_review(store, fixture, delegation, review):
    task_id = fixture["task_id"]

    raw = encode_json(review).decode("utf-8")

    identity = {
        "role": "reviewer",
        "task_id": task_id,
        "node_id": delegation["node_id"],
        "child_task_id": delegation["child_task_id"],
        "attempt_id": delegation["attempt_id"],
    }

    return store.request(
        task_id,
        "submit",
        child_task_id=delegation["child_task_id"],
        submission_id=review["submission_id"],
        payload_json=raw,
        sha256=sha256(raw.encode("utf-8")),
        identity=identity,
    )


@pytest.fixture
def ready_correction(correction_setup):
    store, fixture = correction_setup

    submit_initial_review(store, fixture)

    new_candidate, new_manifest = prepare_corrected_candidate(
        store, fixture
    )

    correction = make_correction(
        fixture, new_candidate, new_manifest, store
    )

    return store, fixture, new_candidate, correction


def test_valid_correction_advances_candidate(correction_setup):
    store, fixture = correction_setup

    submit_initial_review(store, fixture)

    new_candidate, new_manifest = prepare_corrected_candidate(
        store,
        fixture,
    )

    correction = make_correction(
        fixture,
        new_candidate,
        new_manifest,
        store,
    )

    result = store.request(
        fixture["task_id"],
        "candidate",
        candidate=new_candidate,
        correction=correction,
    )

    assert result["candidate"] == new_candidate
    assert result["manifest"] == new_manifest

    state = store.request(
        fixture["task_id"],
        "view",
    )

    assert state["candidate"] == new_candidate
    assert state["manifest"] == new_manifest

    assert len(state["children"]) == 1
    assert state["children"][0]["review"]["status"] == "findings"

    assert len(state["corrections"]) == 1
    assert state["corrections"][0] == correction
    assert state["correction"] == correction


def test_missing_correction_rejected(ready_correction):
    store, fixture, candidate, _ = ready_correction

    with pytest.raises(CollaborationError) as error:
        store.request(
            fixture["task_id"],
            "candidate",
            candidate=candidate,
        )

    assert error.value.code == "INVALID_CORRECTION"
    assert store.request(fixture["task_id"], "view")["candidate"] == fixture["versions"][0]["candidate"]


def test_omitted_finding_rejected(ready_correction):
    store, fixture, candidate, correction = ready_correction

    invalid = copy.deepcopy(correction)
    invalid["dispositions"] = []

    with pytest.raises(CollaborationError) as error:
        store.request(
            fixture["task_id"],
            "candidate",
            candidate=candidate,
            correction=invalid,
        )

    assert error.value.code == "INVALID_CORRECTION"
    assert store.request(fixture["task_id"], "view")["candidate"] == fixture["versions"][0]["candidate"]


def test_unchanged_evidence_rejected(ready_correction):
    store, fixture, candidate, correction = ready_correction

    invalid = copy.deepcopy(correction)
    invalid["dispositions"][0]["changed_artifact_ids"] = ["returns"]

    with pytest.raises(CollaborationError) as error:
        store.request(
            fixture["task_id"],
            "candidate",
            candidate=candidate,
            correction=invalid,
        )

    assert error.value.code == "INVALID_CORRECTION"
    assert store.request(fixture["task_id"], "view")["candidate"] == fixture["versions"][0]["candidate"]


def test_unresolved_disagreement_rejected(ready_correction):
    store, fixture, candidate, correction = ready_correction

    invalid = copy.deepcopy(correction)
    invalid["dispositions"][0]["status"] = "unresolved"
    invalid["remaining_disagreements"] = ["finding-1"]

    with pytest.raises(CollaborationError) as error:
        store.request(
            fixture["task_id"],
            "candidate",
            candidate=candidate,
            correction=invalid,
        )

    assert error.value.code == "INVALID_CORRECTION"
    assert store.request(fixture["task_id"], "view")["candidate"] == fixture["versions"][0]["candidate"]


def test_stale_candidate_rejected(ready_correction):
    store, fixture, _, correction = ready_correction

    with pytest.raises(CollaborationError) as error:
        store.request(
            fixture["task_id"],
            "candidate",
            candidate=fixture["versions"][0]["candidate"],
            correction=correction,
        )

    assert error.value.code == "STALE_CANDIDATE"


def test_cancelled_task_cannot_advance(ready_correction):
    store, fixture, candidate, correction = ready_correction

    store.request(fixture["task_id"], "cancel")

    with pytest.raises(CollaborationError) as error:
        store.request(
            fixture["task_id"],
            "candidate",
            candidate=candidate,
            correction=correction,
        )

    assert error.value.code == "INVALID_STATE"
    assert store.request(fixture["task_id"], "view")["status"] == "cancelled"


def test_corrected_candidate_requires_new_review(ready_correction):
    store, fixture, new_candidate, correction = ready_correction

    store.request(
        fixture["task_id"],
        "candidate",
        candidate=new_candidate,
        correction=correction,
    )

    # The first review contains findings, not approval.
    # Candidate revision 2 must receive a new review.
    with pytest.raises(CollaborationError) as error:
        store.request(
            fixture["task_id"],
            "finish_root",
            status="done",
        )

    assert error.value.code == "FINALIZATION_BLOCKED"

    state = store.request(fixture["task_id"], "view")
    assert state["status"] == "running"
    assert state["candidate"] == new_candidate
    assert len(state["children"]) == 1


def test_second_review_can_complete_task(ready_correction):
    store, fixture, new_candidate, correction = ready_correction
    task_id = fixture["task_id"]

    result = store.request(
        task_id,
        "candidate",
        candidate=new_candidate,
        correction=correction,
    )

    new_manifest = result["manifest"]

    delegation, review = make_second_review(
        fixture, new_candidate, new_manifest
    )

    # Rebind coverage citations to the corrected manifest.
    context = store.evidence.candidate_context(
        task_id, new_candidate
    )
    descriptors = {
        item["artifact_id"]: item
        for item in context["manifest"]["artifacts"]
    }

    for coverage in review["coverage"]:
        for citation in coverage["evidence"]:
            citation["sha256"] = descriptors[
                citation["artifact_id"]
            ]["sha256"]

    store.request(
        task_id,
        "start",
        delegation=delegation,
    )

    submit_review(store, fixture, delegation, review)

    store.request(
        task_id,
        "finish_child",
        child_task_id=delegation["child_task_id"],
        status="done",
    )

    result = store.request(
        task_id,
        "finish_root",
        status="done",
    )

    assert result["status"] == "completed"

    state = store.request(task_id, "view")

    assert len(state["children"]) == 2
    assert state["children"][-1]["review"]["status"] == "pass"
    assert state["children"][-1]["review"]["candidate"] == new_candidate
    assert state["children"][-1]["review"]["manifest"] == new_manifest


def test_stale_second_review_rejected(ready_correction):
    store, fixture, new_candidate, correction = ready_correction
    task_id = fixture["task_id"]

    result = store.request(
        task_id,
        "candidate",
        candidate=new_candidate,
        correction=correction,
    )

    delegation, review = make_second_review(
        fixture, new_candidate, result["manifest"]
    )

    store.request(
        task_id,
        "start",
        delegation=delegation,
    )

    # Deliberately reference the original candidate.
    review["candidate"] = fixture["versions"][0]["candidate"]

    with pytest.raises(CollaborationError) as error:
        submit_review(store, fixture, delegation, review)

    assert error.value.code == "STALE_CANDIDATE"

    state = store.request(task_id, "view")
    assert state["children"][-1]["review"] is None
    assert state["status"] == "running"


def test_maximum_review_rounds_enforced(correction_setup):
    store, fixture = correction_setup
    task_id = fixture["task_id"]

    original = fixture["versions"][0]["delegation"]
    maximum = fixture["task"]["max_review_rounds"]

    # Exhaust the permitted reviewer rounds sequentially.
    for round_number in range(1, maximum + 1):
        delegation = copy.deepcopy(original)
        delegation.update({
            "child_task_id": f"review-limit-{round_number}",
            "attempt_id": f"attempt-limit-{round_number}",
            "round": round_number,
        })

        store.request(
            task_id,
            "start",
            delegation=delegation,
        )

        # End this child without an accepted review.
        store.request(
            task_id,
            "cancel_child",
            child_task_id=delegation["child_task_id"],
        )

    # A fourth reviewer must not be created.
    rejected = copy.deepcopy(original)
    rejected.update({
        "child_task_id": "review-limit-extra",
        "attempt_id": "attempt-limit-extra",
        "round": maximum + 1,
    })

    with pytest.raises(CollaborationError) as error:
        store.request(
            task_id,
            "start",
            delegation=rejected,
        )

    assert error.value.code == "CHILD_LIMIT"

    state = store.request(task_id, "view")
    assert len(state["children"]) == maximum
    assert all(
        child["status"] == "cancelled"
        for child in state["children"]
    )


def test_correction_history_survives_restart(ready_correction):
    store, fixture, candidate, correction = ready_correction
    task_id = fixture["task_id"]

    store.request(
        task_id,
        "candidate",
        candidate=candidate,
        correction=correction,
    )

    reopened = ControlStore(
        store.root,
        store.evidence.root,
    )

    state = reopened.request(task_id, "view")

    assert state["candidate"] == candidate
    assert state["corrections"] == [correction]
    assert state["correction"] == correction


def test_blocked_review_prevents_completion(correction_setup):
    store, fixture = correction_setup
    task_id = fixture["task_id"]
    version = fixture["versions"][0]

    delegation = version["delegation"]

    store.request(
        task_id,
        "start",
        delegation=delegation,
    )

    review = copy.deepcopy(version["review"])
    review["status"] = "blocked"
    review["reason"] = "Reviewer cannot complete verification."
    review["submission_id"] = "blocked-review-1"

    submit_review(store, fixture, delegation, review)

    store.request(
        task_id,
        "finish_child",
        child_task_id=delegation["child_task_id"],
        status="done",
    )

    with pytest.raises(CollaborationError) as error:
        store.request(
            task_id,
            "finish_root",
            status="done",
        )

    assert error.value.code == "FINALIZATION_BLOCKED"

    state = store.request(task_id, "view")
    assert state["status"] == "running"
    assert state["children"][-1]["review"]["status"] == "blocked"


def test_incomplete_review_prevents_completion(correction_setup):
    store, fixture = correction_setup
    task_id = fixture["task_id"]
    version = fixture["versions"][0]
    delegation = version["delegation"]

    store.request(
        task_id,
        "start",
        delegation=delegation,
    )

    review = copy.deepcopy(version["review"])
    review["status"] = "incomplete"
    review["reason"] = "Required evidence is unavailable."
    review["submission_id"] = "incomplete-review-1"
    review["missing_evidence"] = [
        "Point-in-time availability evidence is missing."
    ]

    submit_review(store, fixture, delegation, review)

    store.request(
        task_id,
        "finish_child",
        child_task_id=delegation["child_task_id"],
        status="done",
    )

    with pytest.raises(CollaborationError) as error:
        store.request(
            task_id,
            "finish_root",
            status="done",
        )

    assert error.value.code == "FINALIZATION_BLOCKED"

    state = store.request(task_id, "view")
    assert state["status"] == "running"
    assert state["children"][-1]["review"]["status"] == "incomplete"
