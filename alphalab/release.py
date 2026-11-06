"""Release manifests, compatibility checks and deployment safety helpers.

The helpers are platform neutral and only read the directories they are
given. Deployment scripts use them to record what a release contains, refuse
an incompatible activation, protect active research work, and confirm which
version a running instance actually serves.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import sqlite3
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from alphalab.utils.paths import REPO_ROOT

MANIFEST_SCHEMA = "alphalab.release-manifest.v1"
MANIFEST_NAME = "release-manifest.json"
CORE_LOCK = "integrations/conexus/runtime.lock.json"
CORE_UPSTREAM = "build/conexus/UPSTREAM.json"
PINNED_INPUTS = (
    "pyproject.toml",
    CORE_LOCK,
    "vendor/conexus/EXPORT.json",
    "apps/desktop/package.json",
    "apps/desktop/package-lock.json",
)
ACTIVE_JOB_STATES = ("queued", "running", "pending")
# Mirrors apps.api.services.agent_conversation_service.TERMINAL_RUN_STATES;
# the core package must not import the API application.
TERMINAL_AGENT_RUN_STATES = frozenset({"completed", "blocked", "failed", "cancelled"})
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
_PYPROJECT_VERSION = re.compile(r'^version\s*=\s*"([^"]+)"', re.MULTILINE)
_PYPROJECT_PYTHON = re.compile(r'^requires-python\s*=\s*"([^"]+)"', re.MULTILINE)
_PACKAGE_VERSION = re.compile(r'^__version__\s*=\s*"([^"]+)"', re.MULTILINE)
_MINIMUM = re.compile(r">=\s*(\d+)(?:\.(\d+))?")


class ReleaseError(RuntimeError):
    """Raised when a release manifest is missing, malformed or inconsistent."""


class CompatibilityError(ReleaseError):
    """Raised when a candidate release cannot safely replace the current one."""


class ActiveTaskError(ReleaseError):
    """Raised when research work is still running in the runtime directory."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _match(pattern: re.Pattern[str], path: Path) -> str | None:
    try:
        found = pattern.search(path.read_text(encoding="utf-8"))
    except OSError:
        return None
    return found.group(1) if found else None


def core_lock(root: Path | str = REPO_ROOT) -> dict[str, Any] | None:
    """Return the pinned Conexus core lock of a checkout or release."""
    value = _read_json(Path(root) / CORE_LOCK)
    return value if isinstance(value, dict) else None


def _core_summary(lock: dict[str, Any] | None) -> dict[str, Any] | None:
    if lock is None:
        return None
    return {
        "revision": lock.get("revision"),
        "layout_version": lock.get("layoutVersion"),
        "packages": list(lock.get("packages") or []),
    }


def app_versions(root: Path | str) -> dict[str, str | None]:
    """Every place a release declares the application version."""
    root = Path(root)
    return {
        "pyproject": _match(_PYPROJECT_VERSION, root / "pyproject.toml"),
        "package": _match(_PACKAGE_VERSION, root / "alphalab/__init__.py"),
    }


def _node_requirement(root: Path) -> str | None:
    package = _read_json(root / "apps/desktop/package.json")
    engines = package.get("engines") if isinstance(package, dict) else None
    return engines.get("node") if isinstance(engines, dict) else None


def current_versions(root: Path | str = REPO_ROOT) -> dict[str, Any]:
    """Versions of the local checkout or release, as a deployment would report them."""
    from alphalab.utils.build_info import build_info

    root = Path(root)
    info = build_info()
    manifest = _read_json(root / MANIFEST_NAME)
    upstream = _read_json(root / CORE_UPSTREAM)
    return {
        "app_version": info["version"],
        "commit_sha": info["commit_sha"],
        "core": _core_summary(core_lock(root)),
        "core_runtime_revision": upstream.get("revision") if isinstance(upstream, dict) else None,
        "manifest": _manifest_summary(manifest) if isinstance(manifest, dict) else None,
    }


def _manifest_summary(manifest: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema": manifest.get("schema"),
        "commit_sha": manifest.get("commit_sha"),
        "ref": manifest.get("ref"),
        "app_version": (manifest.get("app") or {}).get("version"),
        "created_at": manifest.get("created_at"),
    }


def build_manifest(release_dir: Path | str, *, commit_sha: str, ref: str | None = None) -> dict[str, Any]:
    """Describe a built release: versions, pinned core and hashes of pinned inputs."""
    root = Path(release_dir)
    if not root.is_dir():
        raise ReleaseError(f"release directory does not exist: {root}")
    lock = core_lock(root)
    if lock is None:
        raise ReleaseError(f"pinned Conexus core lock is missing: {CORE_LOCK}")
    versions = app_versions(root)
    if versions["pyproject"] is None:
        raise ReleaseError("pyproject.toml declares no version")
    inputs = {
        name: _sha256(root / name) for name in PINNED_INPUTS if (root / name).is_file()
    }
    return {
        "schema": MANIFEST_SCHEMA,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "commit_sha": commit_sha,
        "ref": ref,
        "app": {"name": "alphalab", "version": versions["pyproject"]},
        "core": {
            **_core_summary(lock),
            "runtime_built": (root / CORE_UPSTREAM).is_file(),
        },
        "requirements": {
            "python": _match(_PYPROJECT_PYTHON, root / "pyproject.toml"),
            "node": _node_requirement(root),
        },
        "inputs": inputs,
    }


