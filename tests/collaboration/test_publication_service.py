from __future__ import annotations

import json

import pytest

from alphalab.collaboration import CollaborationError
from apps.api.services.collaboration_publication_service import (
    destination_from_workspace,
    discover_local_host_origin,
)


def _connection(path, *, origin="http://127.0.0.1:8787", slug="alphalab-research-agent"):
    path.write_text(json.dumps({
        "origin": origin,
        "slug": slug,
        "token": "local-workspace-token-0123456789",
        "pid": 12345,
    }), encoding="utf-8")
    return path


def test_connection_file_returns_only_loopback_origin(tmp_path):
    path = _connection(tmp_path / "connection.json")
    assert discover_local_host_origin(path, expected_slug="alphalab-research-agent") == "http://127.0.0.1:8787"


def test_connection_file_rejects_non_loopback_origin(tmp_path):
    path = _connection(tmp_path / "connection.json", origin="https://example.com:8787")
    with pytest.raises(CollaborationError) as error:
        discover_local_host_origin(path, expected_slug="alphalab-research-agent")
    assert error.value.code == "ACCESS_DENIED"


def test_connection_file_rejects_wrong_slug(tmp_path):
    path = _connection(tmp_path / "connection.json", slug="another-agent")
    with pytest.raises(CollaborationError) as error:
        discover_local_host_origin(path, expected_slug="alphalab-research-agent")
    assert error.value.code == "TASK_MISMATCH"


def test_destination_uses_snapshot_workspace_and_report_versions():
    workspace = {
        "revision": 7,
        "nodes": [{
            "id": "report-1", "type": "document",
            "description": "AlphaLab quantitative report",
            "values": {"content": "# Old report", "revision": 3},
        }],
    }
    assert destination_from_workspace(workspace, "new-report") == {
        "report_id": "new-report", "mode": "create",
        "expected_workspace_revision": 7, "expected_report_revision": None,
    }
    assert destination_from_workspace(workspace, "report-1") == {
        "report_id": "report-1", "mode": "update",
        "expected_workspace_revision": 7, "expected_report_revision": 3,
    }


def test_destination_rejects_unrelated_existing_node():
    workspace = {"revision": 2, "nodes": [{
        "id": "report-1", "type": "note", "description": "Personal note",
        "values": {"content": "Do not overwrite"},
    }]}
    with pytest.raises(CollaborationError) as error:
        destination_from_workspace(workspace, "report-1")
    assert error.value.code == "VERSION_CONFLICT"
