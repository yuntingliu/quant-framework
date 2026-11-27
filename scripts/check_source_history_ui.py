"""Exercise the inline workbench views against an isolated real API.

Requires Playwright + Chromium, installed frontend dependencies and a Python
interpreter with AlphaLab's app/dev dependencies. Never opens a user's store.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import signal
import socket
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path
from uuid import uuid4

SEED = '''
from alphalab import StrategyRepository
from alphalab.validation.repository import ValidationRepository
from alphalab.dataio.runtime import OperationsStore
from alphalab.dataio.recipes import render_builtin_recipe
repo = StrategyRepository()
repo.clone_project('sdk-v1-default', 'history-demo')
repo.create_strategy('history-demo', 'copy', name='Comparison strategy')
repo.select_strategy('main')
factor = repo.get_factor_source('history-demo', 'momentum_20d')
repo.replace_factor_source('history-demo', 'momentum_20d', factor['source'].replace('] = 20,', '] = 60,'))
copy = repo.select_strategy('copy').get_project('history-demo')
repo.update_strategy_source('history-demo', copy['strategy_source'].replace('top_n: int = 10', 'top_n: int = 3'))
package = repo.select_strategy('main').get_package('history-demo')
repo.record_factor_evidence('history-demo', 'momentum_20d', 'main', {
    'project_id': 'history-demo', 'revision': package['revision'],
    'source_sha256': package['source_sha256'], 'status': 'insufficient',
    'summary': {'observations': 3, 'mean_rank_ic': 0.02}, 'rows': [],
}, {'start_date': '2024-01-01', 'end_date': '2025-01-01', 'parameters': {'window': 20}},
    {'universe': 'sdk-v1-default', 'start_date': '2024-01-01', 'end_date': '2025-01-01'},
    {'forward_return': 'next_open_to_open', 'missing': 'not_zero_filled'},
    {'scope': 'strategy_package'})
repo.close()
validation = ValidationRepository()
initial = validation.get_or_create('history-demo')
validation.update_source('history-demo', initial['source'] + '\\n# verified validation change\\n')
validation.close()
ops = OperationsStore()
for i in range(24):
    recipe = render_builtin_recipe('rq.etf_daily', start='2021-08-25', end='2026-08-25')
    suffix = '' if i < 2 else f'\\n# recipe {i}\\n'
    template = 'rq.etf_daily' if i % 2 == 0 else 'rq.a_share_daily'
    ops.save_recipe_draft('history-demo', recipe + suffix, selected_template_id=template)
'''
HARNESS = '''
import React, { useRef, useState } from 'react'
import { createRoot } from 'react-dom/client'
import { SourceReviewWorkspace } from '/src/components/shared/SourceReviewWorkspace'
import { ThemeProvider } from '/src/contexts/ThemeContext'
import { LanguageProvider } from '/src/contexts/LanguageContext'
import { ConfirmProvider } from '/src/hooks/useConfirm'
import '/src/index.css'
export function Demo() {
  const [kind, setKind] = useState('factor')
  const [text, setText] = useState('unsaved editor buffer')
  const [pending, setPending] = useState(false)
  const [refreshRequired, setRefreshRequired] = useState(false)
  const [ran, setRan] = useState(false)
  const refreshAttempts = useRef(0)
  return <main style={{height: "100dvh", padding: 12, display: "flex", flexDirection: "column"}}>
    <h1>Source history — isolated component acceptance</h1>
    <label>Source type<select aria-label="Source type" value={kind} onChange={e => setKind(e.target.value)}>
      <option value="strategy">Strategy</option><option value="factor">Factors</option><option value="validation">Validation</option><option value="recipe">Recipe</option>
    </select></label>
    <SourceReviewWorkspace projectId="history-demo" strategyId={kind === 'strategy' ? 'copy' : 'main'}
      kind={kind === 'factor' ? 'strategy' : kind} dirty={Boolean(text) || pending || refreshRequired}
      restore={{ kind, factor: kind === 'factor' ? 'momentum_20d' : undefined,
        blocked: Boolean(text) || pending || refreshRequired,
        refresh: async () => { if (kind === 'validation' && refreshAttempts.current++ === 0) throw new Error('refresh unavailable') },
        onRefreshError: () => setRefreshRequired(true), onRefreshSuccess: () => setRefreshRequired(false) }}>
    <textarea aria-label="Unsaved editor" value={text} onChange={e => setText(e.target.value)} />
    <label>Pending visual edits<input type="checkbox" checked={pending} onChange={e => setPending(e.target.checked)} /></label>
    <button type="button" disabled={Boolean(text) || pending || refreshRequired} onClick={() => setRan(true)}>Run saved source</button>
    {refreshRequired && <span>Refresh required before running</span>}{ran && <span>Started</span>}
    </SourceReviewWorkspace>
  </main>
}
createRoot(document.getElementById('root')).render(<LanguageProvider><ThemeProvider><ConfirmProvider><Demo /></ConfirmProvider></ThemeProvider></LanguageProvider>)
'''


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def ready(url, process):
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError('Test server exited; inspect its log')
        try:
            with urllib.request.urlopen(url, timeout=1):
                return
        except OSError:
            time.sleep(0.1)
    raise TimeoutError('Test server did not become ready')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--python', required=True, help='Python with AlphaLab app/dev dependencies')
    parser.add_argument('--chromium', help='Optional path to an installed Chromium executable')
    parser.add_argument('--output', type=Path, help='Optional local screenshot directory')
    parser.add_argument('--serve', action='store_true', help='Keep the interactive preview running until interrupted')
    args = parser.parse_args()
    if not args.serve:
        from playwright.sync_api import expect, sync_playwright

    root = Path(__file__).resolve().parents[1]
    desktop = root / 'apps' / 'desktop'
    name = f'history-check-{uuid4().hex}'
    html, tsx = desktop / f'{name}.html', desktop / f'{name}.tsx'
    processes = []
    try:
        with tempfile.TemporaryDirectory(prefix='history-ui-') as temp:
            state = Path(temp)
            env = {**os.environ, 'PYTHONPATH': str(root), 'ALPHALAB_RUNTIME_DIR': str(state / 'runtime'),
                   'ALPHALAB_APP_DATA_DIR': str(state / 'app'), 'ALPHALAB_CACHE_DIR': str(state / 'cache')}
            seed = SEED
            if not args.serve:
                # A real saved module with many changed lines and one unbroken long line.
                seed += """
