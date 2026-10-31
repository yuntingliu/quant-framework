"""Contract tests for the financial agent benchmark task set."""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import pytest

from alphalab.dataio.quality import validate_dataset

ROOT = Path(__file__).resolve().parents[2]
BENCHMARK = ROOT / "benchmark"
CATEGORIES = {
    "data_diagnostics",
    "factor_implementation",
    "strategy_backtest",
    "risk_execution",
    "result_audit",
    "failure_recovery",
}


def _tasks() -> list[tuple[Path, dict]]:
    paths = sorted(BENCHMARK.glob("tasks/*/*/task.json"))
    return [(path, json.loads(path.read_text(encoding="utf-8"))) for path in paths]


def _fixtures() -> list[dict]:
    return [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(BENCHMARK.glob("data/*/fixture.json"))
    ]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _restore(fixture: str, destination: Path) -> Path:
    """Copy a fixture into a writable root, the way a runner must."""

    shutil.copytree(BENCHMARK / "data" / fixture, destination)
    (destination / "fixture.json").unlink()
    return destination


def test_every_task_matches_the_published_schema():
    # jsonschema ships in the app extra, while this group otherwise needs only
    # core dependencies. Skip rather than break the local gate in CONTRIBUTING.md,
    # which installs [dev,dashboard,rq]; CI installs [dev,app] and does run this.
    jsonschema = pytest.importorskip("jsonschema")
    schema = json.loads((BENCHMARK / "schema" / "task.schema.json").read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator.check_schema(schema)
    validator = jsonschema.Draft202012Validator(schema)
    tasks = _tasks()
    assert tasks, "No benchmark tasks were discovered"
    for path, task in tasks:
        errors = [
            f"{list(error.path)}: {error.message}" for error in validator.iter_errors(task)
        ]
        assert not errors, f"{path.relative_to(ROOT)} violates the task schema: {errors}"


def test_task_directories_identifiers_and_splits_agree():
    seen: set[str] = set()
    for path, task in _tasks():
        relative = path.relative_to(ROOT)
        assert path.parent.name == task["task_id"], f"{relative} directory does not match task_id"
        assert path.parent.parent.name == task["split"], f"{relative} is under the wrong split"
        assert task["task_id"] not in seen, f"Duplicate task_id: {task['task_id']}"
        seen.add(task["task_id"])


def test_agent_visible_inputs_exist_and_reference_answers_live_outside_the_task_tree():
    for path, task in _tasks():
        directory = path.parent
        prompt = directory / task["request"]["prompt_file"]
        assert prompt.is_file(), f"Missing prompt for {task['task_id']}"
        assert prompt.read_text(encoding="utf-8").strip(), f"Empty prompt for {task['task_id']}"
        workspace = task["environment"]["workspace"]
        if workspace is not None:
            assert (directory / workspace).is_dir(), f"Missing workspace for {task['task_id']}"

        reference = ROOT / task["reference"]["directory"]
        assert (
            reference / task["reference"]["expected_file"]
        ).is_file(), f"Missing expected answer for {task['task_id']}"
        if task["reference"]["compute"] is not None:
            assert (
                reference / task["reference"]["compute"]
            ).is_file(), f"Missing reference computation for {task['task_id']}"
        # The whole point of the split: a runner mounts the task directory, so an
        # expected answer stored inside it would be handed to the agent.
        assert BENCHMARK / "reference" in reference.parents or reference.parent == (
            BENCHMARK / "reference"
        ), f"{task['task_id']} keeps its reference outside benchmark/reference"
        assert directory not in reference.parents and reference != directory


def test_no_task_directory_contains_an_expected_answer():
    leaked = [
        path.relative_to(ROOT).as_posix()
        for path in BENCHMARK.glob("tasks/**/*")
        if path.is_file() and path.name in {"expected.json", "compute.py"}
    ]
    assert not leaked, f"Reference material is reachable from the agent workspace: {leaked}"


def test_judged_conditions_are_never_blocking_and_always_carry_a_rubric():
    for _, task in _tasks():
        for condition in task["pass_conditions"]:
            if condition["type"] == "judged":
                assert condition["weight"] == "scored", (
                    f"{task['task_id']}/{condition['id']} lets a model opinion block a pass"
                )
                assert condition.get("rubric"), f"{task['task_id']}/{condition['id']} has no rubric"
        blocking = [item for item in task["pass_conditions"] if item["weight"] == "blocking"]
        assert blocking, f"{task['task_id']} has no blocking condition and cannot fail objectively"


def test_denied_commands_are_never_also_required():
    for _, task in _tasks():
        denied = set(task["allowed_operations"].get("commands_deny", []))
        allowed = task["allowed_operations"].get("commands_allow")
        required = {
            condition["command"]
            for condition in task["pass_conditions"]
            if condition["type"] == "command_present"
        }
        assert not (required & denied), f"{task['task_id']} requires a denied command"
        if allowed is not None:
            assert not (denied & set(allowed)), f"{task['task_id']} both allows and denies a command"
            assert required <= set(allowed), f"{task['task_id']} requires a command it never allows"


def test_fixture_files_match_their_recorded_digests():
    fixtures = _fixtures()
    assert fixtures, "No benchmark fixtures were discovered"
    for fixture in fixtures:
        root = BENCHMARK / "data" / fixture["fixture"]
        for relative, recorded in fixture["files"].items():
            path = root / relative
            assert path.is_file(), f"{fixture['fixture']} is missing {relative}"
            assert path.stat().st_size == recorded["bytes"], f"{relative} changed size"
            assert _sha256(path) == recorded["sha256"], f"{relative} changed content"
        tracked = {
            path.relative_to(root).as_posix() for path in root.rglob("*.parquet")
        }
        assert tracked == set(fixture["files"]), (
            f"{fixture['fixture']} has partitions outside its manifest: "
            f"{sorted(tracked ^ set(fixture['files']))}"
        )


def test_every_task_restores_a_fixture_that_exists():
    available = {fixture["fixture"] for fixture in _fixtures()} | {"bundled"}
    for _, task in _tasks():
        assert task["environment"]["fixture"] in available, (
            f"{task['task_id']} restores an unknown fixture: {task['environment']['fixture']}"
        )


def test_the_clean_fixture_reports_no_quality_issue(tmp_path):
    root = _restore("clean-small", tmp_path / "runtime")
    for dataset, kwargs in (
        ("rq.bars", {"fail_on_gap": True, "as_of_date": "2025-05-07"}),
        ("rq.daily_factors", {"required_fields": ["market_cap", "turnover"], "fail_on_gap": True}),
        ("canonical.fundamentals", {}),
    ):
        report = validate_dataset(dataset, root, **kwargs)
        assert report["status"] == "passed", f"{dataset}: {report['issues']}"


@pytest.mark.parametrize(
    "fixture,dataset,kwargs,codes",
    [
        (
            "bar-gap-small",
            "rq.bars",
            {"fail_on_gap": True, "as_of_date": "2025-05-07"},
            ["bar_key_gap"],
        ),
        (
            "missing-field-small",
            "rq.daily_factors",
            {"required_fields": ["market_cap", "turnover"], "fail_on_gap": True},
            ["factor_key_gap"],
        ),
        ("duplicate-keys-small", "rq.bars", {}, ["duplicate_keys"]),
        (
            "stale-small",
            "rq.bars",
            {"fail_on_gap": True, "as_of_date": "2025-05-07"},
            ["stale_end_date"],
        ),
    ],
)
def test_planted_defects_still_reproduce_their_recorded_issue_codes(
    tmp_path, fixture, dataset, kwargs, codes
):
    root = _restore(fixture, tmp_path / "runtime")
    report = validate_dataset(dataset, root, **kwargs)
    assert report["status"] == "failed"
    assert sorted({issue["code"] for issue in report["issues"]}) == sorted(codes)

    recorded = json.loads(
        (BENCHMARK / "data" / fixture / "fixture.json").read_text(encoding="utf-8")
    )
    planted = next(item for item in recorded["planted_defects"] if item["dataset"] == dataset)
    assert sorted(planted["expected_issue_codes"]) == sorted(codes)
    if "missing_keys" in planted:
        assert report["metrics"]["missing_keys"] == planted["missing_keys"]
    if "expected_field_key_coverage" in planted:
        assert (
            report["metrics"]["field_key_coverage"] == planted["expected_field_key_coverage"]
        )


def test_the_point_in_time_leak_is_invisible_to_every_automated_check(tmp_path):
    # The defect this fixture carries is reachable only by comparing availability
    # against disclosure. If a future check starts catching it, the task that
    # depends on the validator staying silent needs rewriting.
    root = _restore("pit-leak-small", tmp_path / "runtime")
    for dataset in ("rq.bars", "rq.daily_factors", "canonical.fundamentals"):
        report = validate_dataset(dataset, root, as_of_date="2025-05-07")
        assert report["status"] == "passed", f"{dataset} now reports {report['issues']}"


def test_the_point_in_time_reference_recomputes_the_planted_leak():
    recorded = json.loads(
        (BENCHMARK / "data" / "pit-leak-small" / "fixture.json").read_text(encoding="utf-8")
    )
    planted = next(
        item for item in recorded["planted_defects"] if item["kind"] == "point_in_time_leak"
    )
    expected = json.loads(
        (
            BENCHMARK / "reference" / "dev" / "data-pit-leak-crosscheck" / "expected.json"
        ).read_text(encoding="utf-8")
    )
    assert expected["ground_truth"]["leaked_rows"] == planted["rows"]
    assert expected["ground_truth"]["leaked_keys"] == [
        f"{row['symbol']}:{row['quarter']}" for row in planted["rows"]
    ]

    condition = next(
        item
        for item in json.loads(
            (
                BENCHMARK / "tasks" / "dev" / "data-pit-leak-crosscheck" / "task.json"
            ).read_text(encoding="utf-8")
        )["pass_conditions"]
        if item["id"] == "leaked-rows-exact"
    )
    assert sorted(condition["expect"]) == sorted(expected["ground_truth"]["leaked_keys"])


def test_the_first_round_covers_every_category_and_every_outcome():
    tasks = [task for _, task in _tasks()]
    assert 12 <= len(tasks) <= 20, f"The first round defines {len(tasks)} tasks, not 12 to 20"
    assert {task["category"] for task in tasks} == CATEGORIES
    assert {task["expected_outcome"] for task in tasks} == {
        "completed",
        "refused",
        "clarification_required",
    }
    assert {task["split"] for task in tasks} == {"dev", "holdout"}
    # Negative cases named in the roadmap issue must all be represented.
    planted = {task["negative_case"]["kind"] for task in tasks if task.get("negative_case")}
    assert {
        "point_in_time_leak",
        "wrong_return_convention",
        "missing_field",
        "duplicate_execution",
    } <= planted


def test_the_published_manifest_matches_the_task_set():
    manifest = json.loads((BENCHMARK / "manifest.json").read_text(encoding="utf-8"))
    tasks = [task for _, task in _tasks()]
    assert manifest["counts"]["tasks"] == len(tasks)
    assert {entry["task_id"] for entry in manifest["tasks"]} == {
        task["task_id"] for task in tasks
    }
    assert set(manifest["fixtures"]) == {fixture["fixture"] for fixture in _fixtures()}
    assert "reference" not in json.dumps(manifest["tasks"]), (
        "The manifest must not carry reference paths into agent-facing material"
    )
