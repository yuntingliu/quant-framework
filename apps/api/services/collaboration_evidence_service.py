"""Collect existing frozen run/source evidence without executing or publishing it.

This is a trusted service facade, not an unauthenticated HTTP route. The Host
must authorize the selected result before calling it.
"""

from __future__ import annotations

from alphalab import ResultStore
from alphalab.collaboration.contracts import (
    CollaborationError,
    decode_json,
    require,
    sha256,
)
from alphalab.collaboration.evidence import EvidenceArtifact
from apps.api.services.workspace_output_service import prepare_workspace_outputs


def collect_backtest_evidence(store: ResultStore, backtest_id: str, *, request_id: str,
                              report_markdown: str, outputs: dict) -> dict:
    """Return exact artifacts and honest coverage for a later EvidenceStore.freeze.

    Current provider partitions are deliberately never read. A historical run
    without original input bytes cannot acquire a fabricated data/PIT pass.
    """
    require(prepare_workspace_outputs(request_id, outputs)["status"] == "succeeded", "INVALID_WORKSPACE_OUTPUTS")
    require(isinstance(report_markdown, str) and report_markdown.strip())
    try:
        exported = store.export_backtest_evidence(backtest_id)
    except ValueError:
        raise CollaborationError("EVIDENCE_LIMIT") from None
    require(exported is not None, "ACCESS_DENIED")
    record = exported["record"]
    source = {"identity": f"backtest:{backtest_id}", "revision": None,
              "code_sha256": None, "environment_sha256": None}
    artifacts = []
    omissions = ["Original market-input bytes and decision-time availability were not archived with this run; current data was not substituted."]
    source_count = 0
    for label, prefix in (("strategy-source", "strategy"), ("validation-source", "validation")):
        text = record[f"{prefix}_source"]
        if text is None:
            omissions.append(f"The run has no persisted {prefix} source.")
            continue
        raw = text.encode("utf-8")
        recorded_digest = record[f"{prefix}_source_sha256"]
        require(recorded_digest is None or recorded_digest == sha256(raw), "INTEGRITY_ERROR")
        if recorded_digest is None:
            omissions.append(f"The original {prefix} source digest was not recorded.")
        revision = record[f"{prefix}_revision"]
        identity = {**source, "revision": str(revision) if revision is not None else None,
                    "code_sha256": recorded_digest}
        artifacts.append(EvidenceArtifact(label, raw, "text/plain", "source", identity))
        source_count += 1
    result = {
        "run_id": record["id"], "start_date": record["start_date"], "end_date": record["end_date"],
        "metrics": {key: record[key] for key in ("total_return", "annual_return", "annual_vol", "sharpe", "max_drawdown", "n_periods")},
        "strategy_project_id": record["strategy_project_id"], "project_strategy_id": record["project_strategy_id"],
    }
    artifacts.extend([
        EvidenceArtifact.json("backtest-result", result, kind="result", source=source,
                              locators=("", "/metrics", "/metrics/total_return")),
        EvidenceArtifact.json("return-series", {"rows": exported["returns"]}, kind="returns", source=source, locators=("", "/rows")),
        EvidenceArtifact.json("position-weights", {"rows": exported["weights"]}, kind="positions", source=source, locators=("", "/rows")),
    ])
    for field, kind in (("execution_json", "executions"), ("event_json", "executions"),
                        ("provenance_json", "provenance"), ("validation_output_json", "validation"),
                        ("run_diagnostics_json", "result"), ("settings_json", "result"),
                        ("strategy_manifest_json", "source")):
        if record[field] is not None:
            artifacts.append(EvidenceArtifact.json(field.removesuffix("_json").replace("_", "-"),
                decode_json(record[field].encode("utf-8")), kind=kind, source=source))
    report = EvidenceArtifact("report", report_markdown.encode("utf-8"), "text/markdown", "report", source)
    report_ref = {"artifact_id": "report", "sha256": sha256(report.content), "locator": ""}
    artifacts.extend([report, EvidenceArtifact.json("publication", {"report": report_ref, "outputs": outputs},
                                                  kind="publication", source=source)])
    if not exported["returns"]:
        omissions.append("No persisted return rows are available.")
    return {
        "artifacts": artifacts,
        "coverage": {"data": "unavailable", "sources": "complete" if source_count == 2 else "partial" if source_count else "unavailable",
                     "results": "complete" if exported["returns"] else "partial", "pit": "unavailable"},
        "omissions": omissions, "code_identity": record["code_version"], "environment_identity": None,
    }
