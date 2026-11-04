"""Task-scoped immutable evidence in a separate transactional SQLite database.

Only the trusted application owner receives EvidenceStore. Reviewers receive a
reader with fixed task/manifest grants. No filesystem path is an artifact ID.
"""

from __future__ import annotations

import re
import sqlite3
from contextlib import closing, contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from .contracts import (
    CollaborationError,
    decode_json,
    encode_json,
    require,
    resolve_locator,
    sha256,
    validate_contract,
)

MAX_ARTIFACT_BYTES = 2 * 1024 * 1024
MAX_BUNDLE_BYTES = 8 * 1024 * 1024
MAX_READ_BYTES = 128 * 1024
ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
DDL = """
CREATE TABLE IF NOT EXISTS tasks (
 task_id TEXT PRIMARY KEY, payload BLOB NOT NULL, manifest_revision INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS blobs (sha256 TEXT PRIMARY KEY, payload BLOB NOT NULL);
CREATE TABLE IF NOT EXISTS manifests (
 manifest_id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(task_id),
 revision INTEGER NOT NULL, sha256 TEXT NOT NULL, payload BLOB NOT NULL,
 UNIQUE(task_id, revision)
);
CREATE TABLE IF NOT EXISTS deliveries (
 task_id TEXT NOT NULL, delivery_key TEXT NOT NULL, request_sha256 TEXT NOT NULL,
 manifest_id TEXT NOT NULL REFERENCES manifests(manifest_id), PRIMARY KEY(task_id, delivery_key)
);
CREATE TABLE IF NOT EXISTS candidates (
 task_id TEXT NOT NULL REFERENCES tasks(task_id), candidate_id TEXT NOT NULL,
 revision INTEGER NOT NULL, sha256 TEXT NOT NULL, payload BLOB NOT NULL,
 PRIMARY KEY(task_id, candidate_id, revision)
);
"""


def _identifier(value):
    require(isinstance(value, str) and ID.fullmatch(value), "ACCESS_DENIED")


def _reference(value, identity):
    require(isinstance(value, dict) and set(value) == {identity, "revision", "sha256"}, "ACCESS_DENIED")
    _identifier(value[identity])
    require(type(value["revision"]) is int and 1 <= value["revision"] < 2**53 and
            isinstance(value["sha256"], str) and re.fullmatch(r"[a-f0-9]{64}", value["sha256"]), "ACCESS_DENIED")


def _safe_database(root: Path) -> Path:
    root = Path(root).absolute()
    database = root / "evidence.sqlite3"
    require(root.resolve() == root and database.resolve().parent == root and not database.is_symlink(), "ACCESS_DENIED")
    # SQLite may open sidecars itself. Reject an existing sidecar that redirects
    # outside this store instead of following an attacker-controlled symlink.
    for suffix in ("-journal", "-wal", "-shm"):
        require(not Path(str(database) + suffix).is_symlink(), "ACCESS_DENIED")
    return database


