"""Read canonical source history without initialization, migration or execution."""

from __future__ import annotations

import difflib
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Literal

from alphalab.strategy.project_strategies import strategy_id as validate_strategy_id
from alphalab.strategy.repository import normalize_project_id

SourceKind = Literal['strategy', 'validation', 'recipe']


class HistoryUnavailableError(RuntimeError):
    """The canonical store is absent, incompatible, or temporarily unreadable."""


def source_changes(left, right, from_revision, to_revision):
    """Bound diff work/output and retain metadata for omitted/large units."""
    changes = []
    remaining = 100_000
    for path in sorted(left.keys() | right.keys()):
        old, new = left.get(path), right.get(path)
        if old and new and old['source'] == new['source']:
            continue
        old_source = old['source'] if old else ''
        new_source = new['source'] if new else ''
        omitted = max(len(old_source), len(new_source)) > 100_000 or remaining == 0
        lines = [] if omitted else difflib.unified_diff(
            old_source.splitlines(keepends=True), new_source.splitlines(keepends=True),
            fromfile=f'{from_revision}/{path}', tofile=f'{to_revision}/{path}',
        )
        # Keep missing final newlines visible rather than concatenating +/- lines.
        patch = ''.join(line if line.endswith('\n') else line + '\n\\ No newline at end of file\n' for line in lines)
        truncated = omitted or len(patch) > remaining
        patch = patch[:remaining]
        remaining -= len(patch)
        operation = 'added' if old is None else 'removed' if new is None else 'modified'
        changes.append({'path': path, 'kind': (new or old)['kind'], 'change': operation,
                        'summary': f'{operation.capitalize()} {path}',
                        'from_sha256': old['source_sha256'] if old else None,
                        'to_sha256': new['source_sha256'] if new else None,
                        'diff': patch, 'diff_truncated': truncated})
    return changes


