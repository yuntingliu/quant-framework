"""Validate report evidence references and protect them without duplicating documents."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from contextlib import closing
from pathlib import Path
from typing import Any

from alphalab.utils.paths import RUNTIME_APP_DIR

_SCHEMA = """
CREATE TABLE IF NOT EXISTS report_reference_reservations (
    id TEXT PRIMARY KEY,
    report_id TEXT NOT NULL,
    request_id TEXT,
    reference_json TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('reserved', 'confirmed')),
    created_at TEXT DEFAULT (datetime('now')),
    confirmed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_report_reference_reservations_report
    ON report_reference_reservations(report_id, status);
"""


class ReportReferenceStore:
    def __init__(self, alphalab_path: str | Path | None = None, dataio_path: str | Path | None = None):
        self.alphalab_path = Path(alphalab_path) if alphalab_path else RUNTIME_APP_DIR / "alphalab.db"
        self.dataio_path = Path(dataio_path) if dataio_path else RUNTIME_APP_DIR / "dataio.db"

    def _connect(self, path: Path, *, readonly: bool = False) -> sqlite3.Connection:
        if readonly:
            connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
            connection.execute("PRAGMA query_only = ON")
        else:
            connection = sqlite3.connect(path)
        connection.row_factory = sqlite3.Row
        return connection

    def _verified(self, label: str, revision: int | str, sha256: str | None = None) -> dict[str, Any]:
        return {
            "status": "verified",
            "label": label,
            "revision": revision,
            "source_sha256": sha256,
        }

    def validate(self, project_id: str, references: list[dict[str, Any]]) -> dict[str, Any]:
        with closing(self._connect(self.alphalab_path, readonly=True)) as alphalab:
            alphalab.execute("BEGIN")
            if alphalab.execute("SELECT 1 FROM strategy_projects WHERE id=?", (project_id,)).fetchone() is None:
                raise KeyError(project_id)
            results = []
            for reference in references:
                kind = reference.get("kind")
                item = {"kind": kind, "id": reference.get("id")}
                if "revision" in reference:
                    item["revision"] = reference["revision"]
                if kind in {"strategy_package", "validation_package"}:
                    table = "strategy_source_packages" if kind == "strategy_package" else "validation_source_packages"
                    revision = reference.get("revision")
                    row = alphalab.execute(
                        f"SELECT revision, source_sha256 FROM {table} WHERE project_id=? AND revision=?",
                        (project_id, revision),
                    ).fetchone()
                    label = "Strategy package" if kind == "strategy_package" else "Validation package"
                    item.update(
                        self._verified(f"{label} v{revision}", revision, row["source_sha256"])
                        if row else {"status": "missing", "label": f"{label} v{revision}"}
                    )
                elif kind == "factor_evidence":
                    row = alphalab.execute(
                        """SELECT e.id, e.factor_revision_id, e.factor_unit_sha256,
                                  e.sample_json, e.result_json, a.status AS factor_status,
                                  a.path AS factor_path
                           FROM factor_evidence e JOIN factor_assets a ON a.id=e.asset_id
                           WHERE e.project_id=? AND e.id=?""",
                        (project_id, reference.get("id")),
                    ).fetchone()
                    if row:
                        item.update(self._verified(
                            f"Factor evidence r{row['factor_revision_id']}",
                            row["factor_revision_id"],
                            row["factor_unit_sha256"],
                        ))
                        item["details"] = {
                            "sample": json.loads(row["sample_json"]),
                            "summary": json.loads(row["result_json"]).get("summary", {}),
                        }
                        item["factor_status"] = row["factor_status"]
                        item["factor_path"] = row["factor_path"]
                    else:
                        item.update({"status": "missing", "label": "Factor evidence"})
                elif kind == "recipe_revision":
                    revision = reference.get("revision")
                    label = f"Recipe revision r{revision}"
                    try:
                        with closing(self._connect(self.dataio_path, readonly=True)) as dataio:
                            row = dataio.execute(
                                "SELECT revision, source_sha256 FROM data_recipe_history WHERE project_id=? AND revision=?",
                                (project_id, revision),
                            ).fetchone()
                    except sqlite3.OperationalError:
                        item.update(status="unavailable", label=label,
                                    reason="Recipe history is unavailable")
                    else:
                        item.update(
                            self._verified(label, revision, row["source_sha256"])
                            if row else {"status": "missing", "label": label}
                        )
                elif kind == "backtest":
                    row = alphalab.execute(
                        """SELECT id, strategy_revision, strategy_source_sha256, validation_revision,
                                  validation_source_sha256, config_yaml, provenance_json
                           FROM backtests WHERE id=? AND strategy_project_id=?""",
                        (reference.get("id"), project_id),
                    ).fetchone()
                    if row:
                        item.update(self._verified(f"Backtest {reference['id']}", row["id"]))
                        item["details"] = {
                            "strategy_revision": row["strategy_revision"],
                            "validation_revision": row["validation_revision"],
                            "strategy_source_sha256": row["strategy_source_sha256"],
                            "validation_source_sha256": row["validation_source_sha256"],
                            "config_yaml": row["config_yaml"],
                            "data_manifest": self._frozen_manifest(row["provenance_json"]),
                        }
                    else:
                        item.update({"status": "missing", "label": "Backtest"})
                elif kind == "data_manifest":
                    item.update(status="unavailable", label="Data manifest unavailable in linked backtest provenance")
                elif kind in {"experiment", "trial"}:
                    item.update({
                        "status": "not_linked" if not reference.get("id") else "unavailable",
                        "label": "No experiment linked" if not reference.get("id") else "Issue #18 experiment integration is unavailable",
                    })
                else:
                    raise ValueError(f"unsupported report reference kind: {kind}")
                results.append(item)
            self._check_backtest_links(results)
            self._link_data_manifests(results)
            return {
                "project_id": project_id,
                "references": results,
                "status": "verified" if results and all(item["status"] == "verified" for item in results) else "incomplete",
            }

    @staticmethod
    def _frozen_manifest(provenance_json: str | None) -> dict[str, Any] | None:
        try:
            provenance = json.loads(provenance_json or "{}")
            data = provenance.get("data") or {}
            manifest = data.get("manifest") or {}
            manifest_id = provenance.get("data_manifest_id")
            if data.get("kind") != "data_snapshot" or not manifest_id or manifest.get("id") != manifest_id:
                return None
            payload = {key: value for key, value in manifest.items() if key != "id"}
            encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
            if hashlib.sha256(encoded).hexdigest() != manifest_id:
                return None
            return {"id": manifest_id, "datasets": manifest["datasets"], "captured_at": manifest["captured_at"],
                    "partition_count": len(manifest["files"])}
        except (ValueError, TypeError, AttributeError, KeyError):
            return None

    @staticmethod
    def _link_data_manifests(references: list[dict[str, Any]]) -> None:
        for reference in references:
            if reference["kind"] != "data_manifest" or not reference["id"]:
                continue
            for run in references:
                if run["kind"] != "backtest" or run["status"] != "verified":
                    continue
                manifest = run["details"]["data_manifest"]
                if manifest and manifest["id"] == reference["id"]:
                    reference.update(status="verified", label="Snapshot recorded by backtest",
                                     details={**manifest, "backtest_id": run["id"]})
                    break

    @staticmethod
    def _check_backtest_links(references: list[dict[str, Any]]) -> None:
        runs = [ref for ref in references if ref["kind"] == "backtest" and ref["status"] == "verified"]
        for code_kind in ("strategy", "validation"):
            packages = [ref for ref in references if ref["kind"] == f"{code_kind}_package"]
            if not packages:
                continue
            for run in runs:
                frozen_revision = run["details"][f"{code_kind}_revision"]
                frozen_hash = run["details"][f"{code_kind}_source_sha256"]
                if any(
                    ref["status"] == "verified" and ref["revision"] == frozen_revision
                    and (not frozen_hash or ref["source_sha256"] == frozen_hash)
                    for ref in packages
                ):
                    continue
                actual = f"v{frozen_revision}" if frozen_revision is not None else "unknown"
                run["status"] = "mismatch"
                reasons = run.setdefault("issues", [])
                reasons.append({"kind": f"{code_kind}_package", "actual_revision": frozen_revision,
                                "message": f"Backtest uses {code_kind} revision {actual}, which is not among the matching report references."})

    def reserve(self, report_id: str, request_id: str, project_id: str, references: list[dict[str, Any]]) -> dict[str, Any]:
        with self._connect(self.alphalab_path) as connection:
            connection.executescript(_SCHEMA)
            columns = {
                row["name"] for row in connection.execute("PRAGMA table_info(report_reference_reservations)")
            }
            if "request_id" not in columns:
                connection.execute("ALTER TABLE report_reference_reservations ADD COLUMN request_id TEXT")
            connection.execute("BEGIN IMMEDIATE")
            validated = self.validate(project_id, references)
            reservations = []
            for reference in validated["references"]:
                if reference["status"] != "verified":
                    continue
                encoded = json.dumps(reference, sort_keys=True)
                row = connection.execute(
                    "SELECT id, status FROM report_reference_reservations WHERE report_id=? AND request_id=? AND reference_json=?",
                    (report_id, request_id, encoded),
                ).fetchone()
                if row is None:
                    reservation_id = str(uuid.uuid4())
                    connection.execute(
                        "INSERT INTO report_reference_reservations(id, report_id, request_id, reference_json, status) VALUES (?, ?, ?, ?, 'reserved')",
                        (reservation_id, report_id, request_id, encoded),
                    )
                else:
                    reservation_id = row["id"]
                reservations.append({"id": reservation_id, "kind": reference["kind"], "status": row["status"] if row else "reserved"})
            return {**validated, "reservations": reservations}

    def confirm(self, report_id: str, request_id: str) -> None:
        with self._connect(self.alphalab_path) as connection:
            connection.execute(
                """UPDATE report_reference_reservations
                   SET status='confirmed', confirmed_at=datetime('now')
                   WHERE report_id=? AND request_id=? AND status='reserved'""",
                (report_id, request_id),
            )

    def list_reservations(self, report_id: str) -> list[dict[str, Any]]:
        with self._connect(self.alphalab_path, readonly=True) as connection:
            return [dict(row) for row in connection.execute(
                "SELECT * FROM report_reference_reservations WHERE report_id=? ORDER BY created_at, id",
                (report_id,),
            ).fetchall()]


def store() -> ReportReferenceStore:
    return ReportReferenceStore()
