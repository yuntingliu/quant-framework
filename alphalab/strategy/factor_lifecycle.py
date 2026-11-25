"""Factor lifecycle decisions and immutable, version-bound research evidence."""

from __future__ import annotations

import ast
import json
import uuid
from typing import Any

from alphalab.strategy.source import inspect_strategy_source

_STATUSES = {"candidate", "observation", "adopted", "retired"}
_TRANSITIONS = {
    "candidate": {"observation", "retired"},
    "observation": {"candidate", "adopted", "retired"},
    "adopted": {"observation", "retired"},
    "retired": {"candidate"},
}


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


class FactorLifecycle:
    """Repository mixin; all writes use the repository's canonical connection."""

    def record_factor_evidence(
        self,
        project_id: str,
        factor_id: str,
        strategy_id: str,
        result: dict[str, Any],
        request: dict[str, Any],
        sample: dict[str, Any],
        assumptions: dict[str, Any],
        data_requirements: dict[str, Any],
        factor_revision_id: int | None = None,
    ) -> dict[str, Any]:
        path = f"factors/{factor_id}.py"
        evidence_id = str(uuid.uuid4())
        manifest = {
            "status": "unavailable",
            "reason": "Issue #15 data snapshots and reproducibility manifests are not implemented",
        }
        with self._lock, self._conn:
            self._conn.execute("BEGIN IMMEDIATE")
            actual_unit = self._conn.execute(
                """SELECT source_sha256 FROM strategy_source_package_units
                   WHERE project_id=? AND revision=? AND path=? AND kind='factor'""",
                (project_id, int(result["revision"]), path),
            ).fetchone()
            if actual_unit is None:
                raise ValueError("research package or factor unit is unavailable")
            if factor_revision_id is not None:
                revision = self._conn.execute(
                    """SELECT r.id, r.asset_id, r.package_revision, u.source_sha256
                       FROM factor_source_revisions r
                       JOIN strategy_source_package_units u
                         ON u.project_id=r.project_id AND u.path=r.path AND u.revision=r.package_revision
                       WHERE r.project_id=? AND r.id=? AND r.path=?""",
                    (project_id, factor_revision_id, path),
                ).fetchone()
            else:
                candidates = self._conn.execute(
                    """SELECT r.id, r.asset_id, r.package_revision, u.source_sha256
                       FROM factor_source_revisions r
                       JOIN strategy_source_package_units u
                         ON u.project_id=r.project_id AND u.path=r.path AND u.revision=r.package_revision
                       WHERE r.project_id=? AND r.path=? AND u.source_sha256=?
                       ORDER BY r.id DESC LIMIT 2""",
                    (project_id, path, actual_unit["source_sha256"]),
                ).fetchall()
                if len(candidates) > 1:
                    raise ValueError("factor_revision_id is required to disambiguate identical source revisions")
                revision = candidates[0] if candidates else None
            if revision is None or revision["asset_id"] is None:
                raise KeyError(factor_id)
            if (
                revision["package_revision"] is None
                or revision["source_sha256"] != actual_unit["source_sha256"]
            ):
                raise ValueError("factor revision does not match the executed factor unit")
            package = self._conn.execute(
                "SELECT revision, source_sha256 FROM strategy_source_packages WHERE project_id=? AND revision=?",
                (project_id, int(result["revision"])),
            ).fetchone()
            unit = self._conn.execute(
                """SELECT source_sha256 FROM strategy_source_package_units
                   WHERE project_id=? AND revision=? AND path=? AND kind='factor'""",
                (project_id, int(result["revision"]), path),
            ).fetchone()
            if (
                package is None
                or unit is None
                or package["source_sha256"] != result["source_sha256"]
            ):
                raise ValueError("research package or factor unit is unavailable")
            self._conn.execute(
                """INSERT INTO factor_evidence(
                       id, project_id, asset_id, factor_revision_id, factor_unit_sha256,
                       strategy_id, package_revision, package_sha256, request_json,
                       sample_json, assumptions_json, data_requirements_json,
                       data_manifest_json, result_json
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    evidence_id,
                    project_id,
                    revision["asset_id"],
                    revision["id"],
                    actual_unit["source_sha256"],
                    strategy_id,
                    package["revision"],
                    package["source_sha256"],
                    _json(request),
                    _json(sample),
                    _json(assumptions),
                    _json(data_requirements),
                    _json(manifest),
                    _json(result),
                ),
            )
        return self.get_factor_evidence(project_id, evidence_id)

    def get_factor_evidence(self, project_id: str, evidence_id: str) -> dict[str, Any]:
        row = self._conn.execute(
            "SELECT * FROM factor_evidence WHERE project_id=? AND id=?",
            (project_id, evidence_id),
        ).fetchone()
        if row is None:
            raise KeyError(evidence_id)
        return self._evidence_payload(row)

    def list_factor_status_transitions(self, project_id: str, asset_id: str) -> list[dict[str, Any]]:
        self.get_factor_asset(project_id, asset_id)
        rows = self._conn.execute(
            "SELECT * FROM factor_status_transitions WHERE project_id=? AND asset_id=? ORDER BY rowid",
            (project_id, asset_id),
        ).fetchall()
        items = []
        for row in rows:
            item = dict(row)
            item["evidence_ids"] = [
                value[0]
                for value in self._conn.execute(
                    "SELECT evidence_id FROM factor_status_transition_evidence WHERE transition_id=?",
                    (row["id"],),
                ).fetchall()
            ]
            items.append(item)
        return items

    def _reset_factor_asset_status(self, asset, new_revision_id: int) -> None:
        if (
            asset["current_revision_id"] == new_revision_id
            or asset["status"] not in {"observation", "adopted"}
        ):
            return
        transition_id = str(uuid.uuid4())
        self._conn.execute(
            """INSERT INTO factor_status_transitions(
                   id, project_id, asset_id, from_status, to_status,
                   factor_revision_id, reason, origin
               ) VALUES (?, ?, ?, ?, 'candidate', ?, ?, 'system_definition_reset')""",
            (
                transition_id,
                asset["project_id"],
                asset["id"],
                asset["status"],
                new_revision_id,
                "Factor definition changed; previous evidence remains bound to its original revision",
            ),
        )
        self._conn.execute(
            """UPDATE factor_assets SET status='candidate',
               status_revision=status_revision+1, updated_at=datetime('now') WHERE id=?""",
            (asset["id"],),
        )

    def transition_factor_asset(
        self,
        project_id: str,
        asset_id: str,
        to_status: str,
        *,
        reason: str,
        expected_status_revision: int,
        evidence_ids: list[str],
        request_id: str,
    ) -> dict[str, Any]:
        if to_status not in _STATUSES:
            raise ValueError("unsupported factor status")
        if not reason.strip():
            raise ValueError("factor status transition reason is required")
        with self._lock, self._conn:
            self._conn.execute("BEGIN IMMEDIATE")
            replay = self._conn.execute(
                "SELECT * FROM factor_status_transitions WHERE asset_id=? AND request_id=?",
                (asset_id, request_id),
            ).fetchone()
            if replay is not None:
                replay_evidence = {
                    row[0] for row in self._conn.execute(
                        "SELECT evidence_id FROM factor_status_transition_evidence WHERE transition_id=?",
                        (replay["id"],),
                    ).fetchall()
                }
                if (
                    replay["to_status"] != to_status
                    or replay["reason"] != reason.strip()
                    or replay["expected_status_revision"] != expected_status_revision
                    or replay_evidence != set(evidence_ids)
                ):
                    raise RuntimeError("factor asset status request changed since it was first accepted")
                return {
                    "transition": dict(replay),
                    "asset": self.get_factor_asset(project_id, asset_id),
                }
            asset = self._conn.execute(
                "SELECT * FROM factor_assets WHERE project_id=? AND id=?",
                (project_id, asset_id),
            ).fetchone()
            if asset is None:
                raise KeyError(asset_id)
            if int(asset["status_revision"]) != expected_status_revision:
                raise RuntimeError("factor asset status changed since it was inspected")
            if asset["status"] == to_status or to_status not in _TRANSITIONS[asset["status"]]:
                raise ValueError(f"cannot transition {asset['status']} to {to_status}")
            if to_status in {"observation", "adopted"}:
                if not evidence_ids:
                    raise ValueError("observation and adoption require current-revision evidence")
                for evidence_id in evidence_ids:
                    evidence = self._conn.execute(
                        "SELECT asset_id, factor_revision_id FROM factor_evidence WHERE project_id=? AND id=?",
                        (project_id, evidence_id),
                    ).fetchone()
                    if (
                        evidence is None
                        or evidence["asset_id"] != asset_id
                        or evidence["factor_revision_id"] != asset["current_revision_id"]
                    ):
                        raise ValueError("evidence must belong to the asset's current factor revision")
            transition_id = str(uuid.uuid4())
            self._conn.execute(
                """INSERT INTO factor_status_transitions(
                       id, project_id, asset_id, from_status, to_status,
                       factor_revision_id, reason, origin, expected_status_revision, request_id
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, 'user', ?, ?)""",
                (
                    transition_id,
                    project_id,
                    asset_id,
                    asset["status"],
                    to_status,
                    asset["current_revision_id"],
                    reason.strip(),
                    expected_status_revision,
                    request_id,
                ),
            )
            for evidence_id in dict.fromkeys(evidence_ids):
                self._conn.execute(
                    "INSERT INTO factor_status_transition_evidence VALUES (?, ?)",
                    (transition_id, evidence_id),
                )
            self._conn.execute(
                """UPDATE factor_assets SET status=?, status_revision=status_revision+1,
                   updated_at=datetime('now') WHERE id=?""",
                (to_status, asset_id),
            )
            transition = self._conn.execute(
                "SELECT * FROM factor_status_transitions WHERE id=?", (transition_id,)
            ).fetchone()
            return {
                "transition": dict(transition),
                "asset": self.get_factor_asset(project_id, asset_id),
            }

    def get_factor_asset_detail(self, project_id: str, asset_id: str) -> dict[str, Any]:
        asset = self.get_factor_asset(project_id, asset_id)
        package_revision = asset.get("package_revision")
        requirements = None
        if package_revision is not None:
            row = self._conn.execute(
                "SELECT data_requirements_json FROM strategy_source_packages WHERE project_id=? AND revision=?",
                (project_id, package_revision),
            ).fetchone()
            if row is not None:
                requirements = json.loads(row["data_requirements_json"])
        usage = []
        factor_id = asset["factor_id"]
        for strategy in self._strategy_rows(project_id):
            if strategy["archived"]:
                continue
            kind = "unknown_or_potential"
            inspection = inspect_strategy_source(strategy["source"])
            for item in inspection.entrypoints:
                if item.kind != "signal":
                    continue
                blend = item.metadata.get("factor_blend")
                if not isinstance(blend, dict):
                    continue
                identifiers = set(blend.get("weights", {}))
                if blend.get("factor"):
                    identifiers.add(blend["factor"])
                kind = "signal_direct" if factor_id in identifiers else "assembly_scope"
            if kind != "signal_direct":
                for node in ast.walk(ast.parse(strategy["source"])):
                    if (
                        isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Attribute)
                        and node.func.attr == "factor"
                        and node.args
                        and isinstance(node.args[0], ast.Constant)
                        and node.args[0].value == factor_id
                    ):
                        kind = "signal_static_reference"
                        break
            usage.append({
                "strategy_id": strategy["id"],
                "name": strategy["name"],
                "assembly_scope": True,
                "usage": kind,
            })
        return {
            "asset": asset,
            "transitions": self.list_factor_status_transitions(project_id, asset_id),
            "usage": usage,
            "data_requirements": requirements,
            "data_requirements_scope": "strategy_package" if requirements is not None else None,
        }
