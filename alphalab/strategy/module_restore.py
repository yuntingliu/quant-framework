"""Revision selection and transactional restore of a single strategy module."""

import hashlib
import json

from alphalab.strategy.source import assemble_strategy_source


def revisions(db, project, strategy, before):
    return [dict(row) for row in db.execute(
        """SELECT id,package_revision,restored_from FROM strategy_module_revisions
           WHERE project_id=? AND strategy_id=? AND (? IS NULL OR id<?)
           ORDER BY id DESC LIMIT 50""", (project, strategy, before, before),
    )]


def _state(db, project, strategy, revision):
    row = db.execute(
        """SELECT s.name,s.source,s.current_revision,u.source AS target,u.source_sha256,
                  COALESCE(g.generation,0) AS generation
           FROM strategy_module_revisions r JOIN project_strategies s
             ON s.project_id=r.project_id AND s.id=r.strategy_id AND s.archived=0
           JOIN strategy_source_package_units u ON u.project_id=r.project_id
             AND u.revision=r.package_revision AND u.kind='strategy' AND u.path='strategy.py'
           LEFT JOIN project_source_generation g ON g.project_id=r.project_id
           WHERE r.id=? AND r.project_id=? AND r.strategy_id=?""", (revision, project, strategy),
    ).fetchone()
    if row is None:
        raise KeyError("strategy revision unavailable")
    if _digest(row["target"]) != row["source_sha256"]:
        raise ValueError("historical strategy source checksum mismatch")
    token = _digest(json.dumps([project, strategy, revision, row["current_revision"],
                                row["generation"], _digest(row["source"]), row["source_sha256"]]))
    return row, token


def _digest(source):
    return hashlib.sha256(source.encode()).hexdigest()


def preview(db, project, strategy, revision):
    from alphalab.strategy.history import source_changes

    row, token = _state(db, project, strategy, revision)
    path = f"strategies/{strategy}.py"
    current = {path: dict(kind="strategy", source=row["source"], source_sha256=_digest(row["source"]))}
    target = {path: dict(kind="strategy", source=row["target"], source_sha256=row["source_sha256"])}
    return {"token": token, "affected_strategies": [{"id": strategy, "name": row["name"]}],
            "changes": source_changes(current, target, "current", revision)}


def _receipt(db, project, strategy, request_id, revision, token):
    row = db.execute(
        """SELECT id,package_revision,restored_from,preview_token FROM strategy_module_revisions
           WHERE project_id=? AND strategy_id=? AND request_id=?""", (project, strategy, request_id),
    ).fetchone()
    if row is None:
        return None
    if (row["restored_from"], row["preview_token"]) != (revision, token):
        raise RuntimeError("request changed since first restore")
    return {"revision": row["id"], "restored_from": revision, "packages": {strategy: row["package_revision"]}}


def restore(repo, project, revision, token, request_id):
    db, strategy = repo._conn, repo._strategy_id
    with repo._lock, db:
        db.execute("BEGIN")
        project = repo._editable_row(project)["id"]
        replay = _receipt(db, project, strategy, request_id, revision, token)
        if replay:
            return replay
        row, expected = _state(db, project, strategy, revision)
        if token != expected:
            raise RuntimeError("project changed since restore preview")
        if row["source"] == row["target"]:
            raise ValueError("selected strategy already matches this revision")
        factors = [u["source"] for u in repo._source_unit_rows(project) if u["kind"] == "factor"]

    bundled, inspection = assemble_strategy_source(row["target"], factors)
    repo._probe_source(bundled, inspection)

    with repo._lock, db:
        db.execute("BEGIN IMMEDIATE")
        replay = _receipt(db, project, strategy, request_id, revision, token)
        if replay:
            return replay
        _, expected = _state(db, project, strategy, revision)
        if token != expected:
            raise RuntimeError("project changed since restore preview")
        package = repo._save_strategy_package(project, strategy, row["target"], row["current_revision"], bundled, inspection)
        if strategy == "main":
            changed = db.execute(
                """UPDATE strategy_source_units SET source=?,source_sha256=?,updated_at=datetime('now')
                   WHERE project_id=? AND kind='strategy' AND path='strategy.py'""",
                (row["target"], row["source_sha256"], project))
            if changed.rowcount != 1:
                raise ValueError("current main strategy source unit unavailable")
        db.execute(
            """INSERT INTO strategy_module_revisions(project_id,strategy_id,package_revision,
               restored_from,previous_package,request_id,preview_token) VALUES (?,?,?,?,?,?,?)""",
            (project, strategy, package, revision, row["current_revision"], request_id, token))
        repo._advance_source_generation(project)
        return _receipt(db, project, strategy, request_id, revision, token)
