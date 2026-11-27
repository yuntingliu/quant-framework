"""Stable factor asset identity, independent of runtime IDs and paths."""

from __future__ import annotations

import json
import uuid
from typing import Any

import libcst as cst

from alphalab.strategy.factor_lifecycle import FactorLifecycle

_MIGRATION = "strategy-factor-assets-v1"


def _new_id() -> str:
    return str(uuid.uuid4())


def _asset_payload(row) -> dict[str, Any]:
    payload = dict(row)
    payload["factor_id"] = payload["path"].removeprefix("factors/").removesuffix(".py")
    payload["package_revision"] = payload.pop("package_revision", None)
    payload.pop("first_revision_id", None)
    return payload


class FactorAssets(FactorLifecycle):
    """Repository mixin for asset identity and exact, recorded lineage."""

    def _ensure_factor_asset_schema(self) -> None:
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                columns = {
                    row["name"]
                    for row in self._conn.execute("PRAGMA table_info(factor_source_revisions)")
                }
                if "asset_id" not in columns:
                    self._conn.execute("ALTER TABLE factor_source_revisions ADD COLUMN asset_id TEXT")
                transition_columns = {
                    row["name"]
                    for row in self._conn.execute("PRAGMA table_info(factor_status_transitions)")
                }
                if "expected_status_revision" not in transition_columns:
                    self._conn.execute(
                        "ALTER TABLE factor_status_transitions ADD COLUMN expected_status_revision INTEGER"
                    )
                self._conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_factor_source_revisions_asset "
                    "ON factor_source_revisions(asset_id, id)"
                )
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    def _ensure_factor_assets(self) -> None:
        with self._lock:
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                if self._conn.execute(
                    "SELECT 1 FROM strategy_contract_migrations WHERE name = ?", (_MIGRATION,)
                ).fetchone():
                    self._conn.commit()
                    return
                paths = self._conn.execute(
                    "SELECT DISTINCT project_id, path FROM factor_source_revisions "
                    "ORDER BY project_id, path"
                ).fetchall()
                count = 0
                for owner in paths:
                    rows = self._conn.execute(
                        "SELECT id, package_revision FROM factor_source_revisions "
                        "WHERE project_id = ? AND path = ? ORDER BY id",
                        (owner["project_id"], owner["path"]),
                    ).fetchall()
                    segment = []
                    for row in [*rows, None]:
                        if row is not None and row["package_revision"] is not None:
                            segment.append(row)
                            continue
                        if segment:
                            current_revision_id = None if row is not None else segment[-1]["id"]
                            asset_id = self._create_backfilled_asset(owner, segment, current_revision_id)
                            count += 1
                            segment = []
                            if row is not None:
                                # A NULL revision terminates this exact asset lifetime.
                                self._conn.execute(
                                    "UPDATE factor_source_revisions SET asset_id = ? WHERE id = ?",
                                    (asset_id, row["id"]),
                                )
                self._conn.execute(
                    "INSERT INTO strategy_contract_migrations(name, detail_json) VALUES (?, ?)",
                    (_MIGRATION, json.dumps({"assets": count})),
                )
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    def _create_backfilled_asset(self, owner, rows, current_revision_id) -> str:
        asset_id = _new_id()
        self._conn.execute(
            "INSERT INTO factor_assets(id, project_id, path, current_revision_id, status, "
            "status_revision, copied_from_asset_id, copied_from_revision_id) "
            "VALUES (?, ?, ?, ?, 'candidate', 1, NULL, NULL)",
            (asset_id, owner["project_id"], owner["path"], current_revision_id),
        )
        self._conn.executemany(
            "UPDATE factor_source_revisions SET asset_id = ? WHERE id = ?",
            [(asset_id, row["id"]) for row in rows],
        )
        return asset_id

    def _initialize_factor_assets(self, project_id: str) -> None:
        current = {
            row["path"]: row["source"]
            for row in self._source_unit_rows(project_id)
            if row["kind"] == "factor"
        }
        with self._lock:
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                self._sync_factor_assets(project_id, {}, current, None)
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    def list_factor_assets(self, project_id: str) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT a.*, r.package_revision, "
            "(SELECT MIN(fr.id) FROM factor_source_revisions fr WHERE fr.asset_id = a.id) "
            "AS first_revision_id "
            "FROM factor_assets a "
            "LEFT JOIN factor_source_revisions r ON r.id = a.current_revision_id "
            "WHERE a.project_id = ? ORDER BY first_revision_id, a.id",
            (project_id,),
        ).fetchall()
        return [_asset_payload(row) for row in rows]

    def get_factor_asset(self, project_id: str, asset_id: str) -> dict[str, Any]:
        row = self._conn.execute(
            "SELECT a.*, r.package_revision, "
            "(SELECT MIN(fr.id) FROM factor_source_revisions fr WHERE fr.asset_id = a.id) "
            "AS first_revision_id FROM factor_assets a "
            "LEFT JOIN factor_source_revisions r ON r.id = a.current_revision_id "
            "WHERE a.project_id = ? AND a.id = ?",
            (project_id, asset_id),
        ).fetchone()
        if row is None:
            raise KeyError(asset_id)
        return _asset_payload(row)

    def list_factor_evidence(
        self, project_id: str, asset_id: str, *, limit: int = 20, before: str | None = None
    ) -> dict[str, Any]:
        self.get_factor_asset(project_id, asset_id)
        if before:
            created_before, id_before = before.rsplit("|", 1)
            rows = self._conn.execute(
                "SELECT * FROM factor_evidence WHERE project_id = ? AND asset_id = ? "
                "AND (created_at < ? OR (created_at = ? AND id < ?)) "
                "ORDER BY created_at DESC, id DESC LIMIT ?",
                (project_id, asset_id, created_before, created_before, id_before, limit + 1),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM factor_evidence WHERE project_id = ? AND asset_id = ? "
                "ORDER BY created_at DESC, id DESC LIMIT ?",
                (project_id, asset_id, limit + 1),
            ).fetchall()
        items = [self._evidence_payload(row) for row in rows[:limit]]
        return {
            "items": items,
            "next_before": (
                f'{items[-1]["created_at"]}|{items[-1]["id"]}' if len(rows) > limit else None
            ),
        }

    @staticmethod
    def _evidence_payload(row) -> dict[str, Any]:
        payload = dict(row)
        for field in (
            "request_json",
            "sample_json",
            "assumptions_json",
            "data_requirements_json",
            "data_manifest_json",
            "result_json",
        ):
            payload[field.removesuffix("_json")] = json.loads(payload.pop(field))
        return payload

    def _sync_factor_assets(self, project_id, previous, current, _package, copy=None, rename=None) -> None:
        removed = sorted(previous.keys() - current.keys())
        added = sorted(current.keys() - previous.keys())
        rename = (rename, added[0]) if rename and len(removed) == len(added) == 1 else None
        touched = sorted(previous.keys() | current.keys())
        handled_renames = set()
        for path in touched:
            if path in handled_renames:
                continue
            revision = self._conn.execute(
                "SELECT id, package_revision FROM factor_source_revisions "
                "WHERE project_id = ? AND path = ? ORDER BY id DESC LIMIT 1",
                (project_id, path),
            ).fetchone()
            if revision is None:
                continue
            if rename and path in rename:
                old_path, new_path = rename
                asset = self._conn.execute(
                    "SELECT * FROM factor_assets WHERE project_id = ? AND path = ? "
                    "AND current_revision_id IS NOT NULL",
                    (project_id, old_path),
                ).fetchone()
                if asset is not None:
                    current_id = self._conn.execute(
                        "SELECT id FROM factor_source_revisions "
                        "WHERE project_id = ? AND path = ? ORDER BY id DESC LIMIT 1",
                        (project_id, new_path),
                    ).fetchone()[0]
                    self._reset_factor_asset_status(asset, current_id)
                    self._conn.execute(
                        "UPDATE factor_assets SET path = ?, current_revision_id = ?, "
                        "updated_at = datetime('now') WHERE id = ?",
                        (new_path, current_id, asset["id"]),
                    )
                    for changed_path in rename:
                        changed = self._conn.execute(
                            "SELECT id FROM factor_source_revisions "
                            "WHERE project_id = ? AND path = ? ORDER BY id DESC LIMIT 1",
                            (project_id, changed_path),
                        ).fetchone()
                        self._conn.execute(
                            "UPDATE factor_source_revisions SET asset_id = ? WHERE id = ?",
                            (asset["id"], changed[0]),
                        )
                    handled_renames.update(rename)
                    continue
            asset = self._conn.execute(
                "SELECT * FROM factor_assets WHERE project_id = ? AND path = ? "
                "AND current_revision_id IS NOT NULL ORDER BY id LIMIT 1",
                (project_id, path),
            ).fetchone()
            if path in added or asset is None:
                if revision["package_revision"] is None:
                    continue
                asset_id = _new_id()
                copied_from = copy if copy and path == copy["path"] else None
                self._conn.execute(
                    "INSERT INTO factor_assets(id, project_id, path, current_revision_id, "
                    "status, status_revision, copied_from_asset_id, copied_from_revision_id) "
                    "VALUES (?, ?, ?, ?, 'candidate', 1, ?, ?)",
                    (
                        asset_id,
                        project_id,
                        path,
                        revision["id"],
                        copied_from["asset_id"] if copied_from else None,
                        copied_from["revision_id"] if copied_from else None,
                    ),
                )
            else:
                asset_id = asset["id"]
                if revision["package_revision"] is not None:
                    self._reset_factor_asset_status(asset, revision["id"])
                self._conn.execute(
                    "UPDATE factor_assets SET current_revision_id = ?, "
                    "updated_at = datetime('now') WHERE id = ?",
                    (
                        revision["id"] if revision["package_revision"] is not None else None,
                        asset_id,
                    ),
                )
            self._conn.execute(
                "UPDATE factor_source_revisions SET asset_id = ? WHERE id = ?",
                (asset_id, revision["id"]),
            )

    def copy_factor(
        self,
        project_id: str,
        factor_id: str,
        new_factor_id: str,
        *,
        expected_source_sha256: str | None = None,
    ) -> dict[str, Any]:
        row = self._editable_row(project_id)
        project_id = str(row["id"])
        if expected_source_sha256 and row["draft_source_sha256"] != expected_source_sha256:
            raise RuntimeError("draft changed since it was inspected")
        source_asset = self._conn.execute(
            "SELECT * FROM factor_assets WHERE project_id = ? AND path = ? "
            "AND current_revision_id IS NOT NULL",
            (project_id, f"factors/{factor_id}.py"),
        ).fetchone()
        if source_asset is None:
            raise KeyError(factor_id)
        original = self.get_factor_source(project_id, factor_id)["source"]
        copied_source = _copy_factor_source(original, factor_id, new_factor_id)
        units = self._source_unit_rows(project_id)
        strategy = next(item["source"] for item in units if item["kind"] == "strategy")
        factors = [item["source"] for item in units if item["kind"] == "factor"]
        project = self._commit_all_sources(
            project_id,
            strategy,
            [*factors, copied_source],
            expected_source_sha256=str(row["draft_source_sha256"]),
            copy={
                "path": f"factors/{new_factor_id}.py",
                "asset_id": source_asset["id"],
                "revision_id": source_asset["current_revision_id"],
            },
        )
        asset = self._conn.execute(
            "SELECT id FROM factor_assets WHERE project_id = ? AND path = ? "
            "AND current_revision_id IS NOT NULL",
            (project_id, f"factors/{new_factor_id}.py"),
        ).fetchone()
        return {"project": project, "asset": self.get_factor_asset(project_id, asset[0])}

    def _record_clone_origins(self, source_id: str, target_id: str) -> None:
        sources = {
            item["path"]: item
            for item in self.list_factor_assets(source_id)
            if item["current_revision_id"] is not None
        }
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                for target in self.list_factor_assets(target_id):
                    source = sources.get(target["path"])
                    if source is not None:
                        self._conn.execute(
                            "UPDATE factor_assets SET copied_from_asset_id = ?, "
                            "copied_from_revision_id = ? WHERE id = ?",
                            (source["id"], source["current_revision_id"], target["id"]),
                        )
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise


