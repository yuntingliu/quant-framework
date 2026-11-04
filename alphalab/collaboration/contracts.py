"""Versioned collaboration validation; callers supply trusted Host context.

Validation is preflight, not role authorization or an output transaction. Content
digests come from exact archived bytes, never a second language's JSON encoder.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from functools import lru_cache
from importlib.resources import files
from pathlib import Path
from typing import Any

PROTOCOL_URI = "urn:alphalab:collaboration:protocol:v1"
OUTPUT_URI = "urn:alphalab:workspace-outputs:v1"
KINDS = {
    "task_envelope", "delegation_request", "evidence_manifest", "research_package",
    "review_result", "correction_response", "final_output_request",
}
MAX_CONTRACT_BYTES = 600_000
MESSAGES = {
    "INVALID_CONTRACT": "The collaboration message does not match its versioned contract.",
    "MISSING_CONTEXT": "Trusted task and evidence context is required.",
    "TASK_MISMATCH": "The message does not belong to the registered task or owner.",
    "STALE_CANDIDATE": "The message references a different candidate revision.",
    "EVIDENCE_CONFLICT": "The evidence reference does not match the frozen evidence.",
    "INVALID_CITATION": "An evidence locator is not authorized or cannot be resolved.",
    "INCOMPLETE_REVIEW": "The review status is inconsistent with its coverage or findings.",
    "INVALID_CORRECTION": "The correction must account for every finding and preserve disagreements.",
    "FINALIZATION_BLOCKED": "The request does not match the approved candidate and task state.",
    "INVALID_WORKSPACE_OUTPUTS": "The outputs do not match the existing workspace contract.",
    "ACCESS_DENIED": "The evidence is not authorized for this reader.",
    "INTEGRITY_ERROR": "Archived evidence failed its integrity check.",
    "VERSION_CONFLICT": "The expected evidence or candidate version is no longer current.",
    "DUPLICATE_CONFLICT": "The delivery identity was already used for different content.",
    "EVIDENCE_LIMIT": "The evidence request exceeds its declared size or page limits.",
    "STORAGE_UNAVAILABLE": "The evidence store is temporarily unavailable.",
    "INVALID_STATE": "The task or attempt is no longer active.",
    "DUPLICATE_CHILD_TASK": "This task or attempt has already been started.",
    "CHILD_LIMIT": "The serial review child or round limit has been reached.",
    "BUDGET_EXHAUSTED": "The shared root-task budget cannot admit another request.",
    "BUDGET_UNVERIFIABLE": "The configured hard budget requires a verified request bound.",
    "CURRENCY_MISMATCH": "The usage currency differs from the pinned budget currency.",
    "BOUND_EXCEEDED": "The provider exceeded its declared request bound.",
}


class CollaborationError(ValueError):
    """A bounded public error: never echo rejected content or filesystem paths."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(MESSAGES[code])

    def as_dict(self) -> dict:
        return {"status": "failed", "error_code": self.code, "error_summary": str(self)}


def require(condition: Any, code: str = "INVALID_CONTRACT") -> None:
    if not condition:
        raise CollaborationError(code)


def encode_json(value: Any, *, maximum: int = MAX_CONTRACT_BYTES) -> bytes:
    def bounded(item, depth=0):
        require(depth <= 64)
        if isinstance(item, dict):
            require(all(isinstance(key, str) for key in item))
            for child in item.values():
                bounded(child, depth + 1)
        elif isinstance(item, list):
            for child in item:
                bounded(child, depth + 1)

    try:
        bounded(value)
        encoded = json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True,
                             separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError, UnicodeError, RecursionError):
        raise CollaborationError("INVALID_CONTRACT") from None
    require(len(encoded) <= maximum, "EVIDENCE_LIMIT")
    return encoded


def decode_json(raw: bytes) -> Any:
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result)
            result[key] = value
        return result

    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=pairs,
                           parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        encode_json(value, maximum=8 * 1024 * 1024)
        return value
    except (ValueError, UnicodeError, RecursionError):
        raise CollaborationError("INVALID_CONTRACT") from None


def sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


@lru_cache(maxsize=1)
def contract_schema() -> dict:
    return json.loads(files("alphalab.collaboration").joinpath("schemas/protocol.v1.json").read_text())


@lru_cache(maxsize=1)
def _registry():
    from referencing import Registry, Resource
    from referencing.jsonschema import DRAFT7

    output = Path(__file__).resolve().parents[2] / "integrations/conexus/alphalab-research-agent/workspace-output.schema.json"
    # This application-owned schema remains unchanged. Both validators load it
    # locally; no schema reference can initiate a network fetch.
    return Registry().with_resources([
        (PROTOCOL_URI, Resource.from_contents(contract_schema())),
        (OUTPUT_URI, Resource.from_contents(json.loads(output.read_text()), default_specification=DRAFT7)),
    ])