class SourceHistory:
    """Internal read model. Each kind is read within its own canonical store."""

    def __init__(self, path: Path, kind: SourceKind = 'strategy'):
        if kind not in ('strategy', 'validation', 'recipe'):
            raise ValueError('unsupported source history kind')
        self.path, self.kind = Path(path), kind

    @contextmanager
    def _read(self, project_id: str, strategy_id: str):
        project_id = normalize_project_id(project_id)
        strategy_id = validate_strategy_id(strategy_id)
        connection = None
        try:
            connection = sqlite3.connect(self.path.resolve().as_uri() + '?mode=ro', uri=True, timeout=5)
            connection.row_factory = sqlite3.Row
            connection.execute('PRAGMA query_only = ON')
            connection.execute('BEGIN')
            if self.kind == 'recipe':
                exists = connection.execute('SELECT 1 FROM data_recipe_history WHERE project_id=? LIMIT 1', (project_id,)).fetchone()
            elif self.kind == 'validation':
                exists = connection.execute('SELECT 1 FROM strategy_projects WHERE id=?', (project_id,)).fetchone()
            else:
                exists = connection.execute(
                    'SELECT 1 FROM project_strategies WHERE project_id=? AND id=?', (project_id, strategy_id),
                ).fetchone()
            if exists is None:
                raise KeyError('source history owner not found')
            yield connection, project_id, strategy_id
        except sqlite3.Error as exc:
            raise HistoryUnavailableError('Source history is unavailable; no migration was attempted.') from exc
        finally:
            if connection is not None:
                connection.close()

    @property
    def _table(self):
        return 'validation_source_packages' if self.kind == 'validation' else 'data_recipe_history'

    def list(self, project_id: str, strategy_id: str = 'main', *, limit: int = 20, before: int | None = None):
        """Keyset pagination; strategy packages are not an edit-event timeline."""
        if not 1 <= limit <= 100 or (before is not None and before < 1):
            raise ValueError('invalid history pagination')
        with self._read(project_id, strategy_id) as (db, project, strategy):
            if self.kind == 'strategy':
                rows = db.execute(
                    '''SELECT p.revision, p.parent_revision, p.source_sha256, p.created_at
                       FROM strategy_source_packages p JOIN project_strategy_packages s
                       ON p.project_id=s.project_id AND p.revision=s.revision
                       WHERE s.project_id=? AND s.strategy_id=? AND (? IS NULL OR p.revision < ?)
                       ORDER BY p.revision DESC LIMIT ?''', (project, strategy, before, before, limit + 1),
                ).fetchall()
            else:
                extra = ', origin, selected_template_id' if self.kind == 'recipe' else ''
                rows = db.execute(
                    f'''SELECT revision, source_sha256, created_at{extra} FROM {self._table}
                        WHERE project_id=? AND (? IS NULL OR revision < ?) ORDER BY revision DESC LIMIT ?''',
                    (project, before, before, limit + 1),
                ).fetchall()
            items = [dict(row) for row in rows[:limit]]
            for item in items:
                item['summary'] = ('Existing draft preserved at migration; earlier edits unknown'
                                   if item.get('origin') == 'migration_baseline'
                                   else f'Saved {self.kind} source revision {item["revision"]}')
            return {'project_id': project, 'strategy_id': strategy if self.kind == 'strategy' else None,
                    'kind': self.kind, 'items': items,
                    'next_before': items[-1]['revision'] if len(rows) > limit else None,
                    'history_kind': 'unique_source_packages' if self.kind == 'strategy' else 'source_revisions'}

    def _units(self, db, project, strategy, revision):
        if self.kind != 'strategy':
            row = db.execute(f'SELECT * FROM {self._table} WHERE project_id=? AND revision=?', (project, revision)).fetchone()
            if row is None:
                raise KeyError('revision not found')
            return {f'{self.kind}.py': {**dict(row), 'kind': self.kind}}
        if db.execute(
            'SELECT 1 FROM project_strategy_packages WHERE project_id=? AND strategy_id=? AND revision=?',
            (project, strategy, revision),
        ).fetchone() is None:
            raise KeyError('revision not associated with selected strategy')
        rows = db.execute(
            'SELECT path, kind, source, source_sha256 FROM strategy_source_package_units WHERE project_id=? AND revision=? ORDER BY path',
            (project, revision),
        ).fetchall()
        if not rows:
            raise HistoryUnavailableError('Historical source units are unavailable; no reconstruction was attempted.')
        return {f'strategies/{strategy}.py' if r['kind'] == 'strategy' else r['path']: dict(r) for r in rows}

    def compare(self, project_id: str, strategy_id: str, from_revision: int, to_revision: int):
        """Compare immutable units and report conservative package-scope impact."""
        if min(from_revision, to_revision) < 1:
            raise ValueError('revision must be positive')
        with self._read(project_id, strategy_id) as (db, project, strategy):
            left = self._units(db, project, strategy, from_revision)
            right = self._units(db, project, strategy, to_revision)
            changes = source_changes(left, right, from_revision, to_revision)
            associations, affected = {}, []
            if self.kind == 'strategy':
                for revision in (from_revision, to_revision):
                    associations[str(revision)] = [r['strategy_id'] for r in db.execute(
                        'SELECT strategy_id FROM project_strategy_packages WHERE project_id=? AND revision=? ORDER BY strategy_id',
                        (project, revision),
                    )]
                shared = any(c['kind'] == 'factor' for c in changes)
                if changes:
                    # Every active strategy assembles the shared factor library. This
                    # is current package scope, not a claim about a past edit event or
                    # signal-level use of a particular factor.
                    affected = [dict(r) for r in db.execute(
                        '''SELECT id, name, current_revision FROM project_strategies
                           WHERE project_id=? AND archived=0 AND (? OR id=?) ORDER BY id''',
                        (project, shared, strategy),
                    )]
            metadata_changes = []
            if self.kind == 'recipe':
                old_template = left['recipe.py']['selected_template_id']
                new_template = right['recipe.py']['selected_template_id']
                if old_template != new_template:
                    metadata_changes.append({'field': 'selected_template_id', 'before': old_template, 'after': new_template})
            return {'project_id': project, 'strategy_id': strategy if self.kind == 'strategy' else None,
                    'kind': self.kind, 'from_revision': from_revision, 'to_revision': to_revision,
                    'changes': changes, 'metadata_changes': metadata_changes,
                    'summary': f'{len(changes)} source unit(s) changed; {len(metadata_changes)} metadata field(s) changed',
                    'package_strategy_associations': associations, 'affected_strategies': affected,
                    'impact_basis': 'current_assembly_scope' if self.kind == 'strategy' else 'project_source',
                    'usage_analysis': 'not_evaluated',
                    'diff_truncated': any(item['diff_truncated'] for item in changes)}