def write_manifest(release_dir: Path | str, manifest: dict[str, Any]) -> Path:
    path = Path(release_dir) / MANIFEST_NAME
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temp.replace(path)
    return path


def read_manifest(release_dir: Path | str) -> dict[str, Any]:
    path = Path(release_dir) / MANIFEST_NAME
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ReleaseError(f"release manifest unreadable: {error}") from error
    if not isinstance(manifest, dict) or manifest.get("schema") != MANIFEST_SCHEMA:
        raise ReleaseError("release manifest has an unsupported schema")
    for key in ("commit_sha", "app", "core", "inputs"):
        if not manifest.get(key):
            raise ReleaseError(f"release manifest is missing {key}")
    return manifest


def verify_manifest(release_dir: Path | str, manifest: dict[str, Any]) -> list[str]:
    """Return problems where the release no longer matches its manifest."""
    root = Path(release_dir)
    problems: list[str] = []
    for name, digest in sorted((manifest.get("inputs") or {}).items()):
        path = root / name
        if not path.is_file():
            problems.append(f"missing pinned input: {name}")
        elif _sha256(path) != digest:
            problems.append(f"pinned input changed: {name}")
    lock = core_lock(root)
    recorded_core = manifest.get("core") or {}
    if lock is None:
        problems.append(f"missing core lock: {CORE_LOCK}")
    elif lock.get("revision") != recorded_core.get("revision"):
        problems.append("core lock revision differs from the manifest")
    upstream_path = root / CORE_UPSTREAM
    if upstream_path.is_file():
        if _read_json(upstream_path) != lock:
            problems.append("built Conexus runtime does not match the pinned core lock")
    elif recorded_core.get("runtime_built"):
        problems.append(f"built Conexus runtime is missing: {CORE_UPSTREAM}")
    versions = app_versions(root)
    expected = (manifest.get("app") or {}).get("version")
    for source, found in versions.items():
        if found != expected:
            problems.append(f"{source} version {found} differs from manifest version {expected}")
    return problems


def _minimum(requirement: str | None) -> tuple[int, int] | None:
    found = _MINIMUM.search(requirement or "")
    return (int(found.group(1)), int(found.group(2) or 0)) if found else None


def check_compatibility(
    current: dict[str, Any] | None,
    candidate: dict[str, Any],
    *,
    allow_layout_change: bool = False,
    python_version: tuple[int, int] | None = None,
) -> list[str]:
    """Raise ``CompatibilityError`` for unsafe replacements; return informational notes."""
    if candidate.get("schema") != MANIFEST_SCHEMA:
        raise CompatibilityError("candidate release manifest has an unsupported schema")
    running = python_version or sys.version_info[:2]
    minimum = _minimum((candidate.get("requirements") or {}).get("python"))
    if minimum and running < minimum:
        raise CompatibilityError(
            f"candidate requires Python {'.'.join(map(str, minimum))}+, "
            f"found {'.'.join(map(str, running))}"
        )
    if current is None:
        return ["no current release manifest; first managed activation"]
    if current.get("schema") != MANIFEST_SCHEMA:
        raise CompatibilityError("current release manifest has an unsupported schema")
    notes: list[str] = []
    old_core = current.get("core") or {}
    new_core = candidate.get("core") or {}
    if old_core.get("layout_version") != new_core.get("layout_version"):
        message = (
            f"Conexus runtime layout changes from {old_core.get('layout_version')} "
            f"to {new_core.get('layout_version')}"
        )
        if not allow_layout_change:
            raise CompatibilityError(message + "; pass an explicit layout-change approval")
        notes.append(message + " (approved)")
    if old_core.get("revision") != new_core.get("revision"):
        notes.append(f"Conexus core revision changes to {new_core.get('revision')}")
    old_version = (current.get("app") or {}).get("version")
    new_version = (candidate.get("app") or {}).get("version")
    if old_version != new_version:
        notes.append(f"application version changes from {old_version} to {new_version}")
    return notes


def deployment_matches_manifest(health: dict[str, Any], manifest: dict[str, Any]) -> list[str]:
    """Compare a running instance's health payload with the release it should serve."""
    problems: list[str] = []
    if health.get("status") != "ok":
        problems.append(f"health status is {health.get('status')!r}")
    expected = {
        "commit_sha": manifest.get("commit_sha"),
        "version": (manifest.get("app") or {}).get("version"),
        "core_revision": (manifest.get("core") or {}).get("revision"),
    }
    for key, value in expected.items():
        if health.get(key) != value:
            problems.append(f"{key} is {health.get(key)!r}, expected {value!r}")
    return problems


