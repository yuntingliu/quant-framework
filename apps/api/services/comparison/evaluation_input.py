"""Read evaluator-side observations without treating staged work as delivery."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from apps.api.services.workspace_output_service import validate_workspace_delivery

from .evaluation_protocol import _SHA256, FrozenTask, sha256_json

_WORKSPACE_NODES = {
    "decisionNotebook": "alphalab-decision-notebook-v1",
    "workspaceResult": "alphalab-workspace-result-v1",
    "workspaceCommands": "alphalab-workspace-commands-v1",
}
_OBSERVED_FIELDS = {
    "initial_state": {"initial_data_hash"},
    "conditions": {
        "variant", "mode", "source", "cache", "stage", "framework_version", "model_id",
        "provider", "reasoning", "hardware", "permissions_sha256", "assets_sha256",
        "search_policy", "features",
    },
    "reproducibility": {"framework_version", "task_version", "evidence_sha256", "experiment_id"},
    "execution": {
        "tool_calls", "confirmations", "initial_authorizations", "unauthorized_actions",
        "duplicate_executions", "fabrication_events", "observation_complete",
    },
    "results": {"semantic_outcome", "delivered_artifacts", "failure_kind", "infrastructure_failure"},
    "resources": {
        "cost_ledger", "cost_ledger_complete", "human_intervention_count", "human_active_minutes",
        "end_to_end_seconds",
    },
}
_REDACTED_DIGESTS = {
    ("initial_state", "initial_data_hash"), ("reproducibility", "task_version"),
    ("reproducibility", "evidence_sha256"), ("conditions", "permissions_sha256"),
    ("conditions", "assets_sha256"),
}


def committed_workspace_artifacts(task: dict, run: dict, workspace: dict) -> dict[str, Any]:
    """Project current-run, validated Conexus nodes onto evaluator artifact IDs.

    Both inputs must come from the trusted runtime, never from agent output.
    """
    run_id = run.get("id")
    if not isinstance(run_id, str) or not run_id:
        raise ValueError("Expected an observed Conexus run ID")
    changes = run.get("nodeChanges") or {}
    changed_ids = set(changes.get("created", [])) | set(changes.get("updated", []))
    nodes = {node["id"]: node for node in workspace.get("nodes", [])}
    if len(nodes) != len(workspace.get("nodes", [])):
        raise ValueError("Duplicate node IDs in the workspace snapshot")

    def from_current_run(node_id: str) -> bool:
        return node_id in changed_ids and nodes.get(node_id, {}).get("updatedByRunId") == run_id

    if not all(from_current_run(node_id) for node_id in _WORKSPACE_NODES.values()):
        return {}
    candidate = deepcopy(workspace)
    candidate.pop("outputValidation", None)
    validation = validate_workspace_delivery(candidate).get("outputValidation", {})
    if validation.get("status") != "succeeded":
        return {}
    result = nodes[_WORKSPACE_NODES["workspaceResult"]]["values"]["data"]
    report_id = result.get("reportId")
    delivered = {}
    for artifact in task["target_artifacts"]:
        kind = artifact["kind"]
        if kind == "harness_output" and artifact.get("node") in _WORKSPACE_NODES:
            node = _WORKSPACE_NODES[artifact["node"]]
            delivered[artifact["id"]] = deepcopy(nodes[node]["values"]["data"])
        elif kind == "agent_findings" and result["kind"] == "document" and from_current_run(report_id):
            delivered[artifact["id"]] = deepcopy(result)
    return delivered


def bind_observation(raw: dict, observation: dict) -> dict:
    """Attach separate trusted collector evidence to exactly one neutral record."""
    if not isinstance(observation, dict) or set(observation) != {"record_sha256", "observed"}:
        raise ValueError("Invalid observation envelope")
    if observation["record_sha256"] != sha256_json(raw):
        raise ValueError("Observation does not match the neutral record")
    observed = observation["observed"]
    if not isinstance(observed, dict) or observed.keys() - _OBSERVED_FIELDS.keys():
        raise ValueError("Unknown observation section")
    record = deepcopy(raw)
    for section, fields in observed.items():
        if not isinstance(fields, dict) or fields.keys() - _OBSERVED_FIELDS[section]:
            raise ValueError("Unknown observation field")
        target = record.setdefault(section, {})
        if not isinstance(target, dict):
            raise ValueError("Malformed neutral record section")
        for key, value in fields.items():
            if key in target and target[key] is not None and target[key] != value:
                # PR #39 reserves empty placeholders in these fields for captured evidence.
                reserved = section == "execution" and key in {"tool_calls", "confirmations"} and target[key] == []
                redacted = (
                    (section, key) in _REDACTED_DIGESTS and target[key] == "<redacted>"
                    and isinstance(value, str) and _SHA256.fullmatch(value)
                )
                if not (reserved or redacted):
                    raise ValueError("Observation conflicts with a recorded value")
            target[key] = deepcopy(value)
    return record


def _cache(conditions: dict) -> str | None:
    cold, warm = conditions.get("cold_start"), conditions.get("warm_cache")
    recorded = "cold" if cold is True and warm is False else None
    if warm is True and cold is False:
        recorded = "warm"
    explicit = conditions.get("cache")
    if explicit is not None and recorded is not None and explicit != recorded:
        raise ValueError("Conflicting cache evidence")
    return explicit or recorded


def validate_trial_for_plan(record: dict, frozen: FrozenTask, plan: dict, *, order: dict) -> dict:
    """Validate pairing and immutable task identity while retaining bad outcomes."""
    record = deepcopy(record)
    task = frozen.task
    conditions = record["conditions"]
    reproducibility = record["reproducibility"]
    if not isinstance(conditions, dict) or not isinstance(reproducibility, dict):
        raise ValueError("Malformed neutral record metadata")
    repetition = reproducibility.get("repetition_index")
    if type(repetition) is not int or not 0 <= repetition < plan["repetitions"]:
        raise ValueError("Trial repetition was outside the frozen design")
    if record["task_id"] != task["task_id"] or record["task_category"] != task["category"]:
        raise ValueError("Trial task identity differs from the frozen task")
    variant = conditions.get("variant", record["config"])
    if variant not in plan["configurations"] or record["config"] != plan["configurations"][variant]["base_config"]:
        raise ValueError("Trial configuration was outside the frozen design")
    design = plan["configurations"][variant]
    is_live = plan["source_class"] == "live"
    formal = plan["split"] == "holdout" and plan["source_class"] in {"private_replay", "live"}
    mode = "human" if plan["execution_mode"] == "real_user" else "automated"
    source = {"live": "live", "synthetic": "double"}.get(plan["source_class"], "recorded")
    stage = "confirmation" if plan["split"] == "holdout" else "development"
    if is_live and (
        record.get("budget", {}).get("enabled") is not True
        or record.get("budget", {}).get("opt_in") is not True
    ):
        raise ValueError("A live trial must come from an explicitly enabled real run")
    if not is_live and source == "double" and record.get("budget", {}).get("enabled") is True:
        raise ValueError("A paid record cannot enter a synthetic experiment")
    identity = {
        "experiment_id": plan["experiment_id"], "task_version": frozen.input_sha256,
        "repetition_count": plan["repetitions"], "interleave_seed": plan["seed"],
    }
    known_checks = {"mode": mode, "source": source, "stage": stage}
    for key, expected in identity.items():
        if (formal or key in reproducibility) and reproducibility.get(key) != expected:
            raise ValueError(f"Trial {key} differs from the frozen design")
    for key, expected in known_checks.items():
        if (formal or key in conditions) and conditions.get(key) != expected:
            raise ValueError(f"Trial {key} differs from the frozen design")
    cache = _cache(conditions)
    if (formal or cache is not None) and cache != plan["cache"]:
        raise ValueError("Trial cache differs from the frozen design")
    if formal:
        unmatched = conditions.get("conditions_not_matched", [])
        if not isinstance(unmatched, list):
            raise ValueError("Malformed unmatched condition list")
        if record.get("initial_state", {}).get("initial_data_hash") != frozen.fixture_sha256:
            unmatched.append("initial_data_hash")
        if record.get("reset", {}).get("verified") is not True:
            unmatched.append("fixture_reset")
        for key, expected in design.items():
            if key == "base_config":
                continue
            observed = reproducibility.get(key, conditions.get(key)) if key == "framework_version" else conditions.get(key)
            if observed != expected:
                unmatched.append(key)
        if record.get("execution", {}).get("observation_complete") is not True:
            unmatched.append("observation_complete")
        expected_position = order[(task["task_id"], variant, repetition)]
        if (
            type(reproducibility.get("interleaved_order")) is not int
            or reproducibility["interleaved_order"] != expected_position
            or reproducibility.get("plan_size") != len(order)
        ):
            unmatched.append("interleaved_order")
        conditions["conditions_not_matched"] = unmatched
    conditions.update(known_checks, cache=cache or "unknown", variant=variant)
    # Local rehearsal records may lack trusted pins. This label comes from the
    # evaluator's task files and does not attest that the runner restored them.
    reproducibility.setdefault("task_version", frozen.input_sha256)
    return record