class _CopyFactorTransformer(cst.CSTTransformer):
    def __init__(self, old_id: str, new_id: str) -> None:
        self.old_id = old_id
        self.new_id = new_id

    def leave_FunctionDef(self, _original, updated):
        import ast

        decorators = []
        target = False
        factor_decorator = False
        explicit_id = False
        for decorator in updated.decorators:
            value = decorator.decorator
            if isinstance(value, cst.Name) and value.value == "factor":
                factor_decorator = True
            if isinstance(value, cst.Call) and isinstance(value.func, cst.Name) and value.func.value == "factor":
                factor_decorator = True
                args = []
                for arg in value.args:
                    if arg.keyword and arg.keyword.value == "id":
                        explicit_id = True
                        if (
                            isinstance(arg.value, cst.SimpleString)
                            and ast.literal_eval(arg.value.value) == self.old_id
                        ):
                            target = True
                            arg = arg.with_changes(value=cst.SimpleString(repr(self.new_id)))
                    args.append(arg)
                decorator = decorator.with_changes(decorator=value.with_changes(args=tuple(args)))
            decorators.append(decorator)
        if factor_decorator and not explicit_id and updated.name.value == self.old_id:
            target = True
        if not target:
            return updated
        return updated.with_changes(name=cst.Name(self.new_id), decorators=tuple(decorators))


def _copy_factor_source(source: str, old_id: str, new_id: str) -> str:
    if not new_id.isidentifier():
        raise ValueError("new factor ID must be a valid Python identifier")
    tree = cst.parse_module(source)
    return tree.visit(_CopyFactorTransformer(old_id, new_id)).code
