"""Trusted final-output publication coordinator for AlphaLab collaboration.

The Python service validates and records the publication intent in ControlStore.
The Conexus Local Host then atomically commits the report, three Harness outputs,
report history, workspace revision and a durable receipt in its own state store.
The two stores are reconciled by the idempotency key; they are not one distributed
transaction.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit

import httpx

from alphalab.collaboration import (
    CollaborationError,
    ControlStore,
    EvidenceStore,
    validate_contract,
)
from apps.api.services.workspace_output_service import prepare_workspace_outputs

PUBLICATION_PATH = "/api/alphalab/publications"
PUBLICATION_CONTENT_TYPE = "application/vnd.alphalab.publication+json"


def _read_local_host_connection(
    connection_file: str | Path | None = None,
    *,
    expected_slug: str | None = None,
) -> dict:
    """Read and validate the private Local Host connection file."""

    configured = connection_file or os.getenv("ALPHALAB_AGENT_CONNECTION_FILE", "").strip()
    if not configured:
        raise CollaborationError("STORAGE_UNAVAILABLE")
    path = Path(configured).expanduser()
    if path.is_symlink() or not path.is_file():
        raise CollaborationError("STORAGE_UNAVAILABLE")
    try:
        connection = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CollaborationError("STORAGE_UNAVAILABLE") from exc
    if not isinstance(connection, dict):
        raise CollaborationError("INVALID_CONTRACT")

    slug = expected_slug or os.getenv("CONEXUS_PUBLICATION_SLUG", "alphalab-research-agent")
    if connection.get("slug") != slug:
        raise CollaborationError("TASK_MISMATCH")
    if not isinstance(connection.get("token"), str) or len(connection["token"]) < 24:
        raise CollaborationError("ACCESS_DENIED")
    if type(connection.get("pid")) is not int or connection["pid"] <= 0:
        raise CollaborationError("INVALID_CONTRACT")

    origin = connection.get("origin")
    if not isinstance(origin, str) or len(origin) > 300:
        raise CollaborationError("INVALID_CONTRACT")
    try:
        parsed = urlsplit(origin)
        port = parsed.port
    except ValueError as exc:
        raise CollaborationError("INVALID_CONTRACT") from exc
    if (parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
            or port is None or not 1 <= port <= 65535 or parsed.username or parsed.password
            or parsed.path not in {"", "/"} or parsed.query or parsed.fragment):
        raise CollaborationError("ACCESS_DENIED")
    return {"origin": f"http://{parsed.netloc}", "slug": slug, "token": connection["token"]}


def discover_local_host_origin(
    connection_file: str | Path | None = None,
    *,
    expected_slug: str | None = None,
) -> str:
    """Read the Local Host connection file without returning its bearer token."""

    return _read_local_host_connection(
        connection_file, expected_slug=expected_slug
    )["origin"]


async def fetch_local_workspace_snapshot(
    *,
    connection_file: str | Path | None = None,
    expected_slug: str | None = None,
    client: httpx.AsyncClient | None = None,
) -> dict:
    """Fetch the authenticated Local Host snapshot for destination preflight."""

    connection = _read_local_host_connection(
        connection_file, expected_slug=expected_slug
    )
    path = f"/api/public/harnesses/{quote(connection['slug'], safe='')}/workspace"
    headers = {"accept": "application/json", "authorization": f"Bearer {connection['token']}"}

    async def fetch(http: httpx.AsyncClient) -> dict:
        try:
            response = await http.get(f"{connection['origin']}{path}", headers=headers)
            response.raise_for_status()
            workspace = response.json()["workspace"]
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
            raise CollaborationError("STORAGE_UNAVAILABLE") from exc
        if (not isinstance(workspace, dict) or type(workspace.get("revision")) is not int
                or workspace["revision"] < 0 or not isinstance(workspace.get("nodes"), list)):
            raise CollaborationError("INTEGRITY_ERROR")
        return workspace

    if client is not None:
        return await fetch(client)
    async with httpx.AsyncClient(timeout=10.0, trust_env=False) as owned:
        return await fetch(owned)


def destination_from_workspace(workspace: dict, report_id: str) -> dict:
    """Derive a create/update destination from a trusted Local Host snapshot."""

    if not isinstance(report_id, str) or not report_id.strip():
        raise CollaborationError("INVALID_CONTRACT")
    matches = [node for node in workspace["nodes"] if node.get("id") == report_id]
    if len(matches) > 1:
        raise CollaborationError("INTEGRITY_ERROR")
    if not matches:
        return {
            "report_id": report_id,
            "mode": "create",
            "expected_workspace_revision": workspace["revision"],
            "expected_report_revision": None,
        }
    node = matches[0]
    if (node.get("type") not in {"note", "document"}
            or node.get("description") != "AlphaLab quantitative report"
            or not isinstance(node.get("values", {}).get("content"), str)):
        raise CollaborationError("VERSION_CONFLICT")
    revision = node.get("values", {}).get("revision", 1)
    if type(revision) is not int or revision < 1:
        raise CollaborationError("INTEGRITY_ERROR")
    return {
        "report_id": report_id,
        "mode": "update",
        "expected_workspace_revision": workspace["revision"],
        "expected_report_revision": revision,
    }


def build_finalization_context(
    *,
    control_store: ControlStore,
    evidence_store: EvidenceStore,
    task_id: str,
    destination: dict,
    expected_task_version: int,
) -> dict:
    """Construct trusted context for final_output_request validation."""

    state = control_store.request(task_id, "view")
    if state["status"] != "completed" or not state["children"]:
        raise CollaborationError("FINALIZATION_BLOCKED")

    latest = state["children"][-1]
    review = latest.get("review")
    if (
        latest["status"] != "completed"
        or latest.get("needs_parent_escalation")
        or review is None
        or review["status"] != "pass"
        or review["candidate"] != state["candidate"]
        or review["manifest"] != state["manifest"]
    ):
        raise CollaborationError("FINALIZATION_BLOCKED")

    context = evidence_store.candidate_context(task_id, state["candidate"])
    if context["manifest_ref"] != state["manifest"]:
        raise CollaborationError("STALE_CANDIDATE")
    if state["version"] != expected_task_version:
        raise CollaborationError("FINALIZATION_BLOCKED")

    return {
        **context,
        "delegation": latest["delegation"],
        "review": review,
        "correction": state.get("correction"),
        "task_state": {"status": "ready", "version": state["version"]},
        "destination": destination,
    }


def validate_finalization_preflight(
    *,
    request: dict,
    control_store: ControlStore,
    evidence_store: EvidenceStore,
    destination: dict,
) -> dict:
    """Validate the exact final-output request without publishing."""

    context = build_finalization_context(
        control_store=control_store,
        evidence_store=evidence_store,
        task_id=request["task_id"],
        destination=destination,
        expected_task_version=request["expected_task_version"],
    )
    validate_contract("final_output_request", request, context)
    return {
        "status": "ready",
        "task_id": request["task_id"],
        "candidate": request["candidate"],
        "manifest": request["manifest"],
        "review_submission_id": request["review_submission_id"],
        "idempotency_key": request["idempotency_key"],
        "destination": request["destination"],
    }


def _signed_headers(secret: str, method: str, path: str, body: bytes) -> dict[str, str]:
    if not isinstance(secret, str) or len(secret) < 32:
        raise CollaborationError("ACCESS_DENIED")
    timestamp = str(int(time.time()))
    message = f"{method}\n{path}\n{timestamp}\n".encode("utf-8") + body
    signature = hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()
    return {
        "accept": "application/json",
        "content-type": PUBLICATION_CONTENT_TYPE if method == "POST" else "application/json",
        "x-alphalab-publication-timestamp": timestamp,
        "x-alphalab-publication-signature": signature,
    }


def _report_bytes(evidence_store: EvidenceStore, request: dict) -> bytes:
    context = evidence_store.candidate_context(request["task_id"], request["candidate"])
    reference = request["draft_report"]
    artifact = context["evidence"].get(reference["artifact_id"])
    if not artifact or artifact.get("sha256") != reference["sha256"]:
        raise CollaborationError("STALE_CANDIDATE")
    content = artifact.get("content")
    if isinstance(content, str):
        raw = content.encode("utf-8")
    elif isinstance(content, bytes):
        raw = content
    else:
        raise CollaborationError("INTEGRITY_ERROR")
    if hashlib.sha256(raw).hexdigest() != reference["sha256"]:
        raise CollaborationError("INTEGRITY_ERROR")
    return raw


def _decode_receipt(response: httpx.Response) -> dict:
    try:
        payload = response.json()
        receipt = payload["receipt"]
    except (ValueError, KeyError, TypeError) as exc:
        raise CollaborationError("INTEGRITY_ERROR") from exc
    if not isinstance(receipt, dict):
        raise CollaborationError("INTEGRITY_ERROR")
    return receipt


async def _fetch_receipt(
    *,
    client: httpx.AsyncClient,
    host_origin: str,
    secret: str,
    idempotency_key: str,
) -> dict | None:
    path = f"{PUBLICATION_PATH}/{quote(idempotency_key, safe='')}"
    headers = _signed_headers(secret, "GET", path, b"")
    response = await client.get(f"{host_origin.rstrip('/')}{path}", headers=headers)
    if response.status_code == 404:
        return None
    if response.status_code >= 400:
        raise CollaborationError("STORAGE_UNAVAILABLE")
    return _decode_receipt(response)


async def _post_envelope(
    *,
    client: httpx.AsyncClient,
    host_origin: str,
    secret: str,
    body: bytes,
) -> dict:
    headers = _signed_headers(secret, "POST", PUBLICATION_PATH, body)
    response = await client.post(
        f"{host_origin.rstrip('/')}{PUBLICATION_PATH}",
        content=body,
        headers=headers,
    )
    if response.status_code >= 400:
        try:
            error_payload = response.json().get("error", {})
            code = error_payload.get("code")
        except (ValueError, AttributeError):
            code = None
        # These codes are returned only by the Host's pre-commit validation
        # paths. A timeout or 5xx is deliberately not treated as a rejection.
        definite = {
            "ACCESS_DENIED", "INVALID_CONTRACT", "INTEGRITY_ERROR",
            "INVALID_WORKSPACE_OUTPUTS", "DUPLICATE_CONFLICT", "INVALID_STATE",
            "VERSION_CONFLICT", "EVIDENCE_LIMIT", "PUBLICATION_NOT_CONFIGURED",
        }
        if response.status_code in {400, 401, 403, 409, 422, 503} and code in definite:
            failure = CollaborationError(code)
            failure.confirmed_not_committed = True
            raise failure
        raise CollaborationError("STORAGE_UNAVAILABLE")
    return _decode_receipt(response)


async def publish_final_output(
    *,
    request: dict,
    control_store: ControlStore,
    evidence_store: EvidenceStore,
    destination: dict,
    host_origin: str | None = None,
    publication_secret: str | None = None,
    client: httpx.AsyncClient | None = None,
) -> dict:
    """Publish one reviewed report and reconcile its durable Host receipt.

    ``destination`` must come from a trusted Local Host workspace snapshot. The
    Host independently checks both revisions again inside its atomic commit.
    A transport timeout never aborts the intent because the commit outcome may
    be unknown.
    """

    # ControlStore performs fresh preflight inside the same SQLite writer
    # transaction that records the intent. Its idempotency lookup deliberately
    # runs first, so a committed retry still returns its original receipt.
    intent = control_store.prepare_publication(
        request["task_id"], request, destination=destination
    )
    if intent["status"] == "committed":
        return intent["receipt"]
    if intent["status"] != "pending":
        raise CollaborationError("INVALID_STATE")

    host_origin = host_origin or discover_local_host_origin()
    publication_secret = publication_secret or os.getenv("ALPHALAB_PUBLICATION_HMAC_SECRET", "")

    try:
        report_raw = _report_bytes(evidence_store, request)
    except CollaborationError as exc:
        control_store.abort_publication(
            request["task_id"], intent["intent_id"],
            error_code=exc.code,
            reason="Frozen report bytes failed trusted evidence verification.",
            confirmed_not_committed=True,
        )
        raise

    prepared = prepare_workspace_outputs(request["request_id"], request["outputs"])
    if prepared.get("status") != "succeeded":
        control_store.abort_publication(
            request["task_id"], intent["intent_id"],
            error_code="INVALID_WORKSPACE_OUTPUTS",
            reason=prepared.get("error_summary", "Workspace output validation failed."),
            confirmed_not_committed=True,
        )
        raise CollaborationError("INVALID_WORKSPACE_OUTPUTS")

    envelope = {
        "schema_version": 1,
        "intent": intent,
        "request": request,
        "report_markdown": report_raw.decode("utf-8"),
        "report_sha256": hashlib.sha256(report_raw).hexdigest(),
        "prepared_outputs": prepared,
    }
    body = json.dumps(
        envelope, ensure_ascii=False, allow_nan=False, separators=(",", ":")
    ).encode("utf-8")

    async def deliver(http: httpx.AsyncClient) -> dict:
        try:
            return await _post_envelope(
                client=http, host_origin=host_origin,
                secret=publication_secret, body=body,
            )
        except httpx.RequestError:
            # The Host may have committed and lost only the response. Query the
            # durable receipt before attempting the same idempotent POST again.
            receipt = await _fetch_receipt(
                client=http, host_origin=host_origin,
                secret=publication_secret,
                idempotency_key=request["idempotency_key"],
            )
            if receipt is not None:
                return receipt
            try:
                return await _post_envelope(
                    client=http, host_origin=host_origin,
                    secret=publication_secret, body=body,
                )
            except httpx.RequestError:
                receipt = await _fetch_receipt(
                    client=http, host_origin=host_origin,
                    secret=publication_secret,
                    idempotency_key=request["idempotency_key"],
                )
                if receipt is not None:
                    return receipt
                # Leave the intent pending for later reconciliation.
                raise CollaborationError("STORAGE_UNAVAILABLE")

    try:
        if client is not None:
            receipt = await deliver(client)
        else:
            async with httpx.AsyncClient(timeout=30.0, trust_env=False) as owned:
                receipt = await deliver(owned)
    except CollaborationError as exc:
        if getattr(exc, "confirmed_not_committed", False):
            control_store.abort_publication(
                request["task_id"], intent["intent_id"],
                error_code=exc.code,
                reason="Local Host explicitly rejected the publication before commit.",
                confirmed_not_committed=True,
            )
        raise

    return control_store.record_publication_receipt(
        request["task_id"], intent["intent_id"], receipt
    )


async def reconcile_publication(
    *,
    control_store: ControlStore,
    task_id: str,
    idempotency_key: str,
    host_origin: str | None = None,
    publication_secret: str | None = None,
    client: httpx.AsyncClient | None = None,
) -> dict:
    """Recover ControlStore after a Host commit or interrupted HTTP response."""

    host_origin = host_origin or discover_local_host_origin()
    publication_secret = publication_secret or os.getenv("ALPHALAB_PUBLICATION_HMAC_SECRET", "")
    intent = control_store.get_publication(
        task_id, idempotency_key=idempotency_key
    )
    if intent["status"] == "committed":
        return intent["receipt"]
    if intent["status"] != "pending":
        return intent

    async def fetch(http: httpx.AsyncClient) -> dict | None:
        return await _fetch_receipt(
            client=http, host_origin=host_origin,
            secret=publication_secret, idempotency_key=idempotency_key,
        )

    if client is not None:
        receipt = await fetch(client)
    else:
        async with httpx.AsyncClient(timeout=10.0, trust_env=False) as owned:
            receipt = await fetch(owned)
    if receipt is None:
        return {"status": "pending", "intent_id": intent["intent_id"]}
    return control_store.record_publication_receipt(
        task_id, intent["intent_id"], receipt
    )
