from __future__ import annotations

import json

import pytest

from alphalab.collaboration import EvidenceStore, validate_contract
from scripts.prepare_collaboration_acceptance import CASES, prepare


@pytest.mark.parametrize("scenario", CASES)
def test_development_fixture_archives_validate_without_oracle_in_workspace(tmp_path, scenario):
    fixture = prepare(tmp_path, scenario)
    store = EvidenceStore(tmp_path / "evidence")
    for version in fixture["versions"]:
        context = store.candidate_context(fixture["task_id"], version["candidate"])
        validate_contract("review_result", version["review"], {**context, "delegation": version["delegation"]})
        assert all("oracle" not in item["artifact_id"] for item in context["manifest"]["artifacts"])
        assert all(key not in context for key in ("expected_status", "oracle", "facts"))
        assert context["research_package"]["source_refs"] == []
    if scenario == "missing-evidence":
        assert fixture["versions"][0]["review"]["status"] == "incomplete"
    if scenario == "correction":
        assert [v["review"]["status"] for v in fixture["versions"]] == ["findings", "pass"]
    # Exercise the UTF-8 file transport too, including the corrected × / − text.
    restored = json.loads((tmp_path / "fixture.json").read_text(encoding="utf-8"))
    for version in restored["versions"]:
        if version["review"]["status"] == "pass":
            context = store.candidate_context(fixture["task_id"], version["candidate"])
            validate_contract("final_output_request", version["final"], {**context, "review": version["review"],
                              "delegation": version["delegation"], "task_state": {"status": "ready", "version": 3},
                              "destination": version["final"]["destination"]})
