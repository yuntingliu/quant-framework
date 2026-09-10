"""Recover published Runs and deliver chat replies independently of browser lifetime."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import quote

import httpx

from alphalab.utils.paths import RUNTIME_DIR
from apps.api.services.agent_conversation_service import (
    TERMINAL_RUN_STATES,
    AgentConversationStore,
)
from apps.api.services.workspace_output_service import validate_workspace_delivery

logger = logging.getLogger(__name__)


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


def _delivery(run: dict, workspace: dict) -> tuple[list[dict], dict | None]:
    changes = run.get("nodeChanges") or {}
    changed = set(changes.get("created", [])) | set(changes.get("updated", []))
    artifacts = []
    checkpoint = {"version": 1, "runId": run["id"], "updatedAt": run.get("completedAt") or run["createdAt"]}
    remaining = 1_000_000
    for node in workspace.get("nodes", []):
        if node.get("id") not in changed:
            continue
        if node.get("updatedByRunId") != run["id"]:
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
    return artifacts, checkpoint if len(checkpoint) > 3 else None


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
        artifacts, checkpoint = _delivery(run, workspace)
        store.record_run(scope, run, artifacts=artifacts, checkpoint=checkpoint, delivered=True)
    except (httpx.HTTPError, ValueError, KeyError):
        # Keep the durable delivery pending so the background worker can enrich it later.
        logger.warning("Agent reply saved; workspace artifacts will be retried")


async def reconcile_runs() -> None:
    if os.getenv("ALPHALAB_AGENT_MODE") == "off":
        return
    rows = conversation_store().pending_runs(run_scope())
    if not rows:
        return
    semaphore = asyncio.Semaphore(4)
    async with httpx.AsyncClient(timeout=5, trust_env=False) as client:
        async def reconcile(row):
            async with semaphore:
                try:
                    snapshot = json.loads(row["snapshot_json"])
                    if snapshot["status"] not in TERMINAL_RUN_STATES:
                        response = await client.get(
                            f"{web_origin()}/api/public/runs/{quote(row['run_id'], safe='')}",
                            headers={"Authorization": f"Bearer {row['access_token']}"},
                        )
                        response.raise_for_status()
                        snapshot = response.json()["run"]
                    await record_snapshot(snapshot, client)
                except (httpx.HTTPError, ValueError, KeyError):
                    # Availability failures never turn an active Run into a made-up terminal state.
                    pass
        await asyncio.gather(*(reconcile(row) for row in rows))


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