@contextmanager
def _connection(root: Path, *, write=False):
    database = _safe_database(root)
    connection = None
    try:
        if write:
            connection = sqlite3.connect(database, timeout=5, isolation_level=None)
        else:
            connection = sqlite3.connect(database.as_uri() + "?mode=ro", uri=True, timeout=5, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        if write:
            connection.execute("PRAGMA synchronous=FULL")
            connection.execute("BEGIN IMMEDIATE")
        yield connection
        if write:
            connection.commit()
    except sqlite3.Error:
        if connection and write:
            connection.rollback()
        raise CollaborationError("STORAGE_UNAVAILABLE") from None
    except Exception:
        if connection and write:
            connection.rollback()
        raise
    finally:
        if connection:
            connection.close()


def _content(raw: bytes, media_type: str):
    if media_type == "application/json":
        return decode_json(raw)
    if media_type in {"text/plain", "text/markdown"}:
        try:
            return raw.decode("utf-8")
        except UnicodeError:
            raise CollaborationError("INVALID_CONTRACT") from None
    return raw


@dataclass(frozen=True)
class EvidenceArtifact:
    """Host-collected bytes and provenance; no path or executable callback."""

    artifact_id: str
    content: bytes
    media_type: str
    kind: str
    source: dict
    locators: tuple[str, ...] = ("",)

    @classmethod
    def json(cls, artifact_id: str, value: Any, *, kind: str, source: dict, locators=("",)):
        return cls(artifact_id, encode_json(value, maximum=MAX_ARTIFACT_BYTES),
                   "application/json", kind, source, tuple(locators))

    def descriptor(self) -> dict:
        require(isinstance(self.content, bytes) and len(self.content) <= MAX_ARTIFACT_BYTES, "EVIDENCE_LIMIT")
        return {"artifact_id": self.artifact_id, "sha256": sha256(self.content),
                "byte_length": len(self.content), "media_type": self.media_type,
                "kind": self.kind, "source": self.source, "locators": list(self.locators)}


def _manifest_row(connection, task_id, manifest_id):
    row = connection.execute("SELECT * FROM manifests WHERE task_id=? AND manifest_id=?",
                             (task_id, manifest_id)).fetchone()
    require(row is not None, "ACCESS_DENIED")
    raw = bytes(row["payload"])
    require(sha256(raw) == row["sha256"], "INTEGRITY_ERROR")
    manifest = decode_json(raw)
    validate_contract("evidence_manifest", manifest)
    require(manifest["task_id"] == task_id and manifest["manifest_id"] == manifest_id and
            manifest["revision"] == row["revision"], "INTEGRITY_ERROR")
    return manifest, {"manifest_id": row["manifest_id"], "revision": row["revision"], "sha256": row["sha256"]}


def _artifact_bytes(connection, descriptor):
    row = connection.execute("SELECT payload FROM blobs WHERE sha256=?", (descriptor["sha256"],)).fetchone()
    require(row is not None, "INTEGRITY_ERROR")
    raw = bytes(row["payload"])
    require(len(raw) == descriptor["byte_length"] and sha256(raw) == descriptor["sha256"], "INTEGRITY_ERROR")
    return raw


class EvidenceStore:
    """Trusted writer facade. Use a dedicated runtime directory, never a business DB."""

    def __init__(self, root: Path):
        self.root = Path(root).absolute()
        _safe_database(self.root)
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        # DDL is idempotent; normal writer operations use separate transactions
        # and connections, including across processes on Windows.
        with closing(sqlite3.connect(_safe_database(self.root), timeout=5)) as connection:
            connection.executescript(DDL)

    def register_task(self, task: dict) -> None:
        validate_contract("task_envelope", task)
        raw = encode_json(task)
        with _connection(self.root, write=True) as connection:
            old = connection.execute("SELECT payload FROM tasks WHERE task_id=?", (task["task_id"],)).fetchone()
            if old:
                require(bytes(old["payload"]) == raw, "DUPLICATE_CONFLICT")
            else:
                connection.execute("INSERT INTO tasks(task_id,payload) VALUES (?,?)", (task["task_id"], raw))

    def freeze(self, task_id: str, *, expected_revision: int, delivery_key: str,
               artifacts: list[EvidenceArtifact], coverage: dict, omissions: list[str],
               code_identity: str | None = None, environment_identity: str | None = None) -> dict:
        _identifier(task_id)
        _identifier(delivery_key)
        require(type(expected_revision) is int and 0 <= expected_revision < 2**53)
        require(1 <= len(artifacts) <= 64, "EVIDENCE_LIMIT")
        descriptors = [artifact.descriptor() for artifact in artifacts]
        require(sum(item["byte_length"] for item in descriptors) <= MAX_BUNDLE_BYTES, "EVIDENCE_LIMIT")
        # In-memory staging validates all bytes/selectors before publishing.
        manifest = {"schema_version": 1, "task_id": task_id, "manifest_id": "manifest-" + uuid4().hex,
                    "revision": expected_revision + 1, "created_at": datetime.now(timezone.utc).isoformat(),
                    "code_identity": code_identity, "environment_identity": environment_identity,
                    "artifacts": descriptors, "coverage": coverage, "omissions": omissions}
        validate_contract("evidence_manifest", manifest)
        for descriptor, artifact in zip(descriptors, artifacts):
            content = _content(artifact.content, descriptor["media_type"])
            for locator in descriptor["locators"]:
                resolve_locator(content, locator, descriptor["media_type"])
        request_digest = sha256(encode_json({"expected_revision": expected_revision, "artifacts": descriptors,
                                            "coverage": coverage, "omissions": omissions,
                                            "code_identity": code_identity, "environment_identity": environment_identity}))
        raw = encode_json(manifest)
        with _connection(self.root, write=True) as connection:
            task = connection.execute("SELECT manifest_revision FROM tasks WHERE task_id=?", (task_id,)).fetchone()
            require(task is not None, "ACCESS_DENIED")
            previous = connection.execute("SELECT * FROM deliveries WHERE task_id=? AND delivery_key=?", (task_id, delivery_key)).fetchone()
            if previous:
                require(previous["request_sha256"] == request_digest, "DUPLICATE_CONFLICT")
                archived, reference = _manifest_row(connection, task_id, previous["manifest_id"])
                for descriptor in archived["artifacts"]:
                    _artifact_bytes(connection, descriptor)
                return reference
            require(task["manifest_revision"] == expected_revision, "VERSION_CONFLICT")
            for descriptor, artifact in zip(descriptors, artifacts):
                existing = connection.execute("SELECT payload FROM blobs WHERE sha256=?", (descriptor["sha256"],)).fetchone()
                if existing:
                    require(bytes(existing["payload"]) == artifact.content, "INTEGRITY_ERROR")
                else:
                    connection.execute("INSERT INTO blobs VALUES (?,?)", (descriptor["sha256"], artifact.content))
            connection.execute("INSERT INTO manifests VALUES (?,?,?,?,?)",
                               (manifest["manifest_id"], task_id, manifest["revision"], sha256(raw), raw))
            cursor = connection.execute("UPDATE tasks SET manifest_revision=? WHERE task_id=? AND manifest_revision=?",
                                        (manifest["revision"], task_id, expected_revision))
            require(cursor.rowcount == 1, "VERSION_CONFLICT")
            connection.execute("INSERT INTO deliveries VALUES (?,?,?,?)",
                               (task_id, delivery_key, request_digest, manifest["manifest_id"]))
        return {"manifest_id": manifest["manifest_id"], "revision": manifest["revision"], "sha256": sha256(raw)}

    def reader(self, task_id: str, manifests: list[dict]) -> EvidenceReader:
        return EvidenceReader(self.root, task_id, manifests)

    def task(self, task_id: str) -> dict:
        _identifier(task_id)
        with _connection(self.root) as connection:
            row = connection.execute("SELECT payload FROM tasks WHERE task_id=?", (task_id,)).fetchone()
            require(row is not None, "ACCESS_DENIED")
            return decode_json(bytes(row["payload"]))

    def save_candidate(self, package: dict, *, expected_revision: int) -> dict:
        """Archive an exact package revision; never execute or publish its content."""
        require(type(expected_revision) is int and 0 <= expected_revision < 2**53)
        require(package.get("revision") == expected_revision + 1, "VERSION_CONFLICT")
        reader = self.reader(package["task_id"], [package["manifest"]])
        context = reader.context(package["manifest"]["manifest_id"])
        validate_contract("research_package", package, context)
        raw, digest = encode_json(package), sha256(encode_json(package))
        with _connection(self.root, write=True) as connection:
            old = connection.execute("SELECT sha256 FROM candidates WHERE task_id=? AND candidate_id=? AND revision=?",
                                     (package["task_id"], package["candidate_id"], package["revision"])).fetchone()
            if old:
                require(old["sha256"] == digest, "VERSION_CONFLICT")
            else:
                current = connection.execute("SELECT COALESCE(MAX(revision),0) FROM candidates WHERE task_id=? AND candidate_id=?",
                                             (package["task_id"], package["candidate_id"])).fetchone()[0]
                require(current == expected_revision, "VERSION_CONFLICT")
                connection.execute("INSERT INTO candidates VALUES (?,?,?,?,?)",
                                   (package["task_id"], package["candidate_id"], package["revision"], digest, raw))
        return {"candidate_id": package["candidate_id"], "revision": int(package["revision"]), "sha256": digest}

    def candidate(self, task_id: str, reference: dict) -> dict:
        """Trusted owner read; do not expose this unscoped store to an Agent."""
        _identifier(task_id)
        _reference(reference, "candidate_id")
        with _connection(self.root) as connection:
            row = connection.execute("SELECT * FROM candidates WHERE task_id=? AND candidate_id=? AND revision=?",
                                     (task_id, reference["candidate_id"], reference["revision"])).fetchone()
            require(row is not None, "ACCESS_DENIED")
            raw = bytes(row["payload"])
            require(sha256(raw) == row["sha256"] == reference["sha256"], "INTEGRITY_ERROR")
            return decode_json(raw)

    def candidate_context(self, task_id: str, reference: dict) -> dict:
        """Build trusted preflight context from verified archived bytes."""
        package = self.candidate(task_id, reference)
        context = self.reader(task_id, [package["manifest"]]).context(package["manifest"]["manifest_id"])
        validate_contract("research_package", package, context)
        return {**context, "candidate": dict(reference), "research_package": package}


class EvidenceReader:
    """Fixed task and exact manifest grants, using read-only SQLite connections.

    Constructed only by trusted Host code. A local Python user who can open the
    database is outside this application-level role boundary.
    """

    def __init__(self, root: Path, task_id: str, manifests: list[dict]):
        _identifier(task_id)
        require(isinstance(manifests, list) and 1 <= len(manifests) <= 20, "ACCESS_DENIED")
        self._root, self._task_id = Path(root).absolute(), task_id
        self._grants = {}
        with _connection(self._root) as connection:
            for reference in manifests:
                _reference(reference, "manifest_id")
                _, actual = _manifest_row(connection, task_id, reference["manifest_id"])
                require(actual == reference, "ACCESS_DENIED")
                self._grants[reference["manifest_id"]] = dict(reference)

    def _manifest(self, connection, manifest_id):
        _identifier(manifest_id)
        require(manifest_id in self._grants, "ACCESS_DENIED")
        manifest, reference = _manifest_row(connection, self._task_id, manifest_id)
        require(reference == self._grants[manifest_id], "INTEGRITY_ERROR")
        return manifest

    def manifest(self, manifest_id: str) -> dict:
        with _connection(self._root) as connection:
            return self._manifest(connection, manifest_id)

    def read(self, manifest_id: str, artifact_id: str, *, locator: str = "",
             offset: int | None = None, limit: int | None = None) -> dict:
        _identifier(artifact_id)
        with _connection(self._root) as connection:
            manifest = self._manifest(connection, manifest_id)
            descriptor = next((entry for entry in manifest["artifacts"] if entry["artifact_id"] == artifact_id), None)
            require(descriptor is not None, "ACCESS_DENIED")
            require(locator in descriptor["locators"], "INVALID_CITATION")
            raw = _artifact_bytes(connection, descriptor)
        content = _content(raw, descriptor["media_type"])
        require(descriptor["media_type"] != "application/octet-stream", "INVALID_CITATION")
        value = resolve_locator(content, locator, descriptor["media_type"])
        if offset is not None or limit is not None:
            require(type(offset) is int and offset >= 0 and type(limit) is int and 1 <= limit <= 1000 and isinstance(value, list), "EVIDENCE_LIMIT")
            value = value[offset:offset + limit]
        result = {"artifact_id": artifact_id, "sha256": descriptor["sha256"],
                  "byte_length": descriptor["byte_length"], "media_type": descriptor["media_type"],
                  "locator": locator, "offset": offset, "limit": limit, "value": value}
        encode_json(result, maximum=MAX_READ_BYTES)
        return result

    def context(self, manifest_id: str) -> dict:
        """Trusted validator context; never forward this whole object to a model."""
        with _connection(self._root) as connection:
            manifest = self._manifest(connection, manifest_id)
            task = connection.execute("SELECT payload FROM tasks WHERE task_id=?", (self._task_id,)).fetchone()
            require(task is not None, "ACCESS_DENIED")
            evidence = {}
            for descriptor in manifest["artifacts"]:
                raw = _artifact_bytes(connection, descriptor)
                if descriptor["media_type"] == "application/octet-stream":
                    continue  # Archived originals have no implicit executable/JSON view.
                evidence[descriptor["artifact_id"]] = {
                    "sha256": descriptor["sha256"], "media_type": descriptor["media_type"],
                    "content": _content(raw, descriptor["media_type"]),
                }
            return {"task": decode_json(bytes(task["payload"])), "manifest": manifest,
                    "manifest_ref": dict(self._grants[manifest_id]), "evidence": evidence}
