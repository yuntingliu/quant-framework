"""Synthetic development-fixture preparation/preflight, never a financial reviewer.

The private scripted response is kept outside the Agent's evidence workspace.
All acceptance uses public evidence/control/contract facades. Fault injection is
restricted to a disposable copy owned by this runner.
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from alphalab.collaboration import (  # noqa: E402
    CollaborationError,
    ControlStore,
    EvidenceArtifact,
    EvidenceStore,
    check_development_oracle,
    encode_json,
    load_agent_input,
    sha256,
    validate_contract,
)
from apps.api.services.workspace_output_service import prepare_workspace_outputs  # noqa: E402

CORPUS = ROOT / "tests/fixtures/collaboration/dev"
CASES = {"clean": "DEV-CLEAN-01", "seeded-formula": "DEV-FORMULA-01", "seeded-pit": "DEV-PIT-01",
         "seeded-report": "DEV-REPORT-01", "seeded-claim": "DEV-CLAIM-01", "missing-evidence": "DEV-MISSING-01",
         "disputed": "DEV-DISPUTE-01", "stale-candidate": "DEV-CONFLICT-01", "correction": "DEV-FORMULA-01"}
CATEGORIES = {"clean": ["returns", "pit", "report_consistency"], "seeded-formula": ["formulas", "returns"],
              "seeded-pit": ["pit"], "seeded-report": ["report_consistency", "returns"],
              "seeded-claim": ["support"], "missing-evidence": ["pit", "returns"], "disputed": ["returns"],
              "stale-candidate": ["returns"], "correction": ["formulas", "returns"]}


def write(path, value):
    path.write_bytes(encode_json(value, maximum=8 * 1024 * 1024))


def expect(code, callback):
    try:
        callback()
    except CollaborationError as error:
        assert error.code == code, (error.code, code)
        return code
    raise AssertionError(f"Expected {code}")


def prepare(output, scenario, *, adversarial_note=None):
    output.mkdir(parents=True, exist_ok=True)
    check_development_oracle(CORPUS)
    case = scenario if scenario in CASES else "clean"
    payload = load_agent_input(CORPUS, CASES[case])
    oracle = json.loads((CORPUS / "oracle.json").read_text(encoding="utf-8"))["cases"][CASES[case]]
    examples = json.loads((ROOT / "tests/fixtures/collaboration/contracts/examples.json").read_text(encoding="utf-8"))
    task = {**examples["messages"]["task_envelope"], "question": payload["question"],
            "deadline": (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat(),
            "required_categories": CATEGORIES[case], "not_applicable_categories": []}
    store = EvidenceStore(output / "evidence")
    store.register_task(task)
    source = {"identity": "synthetic-development-input", "revision": "1", "code_sha256": None, "environment_sha256": None}
    versions = []

    def freeze(report, revision):
        artifacts = [EvidenceArtifact.json(key, value, kind=key if key in {"result", "timing", "returns"} else "data", source=source)
                     for key, value in payload["evidence"].items()]
        # Archive the supplied input container as data, independently of typed
        # timing/result views. This is synthetic input, never current market data.
        artifacts.append(EvidenceArtifact.json("input-evidence", payload["evidence"], kind="data", source=source))
        artifacts.append(EvidenceArtifact("report", report.encode(), "text/markdown", "report", source))
        if adversarial_note is not None:
            artifacts.append(EvidenceArtifact("adversarial_note", adversarial_note.encode(), "text/plain", "data", source))
        refs = {a.artifact_id: {"artifact_id": a.artifact_id, "sha256": sha256(a.content), "locator": ""} for a in artifacts}
        outputs = copy.deepcopy(examples["messages"]["final_output_request"]["outputs"])
        outputs["summary"] = outputs["decisionNotebook"]["baseCase"] = report
        outputs["workspaceResult"].update(title="Synthetic development review",
                                         columns=[{"key": "statement", "label": "Report", "format": "text"}], rows=[{"statement": report}])
        artifacts.append(EvidenceArtifact.json("publication", {"report": refs["report"], "outputs": outputs}, kind="publication", source=source))
        refs["publication"] = {"artifact_id": "publication", "sha256": sha256(artifacts[-1].content), "locator": ""}
        manifest = store.freeze(task["task_id"], expected_revision=revision - 1, delivery_key=f"fixture-{revision}", artifacts=artifacts,
                                coverage={"sources": "unavailable", "results": "complete" if "result" in refs or "returns" in refs else "unavailable",
                                          "data": "complete", "pit": "complete" if "timing" in refs else "unavailable"},
                                omissions=["No executable strategy source is supplied."] + (["Timing evidence is missing."] if case == "missing-evidence" else []))
        package = {**examples["messages"]["research_package"], "revision": revision, "question": task["question"],
                   "manifest": manifest, "draft_report": refs["report"], "publication": refs["publication"],
                   "claims": [{"claim_id": "claim-1", "text": report, "evidence": [refs[key] for key in payload["evidence"]]}],
                   "metrics": [], "assumptions": ["Synthetic development case; no financial quality inference."], "source_refs": [],
                   "data_refs": [refs["input-evidence"], *[refs[key] for key in payload["evidence"] if key not in {"result"}]],
                   "result_refs": [refs[key] for key in ("result", "returns") if key in refs],
                   "timing": {"decision_at": payload["evidence"].get("timing", {}).get("decision_at"),
                              "availability_evidence": [refs["timing"]] if "timing" in refs else []},
                   "limitations": ["Only supplied synthetic evidence is covered; absent information remains unavailable."]}
        candidate = store.save_candidate(package, expected_revision=revision - 1)
        context = store.candidate_context(task["task_id"], candidate)
        d = {**examples["messages"]["delegation_request"], "child_task_id": f"review-{revision}", "attempt_id": f"attempt-{revision}",
             "round": 1 if case == "stale-candidate" else revision, "deadline": task["deadline"], "candidate": candidate, "manifest": manifest}
        status = "pass" if revision == 2 and case == "correction" else oracle["expected_status"]
        if case == "stale-candidate":
            status = "pass"  # The rejected object is an otherwise valid approval of revision 1.
        if case == "disputed":
            status = "findings"  # Deliberate reviewer allegation; not the oracle's judgment.
        coverage = []
        for category in task["required_categories"]:
            missing = category == "pit" and "timing" not in refs
            evidence_id = "timing" if category == "pit" and not missing else next(iter(payload["evidence"]))
            coverage.append({"category": category, "status": "missing" if missing else "checked",
                             "reason": "No timing evidence was supplied." if missing else "",
                             "evidence": [] if missing else [refs[evidence_id]]})
        findings = []
        if status == "findings":
            findings = [{"finding_id": "finding-1", "category": task["required_categories"][0], "severity": "error",
                         "certainty": "confirmed", "claim_id": "claim-1", "metric_id": None,
                         "explanation": payload["candidate"]["reviewer_allegation"] if case == "disputed" else oracle["rationale"],
                         "expected": "Verify the supplied evidence and correct any unsupported statement.", "observed": report,
                         "evidence": [refs["report"], *[refs[k] for k in payload["evidence"]]],
                         "requested_correction": "Resolve this finding against the frozen evidence.",
                         "verification": "Read the cited frozen artifact and its declared definitions."}]
        review = {"schema_version": 1, "task_id": task["task_id"], "submission_id": f"submission-{revision}",
                  "child_task_id": d["child_task_id"], "attempt_id": d["attempt_id"], "round": d["round"],
                  "candidate": candidate, "manifest": manifest, "status": status, "coverage": coverage,
                  "findings": findings, "missing_evidence": ["timing"] if case == "missing-evidence" else [],
                  "reason": "PIT cannot be verified without timing evidence." if case == "missing-evidence" else None}
        validate_contract("review_result", review, {**context, "delegation": d})
        final = {**examples["messages"]["final_output_request"], "candidate": candidate, "manifest": manifest,
                 "review_submission_id": review["submission_id"], "correction_response_id": None,
                 "draft_report": refs["report"], "payload_sha256": refs["publication"]["sha256"], "outputs": outputs}
        version = {"candidate": candidate, "manifest": manifest, "delegation": d, "review": review, "final": final,
                   "evidence_ids": [*payload["evidence"], *(["adversarial_note"] if adversarial_note is not None else [])]}
        folder = output / f"revision-{revision}"
        folder.mkdir()
        for name, value in {"candidate": package, "manifest": context["manifest"], "scripted-review": review, "final-request": final}.items():
            write(folder / f"{name}.json", value)
        versions.append(version)

    freeze(payload["candidate"]["report"], 1)
    if case == "correction":
        freeze("The compounded two-period net return is -1% (1.10 × 0.90 − 1).", 2)
    elif case == "stale-candidate":
        freeze(payload["candidate"]["report"] + " This is candidate revision 2.", 2)
    result = {"scenario": scenario, "task_id": task["task_id"], "task": task, "versions": versions,
              "fixture_id": CASES[case], "input_sha256": sha256((CORPUS / f"inputs/{CASES[case]}.json").read_bytes()),
              "oracle_in_agent_workspace": False, "expected_review_status": versions[0]["review"]["status"]}
    write(output / "fixture.json", result)
    return result


def verify(output):
    fixture = json.loads((output / "fixture.json").read_text(encoding="utf-8"))
    store = ControlStore(output / "control", output / "evidence")
    state = store.request(fixture["task_id"], "view")
    scenario = fixture["scenario"]
    expected_state = "completed" if scenario in {"clean", "correction", "duplicate-delivery", "staging-interruption"} else "blocked"
    assert state["status"] == expected_state, (scenario, state["status"], expected_state)
    if scenario in {"hash-mismatch", "stale-candidate"}:
        assert state["status"] == "blocked" and all(c["review"] is None for c in state["children"])
        code = "INTEGRITY_ERROR"
        if scenario == "stale-candidate":
            current = store.context(fixture["task_id"], fixture["versions"][-1]["candidate"])
            code = expect("EVIDENCE_CONFLICT", lambda: validate_contract("final_output_request", fixture["versions"][0]["final"], current))
        return {"finalization": "blocked", "error_code": code, "publication_performed": False}
    accepted = [child for child in state["children"] if child["review"] is not None]
    assert len(accepted) == (2 if scenario == "correction" else 1)
    for child, version in zip(accepted, fixture["versions"], strict=True):
        assert child["review"] == version["review"]
        context = store.context(fixture["task_id"], child["delegation"]["candidate"])
        validate_contract("review_result", child["review"], {**context, "delegation": child["delegation"]})
    last = fixture["versions"][-1]
    context = store.context(fixture["task_id"], last["candidate"])
    context.update(review=accepted[-1]["review"], delegation=accepted[-1]["delegation"],
                   task_state={"status": "ready" if state["status"] == "completed" else "blocked", "version": 3},
                   destination=last["final"]["destination"])
    final = copy.deepcopy(last["final"])
    correction = None
    if scenario in {"correction", "disputed"}:
        first = fixture["versions"][0]
        correction = {"schema_version": 1, "task_id": fixture["task_id"], "response_id": "correction-1",
                      "review_submission_id": accepted[0]["review"]["submission_id"], "reviewed_candidate": first["candidate"],
                      "new_candidate": last["candidate"], "dispositions": [{"finding_id": "finding-1",
                          "status": "corrected" if scenario == "correction" else "disputed",
                          "reason": "Geometric compounding fixes the return." if scenario == "correction" else "The frozen definition explicitly states fees_included=true.",
                          "evidence": [context["research_package"]["draft_report"], *context["research_package"]["result_refs"]],
                          "changed_artifact_ids": ["report", "publication"] if scenario == "correction" else []}],
                      "remaining_disagreements": [] if scenario == "correction" else ["finding-1"]}
        previous = store.context(fixture["task_id"], first["candidate"])
        validate_contract("correction_response", correction, {**context, "review": accepted[0]["review"],
                          "next_candidate": last["candidate"], "previous_manifest": previous["manifest"]})
        write(output / "correction.json", correction)
        context["correction"] = correction
        final["correction_response_id"] = "correction-1"
    if state["status"] == "completed":
        validate_contract("final_output_request", final, context)
        prepared = prepare_workspace_outputs(final["request_id"], final["outputs"])
        assert prepared["status"] == "succeeded" and len(prepared["operations"]) == 3
        stale = copy.deepcopy(final)
        stale["candidate"] = {**final["candidate"], "revision": 99}
        expect("STALE_CANDIDATE", lambda: validate_contract("final_output_request", stale, context))
        status = "preflight_passed_without_publication"
    else:
        expect("FINALIZATION_BLOCKED", lambda: validate_contract("final_output_request", final, context))
        status = "blocked"
    write(output / "final-request.json", final)
    return {"finalization": status, "accepted_reviews": len(accepted), "correction_validated": correction is not None,
            "remaining_disagreements": correction["remaining_disagreements"] if correction else [], "publication_performed": False}


def fault(output, mode):
    fixture = json.loads((output / "fixture.json").read_text(encoding="utf-8"))
    path = output / "evidence/evidence.sqlite3"
    assert output.resolve().is_relative_to((ROOT / "build").resolve())
    if mode == "corrupt":
        digest = fixture["versions"][0]["final"]["draft_report"]["sha256"]
        with sqlite3.connect(path) as db:
            db.execute("UPDATE blobs SET payload=? WHERE sha256=?", (b"corrupted test bytes", digest))
        return {"fault": "disposable_evidence_corrupted"}
    if mode == "crash":
        db = sqlite3.connect(path)
        db.execute("BEGIN IMMEDIATE")
        db.execute("INSERT INTO blobs VALUES (?,?)", ("uncommitted-test-blob", b"partial"))
        os._exit(17)  # Only this dedicated test child, never another process.
    assert mode == "check-staging"
    store = EvidenceStore(output / "evidence")
    before = store.candidate_context(fixture["task_id"], fixture["versions"][0]["candidate"])
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT COUNT(*) FROM blobs WHERE sha256='uncommitted-test-blob'").fetchone()[0] == 0
        db.execute("CREATE TRIGGER reject_staged_manifest BEFORE INSERT ON manifests BEGIN SELECT RAISE(ABORT,'injected'); END")
    extra = EvidenceArtifact.json("partial", {"value": "must roll back"}, kind="data", source={"identity": "test", "revision": None, "code_sha256": None, "environment_sha256": None})
    expect("STORAGE_UNAVAILABLE", lambda: store.freeze(fixture["task_id"], expected_revision=1, delivery_key="interrupted",
           artifacts=[extra], coverage={"data": "complete", "sources": "unavailable", "results": "unavailable", "pit": "unavailable"},
           omissions=["This interrupted synthetic staging has no sources, results or timing."]))
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT COUNT(*) FROM manifests").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM blobs WHERE sha256=?", (sha256(extra.content),)).fetchone()[0] == 0
        db.execute("DROP TRIGGER reject_staged_manifest")
    assert store.candidate_context(fixture["task_id"], fixture["versions"][0]["candidate"]) == before
    return {"staging": "rolled_back", "existing_bundle_unchanged": True}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=["prepare", "verify", "corrupt", "crash", "check-staging"])
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--scenario", default="clean")
    args = parser.parse_args()
    if not args.output.resolve().is_relative_to((ROOT / "build").resolve()):
        parser.error("Disposable artifacts must be inside this checkout's ignored build directory.")
    result = prepare(args.output, args.scenario) if args.operation == "prepare" else verify(args.output) if args.operation == "verify" else fault(args.output, args.operation)
    print(json.dumps(result))
