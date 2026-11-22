"""History API is read-only, owner-scoped, bounded, and never executes source."""

import sqlite3

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from alphalab import StrategyRepository
from alphalab.strategy.history import SourceHistory
from apps.api.routers import strategy


@pytest.fixture
def history(tmp_path, monkeypatch):
    path = tmp_path / 'research.db'
    repo = StrategyRepository(path)
    original = repo.clone_project('sdk-v1-default', 'research')
    first = original['current_revision']
    repo.create_strategy('research', 'copy', name='Copy')
    updated = repo.update_strategy_source(
        'research', original['strategy_source'].replace('top_n: int = 10', 'top_n: int = 3'),
    )
    second = updated['current_revision']
    repo.close()
    monkeypatch.setattr(strategy, 'source_history', lambda kind="strategy": SourceHistory(path, kind))
    app = FastAPI()
    app.include_router(strategy.router)
    return path, TestClient(app), first, second


def test_history_pagination_and_selected_strategy(history):
    _, client, first, second = history
    url = '/api/strategy/projects/research/source-history'
    page = client.get(url, params={'strategy_id': 'copy', 'limit': 1}).json()
    assert [r['revision'] for r in page['items']] == [second]
    assert page['next_before'] == second
    older = client.get(url, params={'strategy_id': 'copy', 'before': second}).json()
    assert [r['revision'] for r in older['items']] == [first]
    assert older['next_before'] is None
    assert [r['revision'] for r in client.get(url).json()['items']] == [first]
    assert client.get(url, params={'limit': 101}).status_code == 422
    assert client.get(url, params={'before': 0}).status_code == 422
    assert client.get(url, params={'strategy_id': 'missing'}).status_code == 404
    assert client.get(url.replace('research', 'missing')).status_code == 404


def test_comparison_identity_scope_and_read_only(history, monkeypatch):
    path, client, first, second = history
    before = path.read_bytes()

    def forbidden(*args, **kwargs):
        raise AssertionError('History must not initialize the mutable repository')

    monkeypatch.setattr(StrategyRepository, '__init__', forbidden)
    url = '/api/strategy/projects/research/source-comparison'
    params = {'strategy_id': 'copy', 'from_revision': first, 'to_revision': second}
    response = client.get(url, params=params)
    assert response.status_code == 200, response.text
    data = response.json()
    assert len(data['changes']) == 1
    change = data['changes'][0]
    assert change['path'] == 'strategies/copy.py'
    assert change['change'] == 'modified'
    assert any(line.startswith('-') and 'top_n: int = 10' in line for line in change['diff'].splitlines())
    assert any(line.startswith('+') and 'top_n: int = 3' in line for line in change['diff'].splitlines())
    assert data['usage_analysis'] == 'not_evaluated'
    assert data['package_strategy_associations'][str(first)] == ['copy', 'main']
    assert client.get(url, params={**params, 'strategy_id': 'main'}).status_code == 404
    assert client.get(url, params={**params, 'to_revision': 99999}).status_code == 404
    same = client.get(url, params={**params, 'to_revision': first}).json()
    assert same['changes'] == []
    reverse = client.get(url, params={**params, 'from_revision': second, 'to_revision': first}).json()
    assert any(line.startswith('+') and 'top_n: int = 10' in line for line in reverse['changes'][0]['diff'].splitlines())
    assert client.get('/api/strategy/projects/research/source-history').status_code == 200
    assert path.read_bytes() == before


def test_missing_database_does_not_create_or_leak_path(tmp_path, monkeypatch):
    path = tmp_path / 'private' / 'absent.db'
    monkeypatch.setattr(strategy, 'source_history', lambda kind="strategy": SourceHistory(path, kind))
    app = FastAPI()
    app.include_router(strategy.router)
    response = TestClient(app).get('/api/strategy/projects/research/source-history')
    assert response.status_code == 503
    assert str(tmp_path) not in response.text
    assert not path.parent.exists()


def test_legacy_missing_units_not_silently_reconstructed(history):
    path, client, first, second = history
    with sqlite3.connect(path) as db:
        db.execute('DELETE FROM strategy_source_package_units WHERE project_id=? AND revision=?', ('research', first))
    before = path.read_bytes()
    response = client.get('/api/strategy/projects/research/source-comparison', params={
        'strategy_id': 'copy', 'from_revision': first, 'to_revision': second,
    })
    assert response.status_code == 503
    assert path.read_bytes() == before


def test_source_is_text_only_and_large_diff_is_explicit(history, tmp_path):
    path, client, first, second = history
    marker = tmp_path / 'executed'
    source = f'open({str(marker)!r}, "w").write("bad")\n' + ('# large\n' * 15000)
    with sqlite3.connect(path) as db:
        db.execute('UPDATE strategy_source_package_units SET source=? WHERE project_id=? AND revision=? AND kind=?',
                   (source, 'research', second, 'strategy'))
    response = client.get('/api/strategy/projects/research/source-comparison', params={
        'strategy_id': 'copy', 'from_revision': first, 'to_revision': second,
    })
    assert response.status_code == 200
    assert response.json()['diff_truncated'] is True
    change = response.json()['changes'][0]
    assert change['diff'] == ''
    assert 'before_source' not in change and 'after_source' not in change
    assert not marker.exists()


def test_shared_factor_history_preserves_both_strategy_versions(history):
    path, client, first, second = history
    repo = StrategyRepository(path)
    factor = repo.get_factor_source('research', 'momentum_20d')
    result = repo.replace_factor_source('research', 'momentum_20d', factor['source'].replace(' - 1.0', ' - 0.9'))
    assert set(result['affected_strategies']) == {'main', 'copy'}
    revisions = {r['id']: r['current_revision'] for r in result['strategies']}
    repo.close()
    before = path.read_bytes()
    for selected, old in [('main', first), ('copy', second)]:
        response = client.get('/api/strategy/projects/research/source-comparison', params={
            'strategy_id': selected, 'from_revision': old, 'to_revision': revisions[selected],
        })
        assert response.status_code == 200
        changes = response.json()['changes']
        assert len(changes) == 1
        assert changes[0]['path'] == 'factors/momentum_20d.py'
        assert changes[0]['kind'] == 'factor'
    assert path.read_bytes() == before


def test_uninitialized_schema_is_not_migrated(tmp_path, monkeypatch):
    path = tmp_path / 'legacy.db'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE old_state (value TEXT)')
    before = path.read_bytes()
    monkeypatch.setattr(strategy, 'source_history', lambda kind="strategy": SourceHistory(path, kind))
    app = FastAPI()
    app.include_router(strategy.router)
    assert TestClient(app).get('/api/strategy/projects/research/source-history').status_code == 503
    assert path.read_bytes() == before
