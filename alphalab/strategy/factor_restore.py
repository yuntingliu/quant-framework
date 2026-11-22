"""Factor revisions, scoped to one uninterrupted lifetime of a project path."""

import hashlib
import json


def revisions(db, project, path, before=None):
    return [dict(row) for row in db.execute(
        """SELECT id, package_revision, restored_from FROM factor_source_revisions
           WHERE project_id=? AND path=? AND id > (
             SELECT COALESCE(MAX(id),0) FROM factor_source_revisions
             WHERE project_id=? AND path=? AND package_revision IS NULL)
           AND (? IS NULL OR id < ?) ORDER BY id DESC LIMIT 50""",
        (project, path, project, path, before, before),
    )]


def restore_state(db, project, strategy, path, revision):
    target = db.execute(
        """SELECT u.* FROM factor_source_revisions r JOIN strategy_source_package_units u
           ON u.project_id=r.project_id AND u.path=r.path AND u.revision=r.package_revision
           WHERE r.id=? AND r.project_id=? AND r.path=? AND u.kind='factor'
           AND NOT EXISTS (SELECT 1 FROM factor_source_revisions d WHERE d.project_id=r.project_id
               AND d.path=r.path AND d.id>r.id AND d.package_revision IS NULL)""",
        (revision, project, path),
    ).fetchone()
    current = db.execute(
        "SELECT * FROM strategy_source_units WHERE project_id=? AND path=? AND kind='factor'",
        (project, path),
    ).fetchone()
    strategies = [dict(row) for row in db.execute(
        "SELECT id,name,current_revision FROM project_strategies WHERE project_id=? AND archived=0 ORDER BY id",
        (project,),
    )]
    if not target or not current or strategy not in {item['id'] for item in strategies}:
        raise KeyError("factor revision or active strategy unavailable")
    latest = db.execute("SELECT MAX(id) FROM factor_source_revisions WHERE project_id=?", (project,)).fetchone()[0]
    generation = db.execute("SELECT generation FROM project_source_generation WHERE project_id=?", (project,)).fetchone()
    token = hashlib.sha256(json.dumps([project, strategy, path, revision, latest, generation[0] if generation else 0, strategies,
                                       current['source_sha256'], target['source_sha256']]).encode()).hexdigest()
    return current, target, strategies, token


def preview(db, project, strategy, path, revision):
    from alphalab.strategy.history import source_changes

    current, target, strategies, token = restore_state(db, project, strategy, path, revision)
    return {"token": token, "affected_strategies": strategies, "changes": source_changes(
        {path: current}, {path: target}, "current", revision)}


def record_changes(db, project, previous, current, package, restore):
    for path in sorted(previous.keys() | current.keys()):
        restoring = restore is not None and path == restore['path']
        if previous.get(path) == current.get(path) and not restoring:
            continue
        info = restore if restoring else {}
        db.execute(
            """INSERT INTO factor_source_revisions(project_id,path,package_revision,restored_from,
               request_id,preview_token,strategy_id,affected_json) VALUES (?,?,?,?,?,?,?,?)""",
            (project, path, package if path in current else None, info.get('revision'),
             info.get('request_id'), info.get('token'), info.get('strategy'), info.get('affected_json')),
        )


def receipt(db, project, restore):
    row = db.execute("SELECT * FROM factor_source_revisions WHERE project_id=? AND request_id=?",
                     (project, restore['request_id'])).fetchone()
    if row is None:
        return None
    if (row['path'], row['restored_from'], row['preview_token'], row['strategy_id']) != (
        restore['path'], restore['revision'], restore['token'], restore['strategy']
    ):
        raise ValueError("restore request ID already used for a different request")
    return {"revision": row['id'], "restored_from": row['restored_from'],
            "packages": json.loads(row['affected_json'])}
