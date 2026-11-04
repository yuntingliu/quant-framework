"""Create/reopen the documented, disposable Member 2 protocol walkthrough."""
from __future__ import annotations

import argparse
import copy
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from alphalab.collaboration import (  # noqa: E402
    ControlStore,
    EvidenceArtifact,
    EvidenceReader,
    EvidenceStore,
    encode_json,
    sha256,
    validate_contract,
)


def prepare(output):
    """Host code owns identities, artifacts and candidate; no model credential."""
    task = {"schema_version": 1, "task_id": "handover-task", "root_run_id": "handover-root",
            "researcher_node_id": "handover-researcher", "reviewer_node_id": "handover-reviewer",
            "question": "Check the supplied synthetic report against its frozen result.",
            "policy_id": "handover-policy", "grant_id": "handover-grant", "budget_id": "handover-budget",
            "deadline": (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat(),
            "max_review_rounds": 1, "required_categories": ["report_consistency"], "not_applicable_categories": []}
    store = EvidenceStore(output / "evidence")
    store.register_task(task)
    source = {"identity": "synthetic-handover", "revision": "1", "code_sha256": None, "environment_sha256": None}
    report = EvidenceArtifact("report", b"Synthetic net return: 8% after fees.", "text/markdown", "report", source)
    result = EvidenceArtifact.json("result", {"net_return": 0.08, "fees_included": True}, kind="result", source=source)
    def cite(artifact):
        return {"artifact_id": artifact.artifact_id, "sha256": sha256(artifact.content), "locator": ""}

    # Reuse the canonical four-field example; these bytes are archived, never applied.
    examples = json.loads((ROOT / "tests/fixtures/collaboration/contracts/examples.json").read_text(encoding="utf-8"))
    outputs = copy.deepcopy(examples["messages"]["final_output_request"]["outputs"])
    outputs["summary"] = outputs["decisionNotebook"]["baseCase"] = report.content.decode()
    publication = EvidenceArtifact.json("publication", {"report": cite(report), "outputs": outputs}, kind="publication", source=source)
    manifest = store.freeze(task["task_id"], expected_revision=0, delivery_key="handover-freeze",
                            artifacts=[report, result, publication],
                            coverage={"data": "unavailable", "sources": "unavailable", "results": "complete", "pit": "unavailable"},
                            omissions=["Synthetic protocol check only: no source, market inputs or timing evidence."])
    candidate = store.save_candidate({"schema_version": 1, "task_id": task["task_id"], "run_id": task["root_run_id"],
        "candidate_id": "handover-candidate", "revision": 1, "question": task["question"], "manifest": manifest,
        "draft_report": cite(report), "publication": cite(publication),
        "claims": [{"claim_id": "claim-1", "text": report.content.decode(), "evidence": [cite(result)]}],
        "metrics": [], "assumptions": ["Fixed synthetic integration data."], "source_refs": [], "data_refs": [],
        "result_refs": [cite(result)], "timing": {"decision_at": None, "availability_evidence": []},
        "limitations": ["No financial-quality, data/PIT or publication acceptance is claimed."]}, expected_revision=0)
    reader = EvidenceReader(output / "evidence", task["task_id"], [manifest])
    assert reader.read(manifest["manifest_id"], "report")["value"] == report.content.decode()
    delegation = {"schema_version": 1, "task_id": task["task_id"], "root_run_id": task["root_run_id"],
                  "parent_run_id": task["root_run_id"], "child_task_id": "handover-child", "attempt_id": "handover-attempt",
                  "node_id": task["reviewer_node_id"], "role": "reviewer", "round": 1,
                  "candidate": candidate, "manifest": manifest, "result_type": "review_result",
                  "policy_id": task["policy_id"], "grant_id": task["grant_id"], "budget_id": task["budget_id"], "deadline": task["deadline"]}
    payload = {"task": task, "candidate": candidate, "manifest": manifest, "delegation": delegation}
    (output / "handover-input.json").write_bytes(encode_json(payload))
    return payload


def verify(output):
    payload = json.loads((output / "handover-input.json").read_text(encoding="utf-8"))
    store = ControlStore(output / "control", output / "evidence")
    state = store.request(payload["task"]["task_id"], "view")
    assert state["status"] == "completed" and len(state["children"]) == 1
    child = state["children"][0]
    validate_contract("review_result", child["review"], {**store.context(payload["task"]["task_id"], payload["candidate"]),
                                                       "delegation": child["delegation"]})
    assert child["review"]["status"] == "pass" and child["status"] == "completed"
    return {"reopened_store_valid": True, "typed_review_valid_in_python": True}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=["prepare", "verify"])
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    if not output.is_relative_to((ROOT / "build").resolve()):
        parser.error("Output must stay inside ignored build/")
    output.mkdir(parents=True, exist_ok=True)
    print(json.dumps(prepare(output) if args.operation == "prepare" else verify(output)))
