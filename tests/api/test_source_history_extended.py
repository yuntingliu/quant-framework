"""All source owners, recipe migration/transactions, identities and impact."""

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from alphalab import StrategyRepository
from alphalab.dataio.runtime import OperationsStore
from alphalab.strategy.factor_templates import install_factor_template
from alphalab.strategy.history import SourceHistory
from alphalab.validation.repository import ValidationRepository
from apps.api.routers import strategy


@pytest.fixture
def stores(tmp_path, monkeypatch):
    path = tmp_path / 'app' / 'alphalab.db'
    repo = StrategyRepository(path)
    repo.clone_project('sdk-v1-default', 'research')
    repo.clone_project('sdk-v1-default', 'unrelated')
    repo.create_strategy('research', 'second', name='Same display name')
    validation = ValidationRepository(path)
    validation.get_or_create('research')
    operations = OperationsStore(tmp_path)
    monkeypatch.setattr(strategy, 'source_history', lambda kind='strategy': SourceHistory(
        operations.path if kind == 'recipe' else path, kind,
    ))
    app = FastAPI()
    app.include_router(strategy.router)
    yield repo, validation, operations, TestClient(app)
    validation.close()
    repo.close()


def compare(client, kind, old, new, **params):
    return client.get('/api/strategy/projects/research/source-comparison', params={
        'kind': kind, 'from_revision': old, 'to_revision': new, **params,
    })


def test_validation_versions_are_project_scoped_and_read_only(stores):
    repo, validation, _, client = stores
    initial = validation.get_package('research')
    updated = validation.update_source('research', initial['source'] + '\n# reviewed\n')
    before = repo.path.read_bytes()
    page = client.get('/api/strategy/projects/research/source-history', params={'kind': 'validation', 'limit': 1}).json()
    assert page['items'][0]['revision'] == updated['current_revision']
    assert page['next_before'] == updated['current_revision']
    response = compare(client, 'validation', initial['revision'], updated['current_revision'])
    assert response.status_code == 200
    assert response.json()['changes'][0]['path'] == 'validation.py'
    assert '+# reviewed' in response.json()['changes'][0]['diff']
    assert response.json()['strategy_id'] is None
    assert compare(client, 'validation', 1, 999).status_code == 404
    assert repo.path.read_bytes() == before
    assert client.get('/api/strategy/projects/research/source-history', params={'kind': 'invalid'}).status_code == 422


def test_recipe_history_save_metadata_revert_pagination_and_no_execution(stores):
    _, _, ops, client = stores
    first = ops.save_recipe_draft('research', 'raise RuntimeError("never execute history")\n', selected_template_id='a')
    # Identical saves are no-ops for history; metadata changes are visible.
    ops.save_recipe_draft('research', first['source'], selected_template_id='a')
    ops.save_recipe_draft('research', first['source'], selected_template_id='b')
    ops.save_recipe_draft('research', '# different\n')
    ops.save_recipe_draft('research', first['source'])
    before = ops.path.read_bytes()
    page = client.get('/api/strategy/projects/research/source-history', params={'kind': 'recipe', 'limit': 2}).json()
    assert [row['revision'] for row in page['items']] == [4, 3]
    older = client.get('/api/strategy/projects/research/source-history', params={'kind': 'recipe', 'before': page['next_before']}).json()
    assert [row['revision'] for row in older['items']] == [2, 1]
    assert older['next_before'] is None
    response = compare(client, 'recipe', 1, 2).json()
    assert response['changes'] == []
    assert response['metadata_changes'] == [{'field': 'selected_template_id', 'before': 'a', 'after': 'b'}]
    response = compare(client, 'recipe', 2, 3).json()
    assert response['changes'][0]['path'] == 'recipe.py'
    assert response['changes'][0]['summary'] == 'Modified recipe.py'
    assert compare(client, 'recipe', 1, 99).status_code == 404
    assert ops.path.read_bytes() == before


def test_recipe_migration_only_preserves_existing_draft_once(tmp_path):
    ops = OperationsStore(tmp_path)
    draft = ops.save_recipe_draft('research', '# already exists\n')
    with sqlite3.connect(ops.path) as db:
        db.execute('DROP TABLE data_recipe_history')
    ops = OperationsStore(tmp_path)
    with sqlite3.connect(ops.path) as db:
        assert db.execute('SELECT revision, source, origin FROM data_recipe_history').fetchall() == [
            (1, draft['source'], 'migration_baseline'),
        ]
    OperationsStore(tmp_path)
    assert ops.get_recipe_draft('research') == draft
    assert len(SourceHistory(ops.path, 'recipe').list('research')['items']) == 1
    ops.save_recipe_draft('research', '# new\n', expected_source_sha256=draft['source_sha256'])
    assert len(SourceHistory(ops.path, 'recipe').list('research')['items']) == 2


