"""Application service for project-owned validation.py files."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

from alphalab.strategy.history import SourceHistory
from alphalab.utils.paths import RUNTIME_APP_DIR
from alphalab.validation.builtins import DEFAULT_VALIDATION_SOURCE
from alphalab.validation.repository import ValidationRepository
from alphalab.validation.source import inspect_validation_source


def repository(db_path: str | Path | None = None) -> ValidationRepository:
    return ValidationRepository(db_path)


def source_history() -> SourceHistory:
    return SourceHistory(RUNTIME_APP_DIR / "alphalab.db", "validation")


def restore_preview(project_id: str, revision: int) -> dict[str, Any]:
    return source_history().validation_restore_preview(project_id, revision)


def restore_source(project_id: str, revision: int, token: str, request_id: str) -> dict[str, Any]:
    repo = repository()
    try:
        return repo.restore_source(project_id, revision, token, request_id)
    finally:
        repo.close()


def get_workspace(project_id: str) -> dict[str, Any]:
    repo = repository()
    try:
        return repo.get_or_create(project_id)
    finally:
        repo.close()


def update_source(
    project_id: str,
    source: str,
    *,
    expected_source_sha256: str | None = None,
) -> dict[str, Any]:
    repo = repository()
    try:
        return repo.update_source(
            project_id,
            source,
            expected_source_sha256=expected_source_sha256,
        )
    finally:
        repo.close()


def update_parameters(
    project_id: str,
    edits: Iterable[dict[str, Any]],
    *,
    expected_source_sha256: str | None = None,
) -> dict[str, Any]:
    repo = repository()
    try:
        return repo.update_parameters(
            project_id,
            edits,
            expected_source_sha256=expected_source_sha256,
        )
    finally:
        repo.close()


def migrate_to_default(
    project_id: str,
    *,
    expected_source_sha256: str | None = None,
) -> dict[str, Any]:
    """Create a new user-project revision from the current official template."""

    repo = repository()
    try:
        return repo.update_source(
            project_id,
            DEFAULT_VALIDATION_SOURCE,
            expected_source_sha256=expected_source_sha256,
        )
    finally:
        repo.close()


def validate_source(source: str) -> dict[str, Any]:
    return inspect_validation_source(source).to_dict()


__all__ = [
    "get_workspace",
    "migrate_to_default",
    "repository",
    "restore_preview",
    "restore_source",
    "source_history",
    "update_parameters",
    "update_source",
    "validate_source",
]
