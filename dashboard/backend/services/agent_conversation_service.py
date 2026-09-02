"""Durable single-workspace storage for shared Agent conversations."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

MAX_SHARED_CONVERSATIONS = 100
MAX_MESSAGES_PER_CONVERSATION = 80
MAX_CONVERSATION_BYTES = 4_000_000

_SCHEMA = """
CREATE TABLE IF NOT EXISTS agent_conversations (
    id TEXT PRIMARY KEY,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_agent_conversations_updated
ON agent_conversations(updated_at DESC);
"""


def _encoded(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _merge_messages(
    stored: list[dict[str, Any]],
    incoming: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    messages: dict[str, dict[str, Any]] = {}
    for message in [*stored, *incoming]:
        message_id = message.get("id")
        if not isinstance(message_id, str):
            continue
        previous = messages.get(message_id)
        if previous is None:
            messages[message_id] = message
            continue
        merged = {**previous, **message}
        if "artifacts" not in message and "artifacts" in previous:
            merged["artifacts"] = previous["artifacts"]
        messages[message_id] = merged
    return sorted(
        messages.values(),
        key=lambda item: (str(item.get("createdAt", "")), str(item.get("id", ""))),
    )[-MAX_MESSAGES_PER_CONVERSATION:]


def _newer_checkpoint(
    stored: dict[str, Any] | None,
    incoming: dict[str, Any] | None,
) -> dict[str, Any] | None:
    candidates = [item for item in (stored, incoming) if isinstance(item, dict)]
    if not candidates:
        return None
    return max(candidates, key=lambda item: str(item.get("updatedAt", "")))


def merge_conversation(
    stored: dict[str, Any] | None,
    incoming: dict[str, Any],
) -> dict[str, Any]:
    """Merge a stale browser snapshot without dropping messages from another browser."""

    if stored is None:
        merged = dict(incoming)
        merged["messages"] = list(incoming.get("messages", []))[-MAX_MESSAGES_PER_CONVERSATION:]
    else:
        newer = incoming if incoming["updatedAt"] >= stored["updatedAt"] else stored
        messages = _merge_messages(
            list(stored.get("messages", [])),
            list(incoming.get("messages", [])),
        )
        updated_at = max(
            str(stored["updatedAt"]),
            str(incoming["updatedAt"]),
            *(str(item.get("createdAt", "")) for item in messages),
        )
        checkpoint = _newer_checkpoint(
            stored.get("researchCheckpoint"),
            incoming.get("researchCheckpoint"),
        )
        merged = {
            "id": incoming["id"],
            "title": newer["title"],
            "createdAt": min(str(stored["createdAt"]), str(incoming["createdAt"])),
            "updatedAt": updated_at,
            "messages": messages,
            **({"researchCheckpoint": checkpoint} if checkpoint else {}),
        }

    while len(_encoded(merged).encode("utf-8")) > MAX_CONVERSATION_BYTES:
        messages = list(merged.get("messages", []))
        if len(messages) <= 1:
            raise ValueError("Agent conversation exceeds the shared storage limit")
        merged["messages"] = messages[1:]
    return merged


class AgentConversationStore:
    """SQLite-backed shared history for the one AlphaLab publication workspace."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self.path), timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 30000")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection

    def list_conversations(self) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT payload_json
                FROM agent_conversations
                ORDER BY updated_at DESC, id ASC
                LIMIT ?
                """,
                (MAX_SHARED_CONVERSATIONS,),
            ).fetchall()
        conversations: list[dict[str, Any]] = []
        for row in rows:
            try:
                value = json.loads(row["payload_json"])
            except (json.JSONDecodeError, TypeError):
                continue
            if isinstance(value, dict):
                conversations.append(value)
        return conversations

    def upsert(self, incoming: dict[str, Any]) -> dict[str, Any]:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT payload_json FROM agent_conversations WHERE id = ?",
                (incoming["id"],),
            ).fetchone()
            stored: dict[str, Any] | None = None
            if row is not None:
                try:
                    candidate = json.loads(row["payload_json"])
                    stored = candidate if isinstance(candidate, dict) else None
                except (json.JSONDecodeError, TypeError):
                    stored = None
            merged = merge_conversation(stored, incoming)
            connection.execute(
                """
                INSERT INTO agent_conversations (id, payload_json, created_at, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    payload_json = excluded.payload_json,
                    created_at = excluded.created_at,
                    updated_at = excluded.updated_at
                """,
                (
                    merged["id"],
                    _encoded(merged),
                    merged["createdAt"],
                    merged["updatedAt"],
                ),
            )
            connection.execute(
                """
                DELETE FROM agent_conversations
                WHERE id IN (
                    SELECT id FROM agent_conversations
                    ORDER BY updated_at DESC, id ASC
                    LIMIT -1 OFFSET ?
                )
                """,
                (MAX_SHARED_CONVERSATIONS,),
            )
        return merged