def test_recipe_failure_rolls_back_draft_and_history(tmp_path):
    ops = OperationsStore(tmp_path)
    first = ops.save_recipe_draft('research', '# original\n')
    with sqlite3.connect(ops.path) as db:
        db.execute("CREATE TRIGGER reject_draft BEFORE UPDATE ON data_recipe_drafts BEGIN SELECT RAISE(ABORT, 'injected'); END")
    with pytest.raises(sqlite3.IntegrityError, match='injected'):
        ops.save_recipe_draft('research', '# must roll back\n')
    assert ops.get_recipe_draft('research') == first
    assert len(SourceHistory(ops.path, 'recipe').list('research')['items']) == 1


def test_recipe_concurrent_expected_hash_has_one_winner(tmp_path):
    ops = OperationsStore(tmp_path)
    first = ops.save_recipe_draft('research', '# initial\n')
    other = OperationsStore(tmp_path)
    barrier = Barrier(2)

    def save(store, text):
        barrier.wait(timeout=5)
        try:
            store.save_recipe_draft('research', text, expected_source_sha256=first['source_sha256'])
            return 'saved'
        except RuntimeError as exc:
            assert 'changed since' in str(exc)
            return 'conflict'

    with ThreadPoolExecutor(2) as pool:
        a = pool.submit(save, ops, '# a\n')
        b = pool.submit(save, other, '# b\n')
        assert sorted([a.result(), b.result()]) == ['conflict', 'saved']
    assert len(SourceHistory(ops.path, 'recipe').list('research')['items']) == 2


def test_shared_factor_impact_copy_rename_and_frozen_units(stores):
    repo, _, _, client = stores
    repo.select_strategy('main')
    original = repo.get_package('research')
    # Renames remain explicit removed/added paths, without guessed identity links.
    installed = repo.add_factor_source('research', '@factor(id="extra")\ndef extra(context):\n    return context.history("close", window=1).iloc[-1]\n')
    added = compare(client, 'strategy', original['revision'], installed['current_revision']).json()
    assert {r['id'] for r in added['affected_strategies']} == {'main', 'second'}
    assert added['impact_basis'] == 'current_assembly_scope'
    assert added['usage_analysis'] == 'not_evaluated'
    assert added['changes'][0]['change'] == 'added'
    renamed = repo.replace_factor_source('research', 'extra', '@factor(id="renamed")\ndef extra(context):\n    return context.history("close", window=1).iloc[-1]\n')
    changes = compare(client, 'strategy', installed['current_revision'], renamed['current_revision']).json()['changes']
    assert {(row['path'], row['change']) for row in changes} == {('factors/extra.py', 'removed'), ('factors/renamed.py', 'added')}
    repo.rename_strategy('research', name='Same display name')
    assert repo.get_package('research', original['revision']) == original
    # Equal labels do not erase stable strategy IDs.
    assert {r['id'] for r in compare(client, 'strategy', original['revision'], renamed['current_revision']).json()['affected_strategies']} == {'main', 'second'}


def test_repeated_template_copies_have_distinct_history_paths(stores):
    repo, _, _, client = stores
    repo.select_strategy('main')
    original = repo.get_package('research')
    for _ in range(2):
        source, _ = install_factor_template(repo.get_project('research')['draft_source'], template_id='momentum_20d')
        latest = repo.update_draft('research', source)
    response = compare(client, 'strategy', original['revision'], latest['current_revision'])
    assert response.status_code == 200
    changes = response.json()['changes']
    copies = [c for c in changes if c['kind'] == 'factor']
    assert {(c['path'], c['change']) for c in copies} == {
        ('factors/momentum_20d_2.py', 'added'), ('factors/momentum_20d_3.py', 'added'),
    }
    assert repo.get_package('research', original['revision']) == original


def test_recipe_read_missing_schema_does_not_seed_migration(tmp_path):
    ops = OperationsStore(tmp_path)
    ops.save_recipe_draft('research', '# legacy')
    with sqlite3.connect(ops.path) as db:
        db.execute('DROP TABLE data_recipe_history')
    before = ops.path.read_bytes()
    from alphalab.strategy.history import HistoryUnavailableError
    with pytest.raises(HistoryUnavailableError):
        SourceHistory(ops.path, 'recipe').list('research')
    assert ops.path.read_bytes() == before
