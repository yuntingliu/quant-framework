"""Resolve task-scoped collaboration stores under the application runtime directory.

The task ID is the only caller-selectable component. Store paths are derived
locally; HTTP callers can never supply filesystem paths.
"""

from __future__ import annotations

import re
from pathlib import Path

from alphalab.collaboration import CollaborationError, ControlStore, EvidenceStore
from alphalab.utils.paths import RUNTIME_DIR

_TASK_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")


def _runtime_root() -> Path:
    root = Path(RUNTIME_DIR).expanduser().absolute()
    if root.resolve() != root:
        raise CollaborationError("ACCESS_DENIED")
    return root


def task_store_roots(task_id: str) -> tuple[Path, Path]:
    """Return deterministic control/evidence roots for a validated task ID."""
    if not isinstance(task_id, str) or not _TASK_ID.fullmatch(task_id):
        raise CollaborationError("INVALID_CONTRACT")

    runtime = _runtime_root()
    base = runtime / "collaboration" / "tasks"
    task_root = base / task_id
    control_root = task_root / "control"
    evidence_root = task_root / "evidence"

    # Reject symlinked parents and roots before any SQLite database is opened.
    for path in (runtime, runtime / "collaboration", base, task_root, control_root, evidence_root):
        if path.exists() and path.is_symlink():
            raise CollaborationError("ACCESS_DENIED")
    if task_root.resolve() != task_root or not task_root.resolve().is_relative_to(base.resolve()):
        raise CollaborationError("ACCESS_DENIED")
    return control_root, evidence_root


def create_task_evidence_store(task_id: str) -> EvidenceStore:
    """Create/open the canonical evidence store during trusted task preparation."""
    _, evidence_root = task_store_roots(task_id)
    return EvidenceStore(evidence_root)


def create_task_control_store(task_id: str) -> ControlStore:
    """Create/open canonical stores for the trusted collaboration Host."""
    control_root, evidence_root = task_store_roots(task_id)
    return ControlStore(control_root, evidence_root)


def open_existing_task_stores(task_id: str) -> tuple[ControlStore, EvidenceStore]:
    """Open an existing task only; never create a database for an API lookup."""
    control_root, evidence_root = task_store_roots(task_id)
    control_db = control_root / "control.sqlite3"
    evidence_db = evidence_root / "evidence.sqlite3"
    if (
        not control_db.is_file()
        or control_db.is_symlink()
        or not evidence_db.is_file()
        or evidence_db.is_symlink()
    ):
        raise CollaborationError("TASK_MISMATCH")
    control = ControlStore(control_root, evidence_root)
    return control, control.evidence
