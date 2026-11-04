"""Run a local contract/evidence demonstration, stopping before publication."""

from __future__ import annotations

import argparse
import copy
import json
import shutil
import sqlite3
import sys
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from alphalab.collaboration import (  # noqa: E402
    CollaborationError,
    EvidenceArtifact,
    EvidenceStore,
    encode_json,
    validate_contract,
)


def _artifacts(context):
    result = []
    for descriptor in context["manifest"]["artifacts"]:
        content = context["evidence"][descriptor["artifact_id"]]["content"]
        raw = encode_json(content) if descriptor["media_type"] == "application/json" else content.encode("utf-8")
        result.append(EvidenceArtifact(descriptor["artifact_id"], raw, descriptor["media_type"],
                                       descriptor["kind"], descriptor["source"], tuple(descriptor["locators"])))
    return result


def _expect(code, callback):
    try:
        callback()
    except CollaborationError as error:
        if error.code == code:
            return code
        raise
    raise AssertionError(f"Expected {code}")


def demonstrate(output: Path) -> dict:
    examples = json.loads((ROOT / "tests/fixtures/collaboration/contracts/examples.json").read_text(encoding="utf-8"))
    store = EvidenceStore(output / "store")
    task = examples["messages"]["task_envelope"]
    store.register_task(task)
    old = examples["previous"]
    old_context = {"manifest": old["manifest"], "evidence": old["evidence"]}
    old_options = dict(expected_revision=0, delivery_key="old-evidence", artifacts=_artifacts(old_context),
                       coverage=old["manifest"]["coverage"], omissions=[])
    first = store.freeze(task["task_id"], **old_options)
    old_package = {**old["research_package"], "manifest": first}
    first_candidate = store.save_candidate(old_package, expected_revision=0)
    options = dict(expected_revision=1, delivery_key="new-evidence", artifacts=_artifacts(examples["context"]),
                   coverage=examples["context"]["manifest"]["coverage"], omissions=[])
    second = store.freeze(task["task_id"], **options)
    package = {**examples["messages"]["research_package"], "manifest": second}
    second_candidate = store.save_candidate(package, expected_revision=1)
    context = store.candidate_context(task["task_id"], second_candidate)
    context.update(task_state=examples["context"]["task_state"], destination=examples["context"]["destination"],
                   previous_manifest=store.reader(task["task_id"], [first]).manifest(first["manifest_id"]), next_candidate=second_candidate)
    delegation = {**examples["messages"]["delegation_request"], "manifest": second, "candidate": second_candidate}
    context["delegation"] = delegation
    validate_contract("delegation_request", delegation, context)
    review = {**examples["messages"]["review_result"], "manifest": second, "candidate": second_candidate}
    validate_contract("review_result", review, context)
    old_review = {**old["review"], "manifest": first, "candidate": first_candidate}
    correction = {**examples["messages"]["correction_response"], "reviewed_candidate": first_candidate, "new_candidate": second_candidate}
    validate_contract("correction_response", correction, {**context, "review": old_review})
    context.update(review=review, correction=correction)
    final = {**examples["messages"]["final_output_request"], "manifest": second, "candidate": second_candidate}
    validate_contract("final_output_request", final, context)
    old_reader, new_reader = store.reader(task["task_id"], [first]), store.reader(task["task_id"], [second])
    old_text = old_reader.read(first["manifest_id"], "report")["value"]
    new_text = new_reader.read(second["manifest_id"], "report")["value"]
    assert old_text != new_text and "0%" in old_text and "-1%" in new_text
    assert store.freeze(task["task_id"], **options) == second
    conflict = _expect("VERSION_CONFLICT", lambda: store.freeze(task["task_id"], **{**options, "expected_revision": 0, "delivery_key": "conflict"}))
    denied = _expect("ACCESS_DENIED", lambda: new_reader.read(first["manifest_id"], "report"))
    stale_review = {**review, "candidate": first_candidate}
    stale = _expect("STALE_CANDIDATE", lambda: validate_contract("review_result", stale_review, context))
    changed = copy.deepcopy(final)
    changed["outputs"]["summary"] = "An unreviewed conclusion."
    changed_output = _expect("STALE_CANDIDATE", lambda: validate_contract("final_output_request", changed, context))
    corrupt_root = output / "corrupt-store"
    corrupt_root.mkdir()
    shutil.copy2(store.root / "evidence.sqlite3", corrupt_root / "evidence.sqlite3")
    corrupt = EvidenceStore(corrupt_root)
    reader = corrupt.reader(task["task_id"], [first])
    digest = old["evidence"]["report"]["sha256"]
    with sqlite3.connect(corrupt_root / "evidence.sqlite3") as connection:
        connection.execute("UPDATE blobs SET payload=? WHERE sha256=?", (b"corrupt", digest))
    integrity = _expect("INTEGRITY_ERROR", lambda: reader.read(first["manifest_id"], "report"))
    assert old_reader.read(first["manifest_id"], "report")["value"] == old_text
    for name, value in {"research-package": package, "review": review, "correction": correction, "final-preflight": final}.items():
        (output / f"{name}.json").write_bytes(encode_json(value) + b"\n")
    result = {"status": "passed", "old_manifest": first, "new_manifest": second,
              "old_candidate": first_candidate, "new_candidate": second_candidate,
              "history_preserved": True, "duplicate_delivery": "same_reference",
              "version_conflict": conflict, "unauthorized_manifest": denied, "stale_review": stale,
              "changed_output": changed_output, "corrupt_blob": integrity,
              "publication_performed": False, "model_api_calls": 0,
              "host_role_enforcement": "later_phases", "output": str(output)}
    (output / "verification.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "build/member1/evidence-demo")
    args = parser.parse_args()
    output = args.output.resolve()
    if not output.is_relative_to((ROOT / "build").resolve()):
        parser.error("--output must be inside this checkout's ignored build directory.")
    output = output / f"trial-{uuid4().hex[:12]}"
    output.mkdir(parents=True)
    print(json.dumps(demonstrate(output), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