def _read_only(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)


def _table_columns(connection: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in connection.execute(f'PRAGMA table_info("{table}")')}


def _count_active_jobs(database: Path, table: str) -> int:
    connection = _read_only(database)
    try:
        if "status" not in _table_columns(connection, table):
            return 0
        marks = ",".join("?" for _ in ACTIVE_JOB_STATES)
        row = connection.execute(
            f'SELECT COUNT(*) FROM "{table}" WHERE status IN ({marks})', ACTIVE_JOB_STATES
        ).fetchone()
    finally:
        connection.close()
    return int(row[0])


def _active_agent_runs(database: Path) -> int:
    connection = _read_only(database)
    try:
        if "snapshot_json" not in _table_columns(connection, "agent_conversation_runs"):
            return 0
        rows = connection.execute(
            "SELECT snapshot_json FROM agent_conversation_runs WHERE delivered = 0"
        ).fetchall()
    finally:
        connection.close()
    active = 0
    for (snapshot,) in rows:
        try:
            status = json.loads(snapshot).get("status")
        except (ValueError, AttributeError):
            status = None
        if status not in TERMINAL_AGENT_RUN_STATES:
            active += 1
    return active


def find_active_tasks(runtime_dir: Path | str) -> list[dict[str, Any]]:
    """List work in ``runtime_dir`` that a release switch would interrupt."""
    runtime = Path(runtime_dir)
    app = runtime / "app"
    found: list[dict[str, Any]] = []
    for database, table in (
        ("alphalab.db", "backtest_jobs"),
        ("alphalab.db", "research_runs"),
        ("dataio.db", "sync_jobs"),
    ):
        path = app / database
        if path.is_file():
            try:
                count = _count_active_jobs(path, table)
            except sqlite3.DatabaseError as error:
                raise ActiveTaskError(f"cannot inspect {database}:{table}: {error}") from error
            if count:
                found.append({"source": f"{database}:{table}", "count": count})
    conversations = app / "agent-conversations.sqlite3"
    if conversations.is_file():
        try:
            count = _active_agent_runs(conversations)
        except sqlite3.DatabaseError as error:
            raise ActiveTaskError(f"cannot inspect Agent conversation runs: {error}") from error
        if count:
            found.append({"source": "agent-conversations.sqlite3:agent_conversation_runs", "count": count})
    state = runtime / "conexus/project/state.json"
    if state.is_file():
        value = _read_json(state)
        if value is None:
            raise ActiveTaskError("cannot read conexus/project/state.json")
        runs = value.get("runs", []) if isinstance(value, dict) else []
        count = sum(1 for run in runs if isinstance(run, dict) and run.get("status") in {"queued", "running"})
        if count:
            found.append({"source": "conexus/project/state.json:runs", "count": count})
    return found


def host_lock_owner(runtime_dir: Path | str) -> int | None:
    """PID recorded by the local Conexus Host, for information only."""
    try:
        text = (Path(runtime_dir) / "conexus/project/host.lock").read_text(encoding="utf-8").strip()
    except OSError:
        return None
    found = re.search(r"\d+", text)
    return int(found.group(0)) if found else None


def ensure_no_active_tasks(runtime_dir: Path | str) -> None:
    active = find_active_tasks(runtime_dir)
    if active:
        details = ", ".join(f"{item['count']} in {item['source']}" for item in active)
        raise ActiveTaskError(f"active tasks must finish before changing the release: {details}")


def query_instance_version(
    url: str,
    *,
    username: str | None = None,
    password: str | None = None,
    timeout: float = 5.0,
) -> dict[str, Any]:
    """Ask a running loopback instance which version it serves."""
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"} or parts.hostname not in LOOPBACK_HOSTS:
        raise ReleaseError("instance version queries must use a loopback http(s) origin")
    if not parts.path or parts.path == "/":
        url = url.rstrip("/") + "/api/health"
    headers = {}
    if username is not None or password is not None:
        token = base64.b64encode(f"{username or ''}:{password or ''}".encode()).decode()
        headers["Authorization"] = f"Basic {token}"
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = json.load(response)
    except (OSError, ValueError) as error:
        raise ReleaseError(f"instance version query failed: {error}") from error
    if not isinstance(body, dict):
        raise ReleaseError("instance health response is not an object")
    keys = ("status", "version", "commit_sha", "deployed_at", "core_revision", "manifest")
    return {key: body.get(key) for key in keys}


__all__ = [
    "ActiveTaskError",
    "CompatibilityError",
    "MANIFEST_NAME",
    "MANIFEST_SCHEMA",
    "ReleaseError",
    "app_versions",
    "build_manifest",
    "check_compatibility",
    "core_lock",
    "current_versions",
    "deployment_matches_manifest",
    "ensure_no_active_tasks",
    "find_active_tasks",
    "host_lock_owner",
    "query_instance_version",
    "read_manifest",
    "verify_manifest",
    "write_manifest",
]