def validate_contract_shape(kind: str, value: dict) -> None:
    from jsonschema import Draft7Validator, FormatChecker

    require(kind in KINDS)
    encode_json(value)
    validator = Draft7Validator({"$ref": f"{PROTOCOL_URI}#/definitions/{kind}"},
                               registry=_registry(), format_checker=FormatChecker())
    require(not next(validator.iter_errors(value), None))


def resolve_locator(content: Any, locator: str, media_type: str) -> Any:
    """Resolve only JSON pointers or explicit inclusive text line ranges."""
    try:
        if media_type == "application/json":
            require(locator == "" or locator.startswith("/"), "INVALID_CITATION")
            value = content
            if locator:
                for token in locator[1:].split("/"):
                    require(not re.search(r"~(?![01])", token), "INVALID_CITATION")
                    key = token.replace("~1", "/").replace("~0", "~")
                    if isinstance(value, list):
                        require(bool(re.fullmatch(r"0|[1-9][0-9]*", key)), "INVALID_CITATION")
                        value = value[int(key)]
                    else:
                        require(isinstance(value, dict) and key in value, "INVALID_CITATION")
                        value = value[key]
            return value
        if locator == "":
            return content
        match = re.fullmatch(r"lines:([1-9][0-9]*):([1-9][0-9]*)", locator)
        require(match and isinstance(content, str), "INVALID_CITATION")
        start, end = map(int, match.groups())
        lines = content.splitlines()
        require(start <= end <= len(lines), "INVALID_CITATION")
        return "\n".join(lines[start - 1:end])
    except (KeyError, IndexError, TypeError, ValueError, OverflowError) as error:
        if isinstance(error, CollaborationError):
            raise
        raise CollaborationError("INVALID_CITATION") from None


def _citations(value):
    if isinstance(value, dict):
        if set(value) == {"artifact_id", "sha256", "locator"}:
            yield value
        else:
            for child in value.values():
                yield from _citations(child)
    elif isinstance(value, list):
        for child in value:
            yield from _citations(child)


def _unique(rows, key):
    ids = [row[key] for row in rows]
    require(len(ids) == len(set(ids)))


def _stamp(value, expected, code):
    require(_same(value, expected), code)


def _same(left, right):
    if isinstance(left, bool) or isinstance(right, bool):
        return type(left) is type(right) and left == right
    if isinstance(left, dict) and isinstance(right, dict):
        return left.keys() == right.keys() and all(_same(left[key], right[key]) for key in left)
    if isinstance(left, list) and isinstance(right, list):
        return len(left) == len(right) and all(_same(a, b) for a, b in zip(left, right))
    return left == right


def _time(text):
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def _workspace(outputs, request_id):
    result, batch = outputs["workspaceResult"], outputs["workspaceCommands"]
    require(result["requestId"] == batch["requestId"] == request_id, "INVALID_WORKSPACE_OUTPUTS")
    commands = batch["commands"]
    for index, command in enumerate(commands):
        kind = command["type"]
        if kind == "open_result":
            require(result["kind"] == "document" and command["resultId"] == result["reportId"], "INVALID_WORKSPACE_OUTPUTS")
        if kind in {"open_widget", "close_widget"}:
            require(command["widgetId"] in {f"{mode}.workbench" for mode in
                    ("project", "data", "factor", "strategy", "validation", "report")}, "INVALID_WORKSPACE_OUTPUTS")
        if kind in {"switch_mode", "open_widget", "open_result"}:
            require(not any(item["type"] == "set_focus" for item in commands[index + 1:]), "INVALID_WORKSPACE_OUTPUTS")
        if kind == "set_focus":
            require(any(key in command for key in ("projectId", "strategyId", "backtestId", "symbol", "date")), "INVALID_WORKSPACE_OUTPUTS")