repo = StrategyRepository().select_strategy('copy')
code = repo.get_project('history-demo')['strategy_source']
repo.update_strategy_source('history-demo', code + '\\n' + '\\n'.join(f'# review line {i}' for i in range(160))
                           + '\\n# ' + 'x' * 900 + '\\n# end-of-review\\n')
repo.close()
"""
            subprocess.run([args.python, '-c', seed], cwd=root, env=env, check=True)
            databases = list((state / 'runtime' / 'app').glob('*.db'))
            assert {p.name for p in databases} >= {'alphalab.db', 'dataio.db'}, 'Missing isolated stores'
            before = {p: p.read_bytes() for p in databases}
            api_port, web_port = free_port(), free_port()
            api_url = f'http://127.0.0.1:{api_port}'
            with (state / 'servers.log').open('w+') as log:
                code = "from fastapi import FastAPI; from apps.api.routers.strategy import router; from apps.api.routers.validation import router as validation_router; from apps.api.routers.data_sync import router as data_router; from apps.api.routers.backtests import router as backtests_router; import uvicorn; app=FastAPI(); app.include_router(router); app.include_router(validation_router); app.include_router(data_router); app.include_router(backtests_router); uvicorn.run(app,host='127.0.0.1',port=" + str(api_port) + ")"
                processes.append(subprocess.Popen([args.python, '-c', code], cwd=root, env=env, stdout=log, stderr=log))
                ready(api_url + '/openapi.json', processes[-1])
                html.write_text(f'<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>源码比较 · AlphaLab</title></head><body><div id="root"></div><script type="module" src="/{name}.tsx"></script></body></html>')
                tsx.write_text((root / 'scripts/fixtures/source-history-preview.tsx').read_text() if args.serve else HARNESS)
                if args.serve:
                    html.write_text(html.read_text().replace('<head>', '<head><script>localStorage.setItem("alphalab-language","zh");localStorage.setItem("alphalab-language-default-version","3");</script>'))
                processes.append(subprocess.Popen(
                    ['node', 'node_modules/vite/bin/vite.js', '--port', str(web_port)], cwd=desktop,
                    env={**env, 'WEB_ONLY': '1', 'ALPHALAB_API_ORIGIN': api_url}, stdout=log, stderr=log,
                ))
                url = f'http://127.0.0.1:{web_port}/{name}.html'
                ready(url, processes[-1])
                if args.serve:
                    def stop_preview(_signum, _frame):
                        raise SystemExit(0)
                    signal.signal(signal.SIGTERM, stop_preview)
                    signal.signal(signal.SIGINT, stop_preview)
                    print(f'Interactive preview: {url}', flush=True)
                    print('Example data only. Stop with Ctrl+C.', flush=True)
                    while True:
                        time.sleep(1)
                with sync_playwright() as browser_api:
                    browser = browser_api.chromium.launch(headless=True, executable_path=args.chromium)
                    page = browser.new_page(viewport={'width': 1280, 'height': 900})
                    errors, mutations = [], []
                    page.on('pageerror', lambda error: errors.append(str(error)))
                    page.on('request', lambda request: mutations.append(request.url) if '/api/' in request.url and request.method != 'GET' else None)
                    page.goto(url)
                    page.get_by_label('Unsaved editor').fill('do not overwrite me')
                    opener = page.get_by_role('button', name='Source history & comparison', exact=True)
                    opener.click()
                    panel = page.get_by_role('region', name='Source diff', exact=True)
                    editor = page.get_by_role('button', name='Workbench', exact=True)
                    expect(page.get_by_role('dialog')).to_have_count(0)
                    expect(panel.get_by_label('From revision')).to_have_value('1')
                    panel.get_by_role('button', name='Compare versions', exact=True).click()
                    expect(panel.get_by_role('heading', name='modified · factors/momentum_20d.py')).to_be_visible()
                    diff = panel.locator('.monaco-diff-editor')
                    expect(diff.locator('.char-insert').first).to_be_visible(timeout=45000)
                    expect(diff.locator('.char-delete').first).to_be_visible()
                    expect(panel.locator('.sh-impact li').filter(has_text='Comparison strategy').filter(has_text='copy')).to_be_visible()
                    if args.output:
                        args.output.mkdir(parents=True, exist_ok=True)
                        page.screenshot(path=str(args.output / 'source-history-strategy.png'))
                    # Identical versions produce no changes, without stale previous output.
                    target = panel.get_by_label('To revision').input_value()
                    panel.get_by_label('From revision').select_option(target)
                    expect(panel.get_by_role('region', name='Comparison results')).to_have_count(0)
                    panel.get_by_role('button', name='Compare versions', exact=True).click()
                    expect(panel.get_by_text('These versions are identical')).to_be_visible()
                    editor.click()
                    expect(page.get_by_label('Unsaved editor')).to_have_value('do not overwrite me')
                    for kind, path in [('validation', 'validation.py'), ('recipe', 'recipe.py')]:
                        page.get_by_label('Source type').select_option(kind)
                        opener.click()
                        expect(panel.get_by_label('To revision')).not_to_have_value('')
                        if kind == 'recipe':
                            panel.get_by_role('button', name='Load earlier versions').click()
                            expect(panel.get_by_label('From revision').locator('option')).to_have_count(24)
                        panel.get_by_role('button', name='Compare versions', exact=True).click()
                        expect(panel.get_by_role('heading', name=f'modified · {path}')).to_be_visible()
                        editor.click()
                    # A real unavailable-store response must appear as an error, not empty success.
                    def unavailable(route):
                        route.fulfill(status=503, content_type='application/json', body=json.dumps({'detail': 'Source history is unavailable'}))
                    page.route('**/source-history?**', unavailable)
                    opener.click()
                    expect(panel.get_by_role('alert')).to_contain_text('Source history is unavailable')
                    page.unroute('**/source-history?**', unavailable)
                    panel.get_by_role('button', name='Retry', exact=True).click()
                    expect(panel.get_by_label('To revision')).to_have_value('24')
                    editor.click()
                    expect(page.get_by_label('Unsaved editor')).to_have_value('do not overwrite me')
                    assert not errors, errors
                    assert not mutations, mutations
                    assert all(p.read_bytes() == content for p, content in before.items()), 'History UI changed a database'
                    page.get_by_label('Source type').select_option('strategy')
                    module = page.get_by_role('button', name='Restore strategy version')
                    apply = panel.get_by_role('button', name='Review and restore')
                    module.click()
                    # Pick the earliest strategy revision, prior to the parameter edit.
                    revisions = panel.get_by_label('Historical revision')
                    expect(revisions.locator('option')).to_have_count(3)
                    revisions.select_option(revisions.locator('option').last.get_attribute('value'))
                    expect(diff.locator('.modified .view-lines')).to_contain_text('top_n: int = 10')
                    expect(panel.locator('.sh-file')).to_have_count(1)
                    expect(panel.locator('.sh-file h4')).to_have_text('strategies/copy.py')
                    expect(apply).to_be_disabled()
                    editor.click()
                    expect(page.get_by_label('Unsaved editor')).to_have_value('do not overwrite me')
                    page.get_by_label('Unsaved editor').fill('')
                    page.get_by_label('Pending visual edits').check()
                    module.click()
                    expect(diff.locator('.line-delete').first).to_be_visible()
                    expect(apply).to_be_disabled()
                    editor.click()
                    expect(page.get_by_label('Pending visual edits')).to_be_checked()
                    page.get_by_label('Pending visual edits').uncheck()
                    module.click()
                    expect(apply).to_be_enabled()
                    for width in (1280, 390):
                        page.set_viewport_size({'width': width, 'height': 800})
                        if width == 1280:
                            expect(diff).to_have_class(re.compile('side-by-side'))
                        else:
                            expect(diff).not_to_have_class(re.compile('side-by-side'))
                        assert diff.bounding_box()['width'] > width * 0.85
                        pane = diff.locator('.original' if width == 1280 else '.modified')
                        pane.get_by_role('textbox').focus()
                        page.keyboard.press('Control+End')
                        expect(diff.get_by_text('end-of-review', exact=False).last).to_be_in_viewport()
                        wrap = page.get_by_label('Wrap lines')
                        wrap.uncheck()
                        wrap.check()
                        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                        if args.output and width == 390:
                            page.screenshot(path=str(args.output / 'strategy-restore-preview-narrow.png'))
                    page.set_viewport_size({'width': 1280, 'height': 900})
                    if args.output:
                        page.screenshot(path=str(args.output / 'strategy-restore-preview.png'))
                    apply.click()
                    expect(page.get_by_role('dialog')).to_be_visible()
                    assert not mutations, mutations
                    page.get_by_role('button', name='Cancel', exact=True).click()
                    assert not mutations, mutations
                    apply.click()
                    page.get_by_role('button', name='Restore version', exact=True).click()
                    expect(panel.get_by_role('status').filter(has_text='Restored as new revision')).to_be_visible(timeout=30000)
                    expect(panel.locator('option').first).to_contain_text('restored from')
                    editor.click()
                    page.get_by_label('Unsaved editor').fill('do not overwrite me')
                    page.get_by_label('Source type').select_option('factor')
                    restore = page.get_by_role('button', name='Restore factor version')
                    restore.click()
                    apply = panel.get_by_role('button', name='Review and restore')
                    expect(diff.locator('.modified .view-lines')).to_contain_text('] = 20,')
                    expect(apply).to_be_disabled()
                    editor.click()
                    expect(page.get_by_label('Unsaved editor')).to_have_value('do not overwrite me')
                    page.get_by_label('Unsaved editor').fill('')
                    restore.click()
                    expect(apply).to_be_enabled()
                    if args.output:
                        page.screenshot(path=str(args.output / 'factor-restore-preview.png'))
                    apply.click()
                    page.get_by_role('button', name='Restore version', exact=True).click()
                    expect(panel.get_by_role('status').filter(has_text='Restored as new revision')).to_be_visible(timeout=30000)
                    expect(panel.locator('option').first).to_contain_text('restored from')
                    for kind, nav, resource in [
                        ('validation', 'Restore validation version', '/validation/'),
                        ('recipe', 'Restore recipe version', '/data-sync/'),
                    ]:
                        page.get_by_label('Source type').select_option(kind)
                        page.get_by_role('button', name=nav, exact=True).click()
                        revisions = panel.get_by_label('Historical revision')
                        revisions.select_option(revisions.locator('option').last.get_attribute('value'))
                        apply = panel.get_by_role('button', name='Review and restore')
                        expect(apply).to_be_enabled()
                        apply.click()
                        page.get_by_role('button', name='Restore version', exact=True).click()
                        expect(panel.get_by_role('status').filter(has_text='Restored as new revision')).to_be_visible(timeout=30000)
                        if kind == 'validation':
                            expect(page.get_by_role('button', name='Run saved source', include_hidden=True)).to_be_disabled()
                            expect(page.get_by_text('Refresh required before running')).to_be_attached()
                            panel.get_by_role('button', name='Retry refresh', exact=True).click()
                            editor.click()
                            expect(page.get_by_role('button', name='Run saved source', include_hidden=True)).to_be_enabled()
                    revisions = panel.get_by_label('Historical revision')
                    revisions.select_option('2')
                    expect(panel.locator('.sh-file')).to_have_count(0)
                    expect(panel.locator('.sh-metadata')).to_contain_text('rq.a_share_daily')
                    panel.get_by_role('button', name='Review and restore').click()
                    page.get_by_role('button', name='Restore version', exact=True).click()
                    expect(panel.get_by_role('status').filter(has_text='Restored as new revision')).to_be_visible(timeout=30000)
                    revisions.select_option('3')
                    page.evaluate('''async () => {
                      const workspace = await (await fetch('/api/data-sync/recipes/history-demo')).json()
                      const response = await fetch('/api/data-sync/recipes/history-demo', {method: 'PUT', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({
                        source: workspace.draft.source + '\\n# concurrent recipe edit\\n',
                        expected_source_sha256: workspace.draft.source_sha256, confirm_write: true,
                      })})
                      if (!response.ok) throw new Error(await response.text())
                    }''')
                    panel.get_by_role('button', name='Review and restore').click()
                    page.get_by_role('button', name='Restore version', exact=True).click()
                    expect(panel.get_by_role('alert').filter(has_text='changed since restore preview')).to_be_visible()
                    assert len(mutations) == 7 and '/strategy-module/restore?' in mutations[0] and '/factors/' in mutations[1] and '/data-sync/' in mutations[4] and mutations[-1].endswith('/restore'), mutations
                    assert not errors, errors
                    browser.close()
                print('PASS: read-only comparison, guarded restore preview, confirmed restore and revision receipt')
    finally:
        for process in reversed(processes):
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        html.unlink(missing_ok=True)
        tsx.unlink(missing_ok=True)


if __name__ == '__main__':
    main()
