"""Exercise the actual history dialog against an isolated real API.

Requires Playwright + Chromium, installed frontend dependencies and a Python
interpreter with AlphaLab's app/dev dependencies. Never opens a user's store.
"""

from __future__ import annotations

import argparse
import json
import os
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
repo = StrategyRepository()
repo.clone_project('sdk-v1-default', 'history-demo')
repo.create_strategy('history-demo', 'copy', name='Comparison strategy')
repo.select_strategy('main')
factor = repo.get_factor_source('history-demo', 'momentum_20d')
repo.replace_factor_source('history-demo', 'momentum_20d', factor['source'].replace(' - 1.0', ' - 0.9'))
repo.close()
validation = ValidationRepository()
initial = validation.get_or_create('history-demo')
validation.update_source('history-demo', initial['source'] + '\\n# verified validation change\\n')
validation.close()
ops = OperationsStore()
for i in range(24):
    ops.save_recipe_draft('history-demo', f'# recipe {i}\\n')
'''
HARNESS = '''
import React, { useState } from 'react'
import { createRoot } from 'react-dom/client'
import { SourceHistoryDialog } from '/src/components/shared/SourceHistoryDialog'
import { LanguageProvider } from '/src/contexts/LanguageContext'
import '/src/index.css'
function Demo() {
  const [kind, setKind] = useState('strategy')
  const [text, setText] = useState('unsaved editor buffer')
  return <LanguageProvider><main style={{padding: 24}}>
    <h1>Source history — isolated component acceptance</h1>
    <label>Source type<select aria-label="Source type" value={kind} onChange={e => setKind(e.target.value)}>
      <option value="strategy">Strategy / factors</option><option value="validation">Validation</option><option value="recipe">Recipe</option>
    </select></label>
    <textarea aria-label="Unsaved editor" value={text} onChange={e => setText(e.target.value)} />
    <SourceHistoryDialog projectId="history-demo" strategyId="main" kind={kind} />
  </main></LanguageProvider>
}
createRoot(document.getElementById('root')).render(<Demo />)
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
            subprocess.run([args.python, '-c', SEED], cwd=root, env=env, check=True)
            databases = list((state / 'runtime' / 'app').glob('*.db'))
            assert {p.name for p in databases} >= {'alphalab.db', 'dataio.db'}, 'Missing isolated stores'
            before = {p: p.read_bytes() for p in databases}
            api_port, web_port = free_port(), free_port()
            api_url = f'http://127.0.0.1:{api_port}'
            with (state / 'servers.log').open('w+') as log:
                code = "from fastapi import FastAPI; from apps.api.routers.strategy import router; import uvicorn; app=FastAPI(); app.include_router(router); uvicorn.run(app,host='127.0.0.1',port=" + str(api_port) + ")"
                processes.append(subprocess.Popen([args.python, '-c', code], cwd=root, env=env, stdout=log, stderr=log))
                ready(api_url + '/openapi.json', processes[-1])
                html.write_text(f'<html><head><title>History acceptance</title></head><body><div id="root"></div><script type="module" src="/{name}.tsx"></script></body></html>')
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
                    browser = browser_api.chromium.launch(headless=True)
                    page = browser.new_page(viewport={'width': 1280, 'height': 900})
                    errors, mutations = [], []
                    page.on('pageerror', lambda error: errors.append(str(error)))
                    page.on('request', lambda request: mutations.append(request.url) if '/api/' in request.url and request.method != 'GET' else None)
                    page.goto(url)
                    page.get_by_label('Unsaved editor').fill('do not overwrite me')
                    opener = page.get_by_role('button', name='Source history & comparison', exact=True)
                    opener.click()
                    dialog = page.get_by_role('dialog')
                    expect(dialog.get_by_label('From revision')).to_have_value('1')
                    dialog.get_by_role('button', name='Compare versions', exact=True).click()
                    expect(dialog.get_by_role('heading', name='modified · factors/momentum_20d.py')).to_be_visible()
                    expect(dialog.locator('.sh-line-added')).to_have_count(1)
                    expect(dialog.locator('.sh-line-removed')).to_have_count(1)
                    expect(dialog.locator('.sh-impact li').filter(has_text='Comparison strategy').filter(has_text='copy')).to_be_visible()
                    if args.output:
                        args.output.mkdir(parents=True, exist_ok=True)
                        page.screenshot(path=str(args.output / 'source-history-strategy.png'))
                    # Identical versions produce no changes, without stale previous output.
                    target = dialog.get_by_label('To revision').input_value()
                    dialog.get_by_label('From revision').select_option(target)
                    expect(dialog.get_by_role('region', name='Comparison results')).to_have_count(0)
                    dialog.get_by_role('button', name='Compare versions', exact=True).click()
                    expect(dialog.get_by_text('These versions are identical')).to_be_visible()
                    page.keyboard.press('Escape')
                    expect(page.get_by_label('Unsaved editor')).to_have_value('do not overwrite me')
                    for kind, path in [('validation', 'validation.py'), ('recipe', 'recipe.py')]:
                        page.get_by_label('Source type').select_option(kind)
                        opener.click()
                        expect(dialog.get_by_label('To revision')).not_to_have_value('')
                        if kind == 'recipe':
                            dialog.get_by_role('button', name='Load earlier versions').click()
                            expect(dialog.get_by_label('From revision').locator('option')).to_have_count(24)
                        dialog.get_by_role('button', name='Compare versions', exact=True).click()
                        expect(dialog.get_by_role('heading', name=f'modified · {path}')).to_be_visible()
                        page.keyboard.press('Escape')
                    # A real unavailable-store response must appear as an error, not empty success.
                    def unavailable(route):
                        route.fulfill(status=503, content_type='application/json', body=json.dumps({'detail': 'Source history is unavailable'}))
                    page.route('**/source-history?**', unavailable)
                    opener.click()
                    expect(dialog.get_by_role('alert')).to_contain_text('Source history is unavailable')
                    page.unroute('**/source-history?**', unavailable)
                    dialog.get_by_role('button', name='Retry', exact=True).click()
                    expect(dialog.get_by_label('To revision')).to_have_value('24')
                    page.keyboard.press('Escape')
                    expect(page.get_by_label('Unsaved editor')).to_have_value('do not overwrite me')
                    assert not errors, errors
                    assert not mutations, mutations
                    browser.close()
                assert all(p.read_bytes() == content for p, content in before.items()), 'History UI changed a database'
                print('PASS: real API/UI strategy/factor, validation, recipe, pagination, identical comparison, error/retry, unsaved buffer, no writes')
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
