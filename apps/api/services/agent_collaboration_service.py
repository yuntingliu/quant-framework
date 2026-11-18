"""Prepare an immutable, reviewable AlphaLab backtest candidate (Member 2 collaboration workflow).

Trusted service only: caller must authorize backtest access and draft ownership.
No agent execution, market-data refresh, workspace edit, or final publication.
"""

from __future__ import annotations

from typing import Any

from alphalab.collaboration import EvidenceStore, validate_contract
from alphalab.collaboration.contracts import require, sha256
from apps.api.services.collaboration_evidence_service import collect_backtest_evidence


def _ref(artifact: Any, locator: str = "") -> dict[str, str]:
    """Reference exact archived bytes; never reuse example hashes."""
    require(locator in artifact.descriptor()["locators"], "INVALID_CITATION")
    return {"artifact_id": artifact.artifact_id, "sha256": sha256(artifact.content), "locator": locator}


def prepare_collaboration_candidate(
    *,
    task: dict[str, Any],
    backtest_id: str,
    request_id: str,
    report_markdown: str,
    outputs: dict[str, Any],
    claim_text: str,
    result_store: Any,
    evidence_store: EvidenceStore | None = None,
    candidate_id: str = "candidate-1",
    delivery_key: str = "initial-research-draft",
    expected_revision: int = 0,
    assumptions: list[str] | None = None,
    additional_limitations: list[str] | None = None,
) -> dict[str, Any]:
    """Freeze a pre-authored draft and register its first review candidate.

    The caller supplies a genuine research claim (not an invented performance
    interpretation). The claim is linked to the persisted backtest result,
    but a reviewer must independently verify it. Missing PIT data stays missing.

    This method is for the initial candidate; later revisions belong to the correction and re-review workflow.
    A failure after freeze may leave a *draft* manifest, never a published report.
    """
    require(type(expected_revision) is int and expected_revision == 0, "VERSION_CONFLICT")
    validate_contract("task_envelope", task)
    if evidence_store is None:
        from apps.api.services.collaboration_storage_service import create_task_evidence_store

        evidence_store = create_task_evidence_store(task["task_id"])
    require(isinstance(backtest_id, str) and bool(backtest_id.strip()))
    require(isinstance(request_id, str) and bool(request_id.strip()))
    require(isinstance(report_markdown, str) and bool(report_markdown.strip()))
    require(isinstance(claim_text, str) and bool(claim_text.strip()) and len(claim_text) <= 20000)
    require(isinstance(candidate_id, str) and bool(candidate_id.strip()))
    require(isinstance(delivery_key, str) and bool(delivery_key.strip()))
    assumptions = [] if assumptions is None else assumptions
    additional_limitations = [] if additional_limitations is None else additional_limitations
    require(isinstance(assumptions, list) and all(isinstance(x, str) and x.strip() for x in assumptions))
    require(isinstance(additional_limitations, list) and all(isinstance(x, str) and x.strip() for x in additional_limitations))

    # The evidence facade checks source hashes, exports persisted run bytes,
    # and validates proposed workspace outputs without committing them.
    bundle = collect_backtest_evidence(
        result_store, backtest_id, request_id=request_id,
        report_markdown=report_markdown, outputs=outputs,
    )
    artifacts = bundle["artifacts"]
    by_id = {artifact.artifact_id: artifact for artifact in artifacts}
    require(len(by_id) == len(artifacts) and
            all(name in by_id for name in ("report", "publication", "backtest-result", "return-series")))
    limitations = list(dict.fromkeys([*bundle["omissions"], *additional_limitations]))
    require(len(limitations) <= 50)

    # Register only after collecting/validating the draft to minimize orphan tasks.
    evidence_store.register_task(task)
    manifest = evidence_store.freeze(
        task["task_id"], expected_revision=expected_revision,
        delivery_key=delivery_key, artifacts=artifacts,
        coverage=bundle["coverage"], omissions=bundle["omissions"],
        code_identity=bundle["code_identity"],
        environment_identity=bundle["environment_identity"],
    )
    source_refs = []
    data_refs = []
    result_refs = []

    for artifact in artifacts:
        if artifact.kind == "source":
            source_refs.append(_ref(artifact))

        elif artifact.kind in {"data", "returns", "timing"}:
            data_refs.append(_ref(artifact))

        elif artifact.kind in {
            "result",
            "validation",
            "positions",
            "executions",
        }:
            result_refs.append(_ref(artifact))
    package = {
        "schema_version": 1,
        "task_id": task["task_id"],
        "run_id": task["root_run_id"],
        "candidate_id": candidate_id,
        "revision": manifest["revision"],
        "question": task["question"],
        "manifest": manifest,
        "draft_report": _ref(by_id["report"]),
        "publication": _ref(by_id["publication"]),
        "claims": [{"claim_id": "backtest-claim", "text": claim_text,
                    "evidence": [_ref(by_id["backtest-result"], "/metrics")]}],
        # Do not infer gross/net, fees, compounding or benchmark definitions.
        "metrics": [],
        "assumptions": assumptions,
        "source_refs": source_refs,
        "data_refs": data_refs,
        "result_refs": result_refs,
        "timing": {"decision_at": None, "availability_evidence": []},
        "limitations": limitations,
    }
    # The store validates citations against the *frozen* manifest and ensures
    # package revision matches the expected candidate revision.
    candidate = evidence_store.save_candidate(package, expected_revision=expected_revision)
    context = evidence_store.candidate_context(task["task_id"], candidate)
    validate_contract("research_package", package, context)
    return {"task_id": task["task_id"], "candidate": candidate,
            "manifest": manifest, "research_package": package,
            "coverage": bundle["coverage"], "omissions": limitations,
            "publication_performed": False}
