"""Matched option-model versus controls-only rotations in the canonical SDK engine."""

import json
import os
import uuid

from alphalab.dataio.catalog import DataCatalog
from apps.api.services import options_evidence_service as evidence
from apps.api.services import options_research_service as research


def _save_pair(path, pair):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f".{uuid.uuid4().hex}.tmp")
    temporary.write_text(json.dumps(pair, ensure_ascii=False, allow_nan=False), encoding="utf-8")
    os.replace(temporary, path)


def _path(suite_id, horizon):
    evidence.read_suite(suite_id)
    if horizon not in (1, 5, 20):
        raise ValueError("Rotation horizon must be 1, 5 or 20 sessions")
    return DataCatalog().root / "research" / "option_rotations" / f"{suite_id}-{horizon}.json"


def rotation_pair(suite_id, horizon=5):
    from apps.api.services.backtest_job_service import get_backtest_job

    path = _path(suite_id, horizon)
    if not path.is_file():
        return {"pair": None}
    pair = json.loads(path.read_text(encoding="utf-8"))
    jobs = []
    for item in pair["jobs"]:
        raw = get_backtest_job(item["job_id"]) or {
            "status": "unavailable",
            "error_summary": "Saved backtest job unavailable",
        }
        jobs.append(
            {
                "role": item["role"],
                **{
                    key: raw.get(key)
                    for key in (
                        "status",
                        "backtest_id",
                        "error_summary",
                        "id",
                        "project_id",
                        "execution_reliable",
                        "research_valid",
                    )
                },
            }
        )
    comparison = holdout_comparison = None
    if all(j["status"] == "succeeded" for j in jobs):
        from apps.api.services.options_comparison_service import compare

        comparison = compare([j["backtest_id"] for j in jobs])
        holdout_start = evidence.read_suite(suite_id)["config"].get("holdout_start")
        if holdout_start:
            holdout_comparison = compare([j["backtest_id"] for j in jobs], start=holdout_start)
    return {
        "pair": pair,
        "jobs": jobs,
        "comparison": comparison,
        "holdout_comparison": holdout_comparison,
    }


def run_rotation_pair(suite_id, horizon=5):
    from apps.api.services import strategy_service
    from apps.api.services.backtest_job_service import submit_backtest_job

    path = _path(suite_id, horizon)
    if path.is_file():
        current = rotation_pair(suite_id, horizon)
        failed = {
            j["role"]
            for j in current["jobs"]
            if j["status"] not in {"queued", "running", "succeeded"}
        }
        if failed:
            pair = current["pair"]
            pair.setdefault("previous_attempts", []).append(pair["jobs"])
            updated = []
            for item in pair["jobs"]:
                if item["role"] in failed:
                    job = submit_backtest_job(
                        {
                            "project_id": item["project_id"],
                            "strategy_id": "main",
                            "profile": "runtime",
                            "start_date": pair["start"],
                            "end_date": pair["end"],
                            "revision": item["revision"],
                        }
                    )
                    item = {**item, "job_id": job["id"]}
                updated.append(item)
            pair["jobs"] = updated
            _save_pair(path, pair)
            return rotation_pair(suite_id, horizon)
        return current
    suite = evidence.read_suite(suite_id)
    variants = [r for r in suite["rows"] if r["horizon"] == horizon]
    selected = next((r for r in variants if r["variant"] == "joint"), variants[0])
    report = research.read_research(selected["run_id"])
    start, end = report["folds"][0]["test_start"], report["scope"]["end"]
    projects = []
    # Materialize both sources and save both projects before either job acquires
    # dataset read locks. No vendor access or independent execution engine.
    for source in ("controls", "model"):
        project_id = f"option-pair-{suite_id[:10]}-{horizon}-{source}"
        spec = {
            "project_id": project_id,
            "name": f"Controlled {horizon}d ETF rotation / {source}",
            "research_id": report["id"],
            "prediction_source": source,
            "controlled_suite_id": suite_id,
            "weights": {},
            "underlyings": report["scope"]["underlyings"],
            "start": start,
            "end": end,
            "top_n": 3,
            "frequency": "weekly",
        }
        try:
            project = strategy_service.get_project(project_id)
        except KeyError:
            project = None
        if project is None:
            project = research.create_research_project(spec)["project"]
        elif (
            project.get("settings", {}).get("option_controlled_suite_id") != suite_id
            or project.get("settings", {}).get("option_prediction_source") != source
        ):
            raise ValueError(
                "Existing rotation project does not match this frozen controlled study"
            )
        projects.append(
            {"role": source, "project_id": project_id, "revision": project["current_revision"]}
        )
    jobs = []
    for project in projects:
        job = submit_backtest_job(
            {
                "project_id": project["project_id"],
                "strategy_id": "main",
                "profile": "runtime",
                "start_date": start,
                "end_date": end,
                "revision": project["revision"],
            }
        )
        jobs.append({**project, "job_id": job["id"]})
    pair = {
        "suite_id": suite_id,
        "horizon": horizon,
        "run_id": report["id"],
        "start": start,
        "end": end,
        "jobs": jobs,
        "rule": {
            "top_n": 3,
            "frequency": "weekly_last_close",
            "execution": "next_session_open",
            "allocation": "capped_equal_weight",
            "max_weight": 1 / 3,
        },
        "interpretation": "Same cohort, model training, rebalance rule and canonical execution/cost settings; only the forecast score differs. Historical paired evidence, not prospective alpha validation.",
    }
    _save_pair(path, pair)
    return rotation_pair(suite_id, horizon)