def validate_contract(kind: str, value: dict, context: dict | None = None) -> None:
    """Validate shape, local invariants and references to trusted registered state."""
    validate_contract_shape(kind, value)
    if kind == "task_envelope":
        require(value["researcher_node_id"] != value["reviewer_node_id"])
        require(set(value["not_applicable_categories"]) <= set(value["required_categories"]))
        return
    if kind == "evidence_manifest":
        _unique(value["artifacts"], "artifact_id")
        require(sum(artifact["byte_length"] for artifact in value["artifacts"]) <= 8 * 1024 * 1024, "EVIDENCE_LIMIT")
        if any(status != "complete" for status in value["coverage"].values()):
            require(value["omissions"])
        kinds = {item["kind"] for item in value["artifacts"]}
        for field, allowed in {"data": {"data", "returns"}, "sources": {"source"}, "results": {"result", "returns"}, "pit": {"timing"}}.items():
            if value["coverage"][field] == "complete":
                require(bool(kinds & allowed))
        if value["coverage"]["pit"] == "complete":
            require(value["coverage"]["data"] == "complete")
        return
    require(context and all(key in context for key in ("task", "manifest", "manifest_ref", "evidence")), "MISSING_CONTEXT")
    task, manifest = context["task"], context["manifest"]
    require(value["task_id"] == task["task_id"] == manifest["task_id"], "TASK_MISMATCH")
    if "manifest" in value:
        _stamp(value["manifest"], context["manifest_ref"], "EVIDENCE_CONFLICT")
    entries = {item["artifact_id"]: item for item in manifest["artifacts"]}
    for citation in _citations(value):
        entry = entries.get(citation["artifact_id"])
        require(entry and entry["sha256"] == citation["sha256"], "EVIDENCE_CONFLICT")
        require(citation["locator"] in entry["locators"], "INVALID_CITATION")
        content = context["evidence"].get(citation["artifact_id"])
        require(content and content["sha256"] == entry["sha256"] and content["media_type"] == entry["media_type"], "EVIDENCE_CONFLICT")
        resolve_locator(content["content"], citation["locator"], entry["media_type"])
    if kind == "research_package":
        require(value["run_id"] == task["root_run_id"] and value["question"] == task["question"], "TASK_MISMATCH")
        _unique(value["claims"], "claim_id")
        _unique(value["metrics"], "metric_id")
        for metric in value["metrics"]:
            require(metric["period_start"] <= metric["period_end"])
            require(not (metric["return_basis"] == "net" and metric["fees"] == "excluded"))
        for field, allowed in {"source_refs": {"source"}, "data_refs": {"data", "returns", "timing"},
                               "result_refs": {"result", "returns", "validation", "positions", "executions"}}.items():
            require(all(entries[item["artifact_id"]]["kind"] in allowed for item in value[field]), "EVIDENCE_CONFLICT")
        if manifest["coverage"]["pit"] == "complete":
            require(value["timing"]["decision_at"] is not None and value["timing"]["availability_evidence"])
        require(all(entries[item["artifact_id"]]["kind"] == "timing" for item in value["timing"]["availability_evidence"]), "EVIDENCE_CONFLICT")
        require(entries[value["draft_report"]["artifact_id"]]["kind"] == "report", "EVIDENCE_CONFLICT")
        require(entries[value["publication"]["artifact_id"]]["kind"] == "publication", "EVIDENCE_CONFLICT")
        require(value["publication"]["locator"] == value["draft_report"]["locator"] == "", "INVALID_CITATION")
        publication = context["evidence"][value["publication"]["artifact_id"]]["content"]
        require(isinstance(publication, dict) and publication.get("report") == value["draft_report"] and "outputs" in publication, "EVIDENCE_CONFLICT")
        if any(status != "complete" for status in manifest["coverage"].values()):
            require(value["limitations"])
        return
    if kind == "delegation_request":
        require(all(value[key] == task[key] for key in ("root_run_id", "policy_id", "grant_id", "budget_id")), "TASK_MISMATCH")
        require(value["parent_run_id"] == task["root_run_id"] and value["node_id"] == task["reviewer_node_id"], "TASK_MISMATCH")
        require(value["round"] <= task["max_review_rounds"] and _time(value["deadline"]) <= _time(task["deadline"]), "TASK_MISMATCH")
        _stamp(value["candidate"], context.get("candidate"), "STALE_CANDIDATE")
        return
    if kind == "review_result":
        _stamp(value["candidate"], context.get("candidate"), "STALE_CANDIDATE")
        delegated = context.get("delegation")
        require(delegated and all(value[key] == delegated[key] for key in ("child_task_id", "attempt_id", "round")), "TASK_MISMATCH")
        _unique(value["findings"], "finding_id")
        _unique(value["coverage"], "category")
        require({item["category"] for item in value["coverage"]} == set(task["required_categories"]), "INCOMPLETE_REVIEW")
        for coverage in value["coverage"]:
            if coverage["status"] == "checked":
                require(coverage["evidence"], "INCOMPLETE_REVIEW")
                if coverage["category"] == "pit":
                    require(manifest["coverage"]["pit"] == "complete" and
                            any(entries[item["artifact_id"]]["kind"] == "timing" for item in coverage["evidence"]), "INCOMPLETE_REVIEW")
            else:
                require(coverage["reason"].strip(), "INCOMPLETE_REVIEW")
                if coverage["status"] == "not_applicable":
                    require(coverage["category"] in task["not_applicable_categories"], "INCOMPLETE_REVIEW")
        package = context.get("research_package")
        require(package, "MISSING_CONTEXT")
        for finding in value["findings"]:
            require(finding["claim_id"] is not None or finding["metric_id"] is not None)
            require(finding["claim_id"] is None or finding["claim_id"] in {item["claim_id"] for item in package["claims"]})
            require(finding["metric_id"] is None or finding["metric_id"] in {item["metric_id"] for item in package["metrics"]})
        missing = any(item["status"] == "missing" for item in value["coverage"]) or value["missing_evidence"]
        if value["status"] == "pass":
            require(not value["findings"] and not missing and value["reason"] is None, "INCOMPLETE_REVIEW")
        elif value["status"] == "findings":
            require(value["findings"] and not missing, "INCOMPLETE_REVIEW")
        else:
            require(value["reason"] and value["reason"].strip(), "INCOMPLETE_REVIEW")
        return
    if kind == "correction_response":
        review = context.get("review")
        require(review, "MISSING_CONTEXT")
        _stamp(value["reviewed_candidate"], review["candidate"], "STALE_CANDIDATE")
        _stamp(value["new_candidate"], context.get("next_candidate"), "STALE_CANDIDATE")
        require(value["review_submission_id"] == review["submission_id"], "INVALID_CORRECTION")
        ids = [item["finding_id"] for item in value["dispositions"]]
        require(len(ids) == len(set(ids)) and set(ids) == {item["finding_id"] for item in review["findings"]}, "INVALID_CORRECTION")
        disagreements = {item["finding_id"] for item in value["dispositions"] if item["status"] != "corrected"}
        require(disagreements == set(value["remaining_disagreements"]), "INVALID_CORRECTION")
        for disposition in value["dispositions"]:
            require(all(artifact in entries for artifact in disposition["changed_artifact_ids"]), "INVALID_CORRECTION")
            if disposition["status"] == "corrected":
                previous = context.get("previous_manifest")
                require(previous, "MISSING_CONTEXT")
                require(previous["task_id"] == value["task_id"] and
                        previous["manifest_id"] == review["manifest"]["manifest_id"] and
                        previous["revision"] == review["manifest"]["revision"], "INVALID_CORRECTION")
                old_entries = {item["artifact_id"]: item for item in previous["artifacts"]}
                require(all(artifact not in old_entries or entries[artifact]["sha256"] != old_entries[artifact]["sha256"]
                            for artifact in disposition["changed_artifact_ids"]), "INVALID_CORRECTION")
                require(disposition["changed_artifact_ids"] and value["new_candidate"]["candidate_id"] == value["reviewed_candidate"]["candidate_id"] and
                        value["new_candidate"]["revision"] > value["reviewed_candidate"]["revision"] and
                        value["new_candidate"]["sha256"] != value["reviewed_candidate"]["sha256"], "INVALID_CORRECTION")
        return
    # Final preflight never writes or reserves a publication destination.
    _stamp(value["candidate"], context.get("candidate"), "STALE_CANDIDATE")
    review, package = context.get("review"), context.get("research_package")
    require(review and package, "MISSING_CONTEXT")
    validate_contract("review_result", review, context)
    require(review["status"] == "pass" and review["candidate"] == value["candidate"] and
            review["manifest"] == value["manifest"] and review["submission_id"] == value["review_submission_id"], "FINALIZATION_BLOCKED")
    state = context.get("task_state", {})
    require(state.get("status") == "ready" and _same(state.get("version"), value["expected_task_version"]), "FINALIZATION_BLOCKED")
    require(value["destination"] == context.get("destination"), "VERSION_CONFLICT")
    destination = value["destination"]
    require((destination["mode"] == "create") == (destination["expected_report_revision"] is None), "VERSION_CONFLICT")
    _stamp(value["draft_report"], package["draft_report"], "STALE_CANDIDATE")
    publication = context["evidence"][package["publication"]["artifact_id"]]
    require(value["payload_sha256"] == publication["sha256"] and _same(value["outputs"], publication["content"]["outputs"]), "STALE_CANDIDATE")
    require(value["outputs"]["workspaceResult"]["kind"] == "document" and
            value["outputs"]["workspaceResult"]["reportId"] == destination["report_id"], "INVALID_WORKSPACE_OUTPUTS")
    _workspace(value["outputs"], value["request_id"])
    correction = context.get("correction")
    require(value["correction_response_id"] == (correction["response_id"] if correction else None), "FINALIZATION_BLOCKED")
    if correction:
        require(correction["task_id"] == value["task_id"] and not correction["remaining_disagreements"] and
                correction["new_candidate"] == value["candidate"], "FINALIZATION_BLOCKED")
