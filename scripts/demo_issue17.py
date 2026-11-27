"""Deterministic Issue #17 migration and 20 -> 60 -> restore demonstration.

Uses disposable stores only. It never needs RQ credentials or starts a backtest,
recipe sync, validation run, or real Conexus publication.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from alphalab import StrategyRepository
from alphalab.dataio.recipes import render_builtin_recipe
from alphalab.dataio.runtime import OperationsStore
from alphalab.strategy.history import SourceHistory
from alphalab.validation.repository import ValidationRepository
from apps.api.services.report_reference_service import ReportReferenceStore


def evidence(repo, package, window, observations=3):
    return repo.record_factor_evidence(
        "research", "momentum_20d", "main",
        {
            "project_id": "research",
            "revision": package["revision"],
            "source_sha256": package["source_sha256"],
            "status": "insufficient",
            "summary": {"observations": observations, "mean_rank_ic": 0.02},
            "decay": {"1": {"observations": observations, "mean_rank_ic": 0.02}},
            "rows": [],
        },
        {
            "start_date": "2024-01-01", "end_date": "2025-01-01",
            "frequency": "monthly", "quantiles": 5, "horizons": [1, 3, 6],
            "parameters": {"window": window},
        },
        {
            "universe": "research_universe", "start_date": "2024-01-01",
            "end_date": "2025-01-01", "strategy_id": "main",
        },
        {"forward_return": "next_open_to_open", "missing": "not_zero_filled"},
        {"scope": "strategy_package"},
    )


def run(root: Path) -> dict:
    alphalab_path = root / "alphalab.db"
    repo = StrategyRepository(alphalab_path)
    repo.clone_project("sdk-v1-default", "research")
    repo.create_strategy("research", "second", name="Second")
    repo.select_strategy("main")
    original = next(
        item for item in repo.list_factor_assets("research")
        if item["factor_id"] == "momentum_20d"
    )
    first_package = repo.get_package("research")
    first = evidence(repo, first_package, 20)
    repo.transition_factor_asset(
        "research", original["id"], "observation", reason="Initial deterministic review",
        expected_status_revision=1, evidence_ids=[first["id"]], request_id="observe-20",
    )
    repo.transition_factor_asset(
        "research", original["id"], "adopted", reason="Adopt within the declared sample",
        expected_status_revision=2, evidence_ids=[first["id"]], request_id="adopt-20",
    )

    factor = repo.get_factor_source("research", "momentum_20d")["source"]
    repo.replace_factor_source("research", "momentum_20d", factor.replace("] = 20,", "] = 60,"))
    changed = repo.get_factor_asset("research", original["id"])
    assert changed["status"] == "candidate" and changed["current_revision_id"] != original["current_revision_id"]
    second = evidence(repo, repo.get_package("research"), 60, observations=4)

    history = SourceHistory(alphalab_path)
    revisions = history.factor_revisions("research", "main", "momentum_20d")
    target = revisions[-1]["id"]
    preview = history.factor_restore_preview("research", "main", "momentum_20d", target)
    receipt = repo.restore_factor("research", "momentum_20d", target, preview["token"], "restore-20")
    assert receipt["restored_from"] == target
    restored_asset = repo.get_factor_asset("research", original["id"])
    assert restored_asset["status"] == "candidate"
    assert "] = 20," in repo.get_factor_source("research", "momentum_20d")["source"]

    copied = repo.copy_factor("research", "momentum_20d", "momentum_20d_copy")
    assert copied["asset"]["id"] != original["id"]
    assert copied["asset"]["copied_from_asset_id"] == original["id"]
    assert repo.list_factor_evidence("research", copied["asset"]["id"])["items"] == []

    stale = history.factor_restore_preview("research", "main", "momentum_20d", target)
    repo.replace_factor_source("research", "momentum_20d", factor.replace("] = 20,", "] = 60,"))
    try:
        repo.restore_factor("research", "momentum_20d", target, stale["token"], "stale-restore")
    except RuntimeError as exc:
        assert "changed since" in str(exc)
    else:  # pragma: no cover - demo assertion
        raise AssertionError("stale factor restore unexpectedly succeeded")

    operations = OperationsStore(root)
    first_recipe = render_builtin_recipe("rq.etf_daily", start="2021-08-25", end="2026-08-25")
    second_recipe = render_builtin_recipe("rq.etf_daily", start="2022-01-01", end="2026-08-25")
    operations.save_recipe_draft("research", first_recipe, selected_template_id="rq.etf_daily")
    operations.save_recipe_draft("research", second_recipe, selected_template_id="rq.a_share_daily")
    recipe_history = SourceHistory(operations.path, "recipe")
    recipe_preview = recipe_history.recipe_restore_preview("research", 1)
    recipe_receipt = operations.restore_recipe_revision("research", 1, recipe_preview["token"], "recipe-restore")
    assert operations.get_recipe_draft("research")["source"] == first_recipe

    validation = ValidationRepository(alphalab_path)
    initial = validation.get_or_create("research")
    validation.update_source("research", initial["source"] + "\n# changed validation\n")
    validation_history = SourceHistory(alphalab_path, "validation")
    validation_preview = validation_history.validation_restore_preview("research", 1)
    validation_receipt = validation.restore_source("research", 1, validation_preview["token"], "validation-restore")
    assert validation.get_or_create("research")["current_revision"] == validation_receipt["revision"]
    validation.close()

    with repo._conn as connection:
        connection.execute(
            "INSERT INTO backtests(id, config_yaml, strategy_project_id, strategy_revision, strategy_source_sha256, validation_revision) VALUES (?, '', 'research', ?, ?, 1)",
            ("demo-run", first_package["revision"], first_package["source_sha256"]),
        )
    references = ReportReferenceStore(alphalab_path, operations.path)
    report = references.reserve("demo-report", "demo-request", "research", [
        {"kind": "strategy_package", "revision": first_package["revision"]},
        {"kind": "validation_package", "revision": 1},
        {"kind": "factor_evidence", "id": second["id"]},
        {"kind": "recipe_revision", "revision": 1},
        {"kind": "backtest", "id": "demo-run"},
        {"kind": "data_manifest"},
        {"kind": "experiment"},
    ])
    statuses = [item["status"] for item in report["references"]]
    assert statuses == ["verified", "verified", "verified", "verified", "verified", "unavailable", "not_linked"]
    assert all(job == [] for job in [repo._conn.execute("SELECT * FROM backtest_jobs").fetchall()])
    repo.close()

    migrated = StrategyRepository(alphalab_path)
    assets = migrated.list_factor_assets("research")
    migrated.close()
    return {
        "status": "passed",
        "factor_20_to_60_to_restored": True,
        "restore_revision": receipt["revision"],
        "copied_asset": copied["asset"]["id"],
        "old_evidence_preserved": [first["id"], second["id"]],
        "recipe_revision": recipe_receipt["revision"],
        "validation_revision": validation_receipt["revision"],
        "report_reference_statuses": statuses,
        "migrated_asset_count": len(assets),
        "implicit_jobs_started": 0,
        "data_manifest": "unavailable (#15 not implemented)",
        "conexus_revision_contract": "not_verified",
    }


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="issue17-demo-") as directory:
        print(json.dumps(run(Path(directory)), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
