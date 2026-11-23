"""Freeze Issue #7 task inputs and the Issue #9 evaluation protocol locally."""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

EVALUATOR_VERSION = "1.0.0"
PUBLIC_TASK_SET = "alphalab-financial-agent-benchmark"
SYNTHETIC_TASK_SET = "issue9-offline-fixtures"
_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,95}\Z")
_CONTROL_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.+/@:#-]{0,127}\Z")
_SHA256 = re.compile(r"[a-f0-9]{64}\Z")
_FEATURES = {"financial_tools", "skills", "context_compression", "recovery", "multi_agent"}
_PINS = {
    "framework_version", "model_id", "provider", "reasoning", "hardware",
    "permissions_sha256", "assets_sha256", "search_policy",
}


def sha256_json(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _unique_keys(entries: list[tuple[str, Any]]) -> dict[str, Any]:
    value = dict(entries)
    if len(value) != len(entries):
        raise ValueError("JSON object contains duplicate keys")
    return value


def _invalid_constant(value: str) -> None:
    raise ValueError(f"Non-finite JSON constant: {value}")


def read_json(path: Path) -> Any:
    return json.loads(
        path.read_text(encoding="utf-8"), object_pairs_hook=_unique_keys,
        parse_constant=_invalid_constant,
    )


def _object(value: Any, name: str) -> dict:
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be an object")
    return value


def _id(value: Any, name: str) -> str:
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise ValueError(f"{name} must be a short identifier")
    return value


def _range(value: Any, name: str, lo: float, hi: float) -> float:
    if type(value) not in (int, float) or not lo <= value <= hi or not math.isfinite(value):
        raise ValueError(f"{name} must be between {lo} and {hi}")
    return value


def _integer(value: Any, name: str, lo: int, hi: int) -> int:
    if type(value) is not int:
        raise ValueError(f"{name} must be an integer")
    _range(value, name, lo, hi)
    return value


def _keys(value: dict, *, allowed: set[str], required: set[str], name: str) -> None:
    if value.keys() - allowed or required - value.keys():
        raise ValueError(f"Unexpected or missing fields in {name}")


def normalize_plan(document: dict) -> dict:
    """Validate the design before any records or grades are opened."""
    plan = _object(document, "plan")
    required = {
        "experiment_id", "split", "task_ids", "repetitions", "seed", "configurations",
        "comparisons", "cache", "execution_mode", "source_class", "thresholds",
    }
    _keys(plan, allowed=required, required=required, name="plan")
    _id(plan["experiment_id"], "experiment_id")
    for field, allowed in (
        ("split", ("dev", "holdout")), ("cache", ("cold", "warm")),
        ("execution_mode", ("automated", "real_user")),
        ("source_class", ("synthetic", "public_replay", "private_replay", "live")),
    ):
        if plan[field] not in allowed:
            raise ValueError(f"Invalid {field}")

    task_ids = plan["task_ids"]
    if not isinstance(task_ids, list) or not task_ids:
        raise ValueError("task_ids must be a non-empty array")
    task_ids = [_id(item, "task_id") for item in task_ids]
    if len(set(task_ids)) != len(task_ids):
        raise ValueError("Duplicate task IDs in plan")
    repetitions = _integer(plan["repetitions"], "repetitions", 1, 1000)
    _integer(plan["seed"], "seed", 0, 2**32 - 1)
    formal = plan["split"] == "holdout" and plan["source_class"] in {"private_replay", "live"}
    if formal and repetitions < 2:
        raise ValueError("A confirmation plan needs at least two repetitions")

    configurations = _object(plan["configurations"], "configurations")
    if not configurations:
        raise ValueError("At least one configuration is required")
    for variant, config in configurations.items():
        _id(variant, "configuration variant")
        config = _object(config, "configuration")
        basic = {"base_config", "features", "limits"}
        required_config = basic | _PINS if formal else basic
        _keys(config, allowed=basic | _PINS, required=required_config, name="configuration")
        if config["base_config"] not in ("A", "B", "C", "D"):
            raise ValueError("Invalid Issue #8 base_config")
        features = _object(config["features"], "features")
        _keys(features, allowed=_FEATURES, required=_FEATURES, name="features")
        if any(type(enabled) is not bool for enabled in features.values()):
            raise ValueError("Each feature switch must be a boolean")
        limits = _object(config["limits"], "limits")
        limit_keys = {"time_s", "cost_usd", "tokens", "steps"}
        _keys(limits, allowed=limit_keys, required=limit_keys, name="limits")
        for name, value in limits.items():
            if value is not None:
                (_range if name == "cost_usd" else _integer)(value, name, 0, 10**12)
        if formal and any(limits[name] is None or limits[name] <= 0 for name in ("time_s", "cost_usd")):
            raise ValueError("Confirmation requires positive frozen time and cost limits")
        for key in _PINS & config.keys():
            if key.endswith("_sha256"):
                if not isinstance(config[key], str) or not _SHA256.fullmatch(config[key]):
                    raise ValueError(f"Invalid {key}")
            else:
                if not isinstance(config[key], str) or not _CONTROL_ID.fullmatch(config[key]):
                    raise ValueError(f"Invalid {key}")

    comparisons = plan["comparisons"]
    if not isinstance(comparisons, list):
        raise ValueError("comparisons must be an array")
    comparison_ids = set()
    for comparison in comparisons:
        comparison = _object(comparison, "comparison")
        fields = {"id", "left", "right", "kind"}
        _keys(comparison, allowed=fields | {"feature"}, required=fields, name="comparison")
        name = _id(comparison["id"], "comparison ID")
        left = _id(comparison["left"], "left configuration")
        right = _id(comparison["right"], "right configuration")
        kind = comparison["kind"]
        if name in comparison_ids or left == right or {left, right} - configurations.keys():
            raise ValueError("Duplicate comparison or invalid configuration pair")
        comparison_ids.add(name)
        if kind not in ("same_model", "product", "ablation"):
            raise ValueError("Unknown comparison kind")
        if kind == "ablation":
            changed = {
                feature for feature in _FEATURES
                if configurations[left]["features"][feature] != configurations[right]["features"][feature]
            }
            if comparison.get("feature") not in _FEATURES or changed != {comparison["feature"]}:
                raise ValueError("An ablation must change exactly its declared feature switch")
        elif "feature" in comparison:
            raise ValueError("Only an ablation declares a feature")

    thresholds = _object(plan["thresholds"], "thresholds")
    frozen_thresholds = {
        "quality_floor", "equivalence_margin", "confidence", "bootstrap_samples",
        "minimum_human_score",
    }
    _keys(thresholds, allowed=frozen_thresholds, required=frozen_thresholds, name="thresholds")
    for name in ("quality_floor", "equivalence_margin", "minimum_human_score"):
        _range(thresholds[name], name, 0, 1)
    _range(thresholds["confidence"], "confidence", 0.5, 0.9999)
    _integer(thresholds["bootstrap_samples"], "bootstrap_samples", 100, 100_000)
    return json.loads(json.dumps(plan, allow_nan=False))


@dataclass(frozen=True)
class FrozenTask:
    task: dict
    reference: dict
    input_sha256: str
    fixture_sha256: str


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _portable_file_sha256(path: Path) -> str:
    if path.suffix in {".json", ".md", ".py", ".txt"}:
        return hashlib.sha256(path.read_text(encoding="utf-8").encode("utf-8")).hexdigest()
    return _file_sha256(path)


def _safe_path(base: Path, relative: Any) -> Path:
    if not isinstance(relative, str) or not relative or Path(relative).is_absolute():
        raise ValueError("Invalid benchmark-relative path")
    path = (base / relative).resolve()
    if not path.is_relative_to(base.resolve()):
        raise ValueError("Benchmark input path escapes its root")
    return path


def _tree_digest(root: Path) -> str:
    return sha256_json({
        path.relative_to(root).as_posix(): _portable_file_sha256(
            _safe_path(root, path.relative_to(root).as_posix())
        )
        for path in sorted(root.rglob("*")) if path.is_file() and "__pycache__" not in path.parts
    })


def _verify_fixture(root: Path, fixture: str, expected: dict) -> str:
    fixture_dir = _safe_path(root / "benchmark" / "data", fixture)
    specification = read_json(fixture_dir / "fixture.json")
    if specification["fixture"] != fixture:
        raise ValueError("Fixture ID mismatch")
    files = specification["files"]
    actual = {p.relative_to(fixture_dir).as_posix() for p in fixture_dir.rglob("*") if p.is_file()}
    if actual != {"fixture.json", *files}:
        raise ValueError("Frozen fixture file set differs from its manifest")
    member_hashes = []
    for name, entry in files.items():
        path = _safe_path(fixture_dir, name)
        found = _file_sha256(path)
        if found != entry["sha256"] or path.stat().st_size != entry["bytes"]:
            raise ValueError("Frozen fixture differs from its manifest")
        member_hashes.append(found)
    aggregate = hashlib.sha256("".join(sorted(member_hashes)).encode("ascii")).hexdigest()
    if aggregate != expected["aggregate_sha256"]:
        raise ValueError("Frozen fixture differs from the task set index")
    return aggregate


def freeze_protocol(plan: dict, root: Path) -> tuple[dict, dict[str, FrozenTask]]:
    """Load evaluator-only references and fingerprint every selected frozen input."""
    plan = normalize_plan(plan)
    root = root.resolve()
    benchmark = root / "benchmark"
    manifest_path = benchmark / "manifest.json"
    if not manifest_path.is_file():
        raise ValueError(
            f"Missing benchmark/manifest.json under {root}. "
            "Set --suite-root to a task-set directory or the offline fixture suite."
        )
    manifest = _object(read_json(manifest_path), "task set manifest")
    if manifest.get("schema_version") != 1:
        raise ValueError("Unsupported task set schema")
    indexed = {entry["task_id"]: entry for entry in manifest["tasks"]}
    if len(indexed) != len(manifest["tasks"]):
        raise ValueError("Repeated task IDs in suite manifest")
    schema_path = benchmark / "schema" / "task.schema.json"
    if not schema_path.is_file():
        raise ValueError(
            f"Missing benchmark/schema/task.schema.json under {root}. "
            "Set --suite-root to a complete Issue #7 task set."
        )
    validator = Draft202012Validator(_object(read_json(schema_path), "task schema"))
    tasks = {}
    verified_fixtures = {}
    for task_id in plan["task_ids"]:
        item = indexed.get(task_id)
        if item is None or item["split"] != plan["split"]:
            raise ValueError("Selected task is absent from the specified split")
        task_dir = _safe_path(benchmark / "tasks", f"{plan['split']}/{task_id}")
        task = read_json(task_dir / "task.json")
        problems = sorted(validator.iter_errors(task), key=lambda error: list(error.path))
        if problems:
            first = problems[0]
            location = "/".join(str(part) for part in first.path) or "<root>"
            raise ValueError(
                f"Task {task_id} violates benchmark/schema/task.schema.json "
                f"at {location}: {first.message}"
            )
        if task["task_id"] != task_id or task["split"] != plan["split"] or task["schema_version"] != 1:
            raise ValueError("Task manifest identity mismatch")
        reference_dir = _safe_path(benchmark / "reference", f"{plan['split']}/{task_id}")
        ref = _object(
            read_json(_safe_path(reference_dir, task["reference"]["expected_file"])), "reference"
        )
        fixture = _id(task["environment"]["fixture"], "fixture")
        if fixture != item["fixture"]:
            raise ValueError("Task fixture does not match the task set index")
        if fixture not in verified_fixtures:
            verified_fixtures[fixture] = _verify_fixture(root, fixture, manifest["fixtures"][fixture])
        fixture_hash = verified_fixtures[fixture]
        input_hash = sha256_json({
            "task_tree": _tree_digest(task_dir), "reference_tree": _tree_digest(reference_dir),
            "fixture": fixture_hash,
            "fixture_manifest": _portable_file_sha256(benchmark / "data" / fixture / "fixture.json"),
            "schema": _portable_file_sha256(_safe_path(benchmark, "schema/task.schema.json")),
        })
        tasks[task_id] = FrozenTask(task, ref, input_hash, fixture_hash)
    body = {
        "schema_version": 1, "evaluator_version": EVALUATOR_VERSION,
        "task_set": _id(manifest["task_set"], "task_set"),
        "task_set_version": manifest["version"], "plan": plan,
        "inputs": {task_id: frozen.input_sha256 for task_id, frozen in tasks.items()},
    }
    return {**body, "protocol_sha256": sha256_json(body)}, tasks


def verify_protocol(lock: dict, root: Path) -> dict[str, FrozenTask]:
    """Require the sealed protocol and all frozen files to match exactly."""
    rebuilt, tasks = freeze_protocol(_object(lock, "lock").get("plan"), root)
    if rebuilt != lock:
        raise ValueError("Protocol lock or frozen input changed")
    return tasks
