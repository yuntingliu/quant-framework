
"""Trusted application entry point for finalizing reviewed collaboration tasks.

Only trusted application code should call this service. It does not accept
caller-supplied review approvals or publication artifacts.
"""

from __future__ import annotations

import copy
from typing import Any

from alphalab.collaboration import CollaborationError, ControlStore, EvidenceStore
from apps.api.services.collaboration_publication_service import (
    destination_from_workspace,
    discover_local_host_origin,
    fetch_local_workspace_snapshot,
    publish_final_output,
    reconcile_publication,
)


async def finalize_collaboration_task(
    *,
    task_id: str,
    idempotency_key: str,
    control_store: ControlStore,
    evidence_store: EvidenceStore,
    host_origin: str | None = None,
    publication_secret: str | None = None,
    connection_file: str | None = None,
) -> dict[str, Any]:
    """Finalize an approved task or reconcile its existing publication."""

    if not isinstance(task_id, str) or not task_id.strip():
        raise CollaborationError("INVALID_CONTRACT")
    if not isinstance(idempotency_key, str) or not idempotency_key.strip():
        raise CollaborationError("INVALID_CONTRACT")

    # Read only trusted persisted state, never a caller-supplied review.
    state = control_store.request(task_id, "view")

    # Reconciliation must precede destination discovery.
    # Otherwise an already committed Host publication could make a retry
    # appear stale.
    try:
        existing = control_store.get_publication(
            task_id, idempotency_key=idempotency_key
        )
    except CollaborationError as exc:
        if exc.code != "TASK_MISMATCH":
            raise
        existing = None

    if existing is not None:
        if existing["status"] == "committed":
            return existing["receipt"]

        if existing["status"] == "rejected":
            raise CollaborationError("FINALIZATION_BLOCKED")

        if existing["status"] != "pending":
            raise CollaborationError("INVALID_STATE")

        # Resolve the origin from the same private connection file used by
        # snapshot discovery. An explicit override must match that connection.
        connection_origin = discover_local_host_origin(connection_file)
        if host_origin is not None and host_origin.rstrip("/") != connection_origin:
            raise CollaborationError("ACCESS_DENIED")
        resolved_origin = connection_origin
        return await reconcile_publication(
            control_store=control_store,
            task_id=task_id,
            idempotency_key=idempotency_key,
            host_origin=resolved_origin,
            publication_secret=publication_secret,
        )

    if state["status"] != "completed":
        raise CollaborationError("FINALIZATION_BLOCKED")

    children = state["children"]
    if not children:
        raise CollaborationError("FINALIZATION_BLOCKED")

    latest = children[-1]
    review = latest.get("review")

    if (
        latest["status"] != "completed"
        or latest.get("needs_parent_escalation")
        or not isinstance(review, dict)
        or review.get("status") != "pass"
        or review.get("candidate") != state["candidate"]
        or review.get("manifest") != state["manifest"]
    ):
        raise CollaborationError("FINALIZATION_BLOCKED")

    context = evidence_store.candidate_context(
        task_id, state["candidate"]
    )

    if context["manifest_ref"] != state["manifest"]:
        raise CollaborationError("STALE_CANDIDATE")

    package = context["research_package"]
    publication_ref = package["publication"]
    publication = context["evidence"][publication_ref["artifact_id"]]

    outputs = copy.deepcopy(publication["content"]["outputs"])
    request_id = outputs["workspaceResult"]["requestId"]
    report_id = outputs["workspaceResult"]["reportId"]

    connection_origin = discover_local_host_origin(connection_file)
    if host_origin is not None and host_origin.rstrip("/") != connection_origin:
        raise CollaborationError("ACCESS_DENIED")
    resolved_origin = connection_origin
    workspace = await fetch_local_workspace_snapshot(
        connection_file=connection_file
    )
    destination = destination_from_workspace(workspace, report_id)

    correction = state.get("correction")

    request = {
        "schema_version": 1,
        "task_id": task_id,
        "request_id": request_id,
        "idempotency_key": idempotency_key,
        "expected_task_version": state["version"],
        "candidate": state["candidate"],
        "manifest": state["manifest"],
        "review_submission_id": review["submission_id"],
        "correction_response_id": (
            correction["response_id"] if correction else None
        ),
        "draft_report": package["draft_report"],
        "payload_sha256": publication["sha256"],
        "destination": destination,
        "outputs": outputs,
    }

    return await publish_final_output(
        request=request,
        control_store=control_store,
        evidence_store=evidence_store,
        destination=destination,
        host_origin=resolved_origin,
        publication_secret=publication_secret,
    )
