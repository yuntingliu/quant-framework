"""Exercise report evidence/search/compare/export with real stores and isolated Documents."""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path
from uuid import uuid4

SEED = '''
from alphalab import StrategyRepository
from alphalab.dataio.runtime import OperationsStore
from alphalab.validation.repository import ValidationRepository
repo = StrategyRepository()
repo.clone_project('sdk-v1-default', 'research')
package = repo.get_package('research')
repo.record_factor_evidence('research', 'momentum_20d', 'main', {
    'project_id': 'research', 'revision': package['revision'],
    'source_sha256': package['source_sha256'], 'status': 'insufficient',
    'summary': {'observations': 3, 'mean_rank_ic': 0.02}, 'rows': [],
}, {'start_date': '2024-01-01', 'end_date': '2025-01-01', 'parameters': {'window': 20}},
    {'universe': 'research_universe', 'start_date': '2024-01-01', 'end_date': '2025-01-01'},
    {'forward_return': 'next_open_to_open'}, {'scope': 'strategy_package'})
repo.close()
ValidationRepository().get_or_create('research')
OperationsStore().save_recipe_draft('research', '# recipe\\n')
'''
HARNESS = '''
import React from 'react'
import { createRoot } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { LanguageProvider } from '/src/contexts/LanguageContext'
import { ThemeProvider } from '/src/contexts/ThemeContext'
import { WorkspaceProvider } from '/src/contexts/WorkspaceContext'
import { AgentPromptProvider } from '/src/contexts/AgentPromptContext'
import { ReportWorkbenchWidget } from '/src/widgets/research/ReportWorkbench'
import '/src/index.css'
import '/src/styles.css'
createRoot(document.getElementById('root')).render(<QueryClientProvider client={new QueryClient()}>
  <LanguageProvider><ThemeProvider><WorkspaceProvider><AgentPromptProvider><ReportWorkbenchWidget /></AgentPromptProvider></WorkspaceProvider></ThemeProvider></LanguageProvider>
</QueryClientProvider>)
'''


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def ready(url, process):
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError('Preview server exited')
        try:
            with urllib.request.urlopen(url, timeout=1):
                return
        except OSError:
            time.sleep(0.1)
    raise TimeoutError('Preview did not become ready')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--python', required=True)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    from playwright.sync_api import expect, sync_playwright
    root = Path(__file__).resolve().parents[1]
    desktop = root / 'apps' / 'desktop'
    name = f'report-check-{uuid4().hex}'
    html, tsx = desktop / f'{name}.html', desktop / f'{name}.tsx'
    processes = []
    try:
        with tempfile.TemporaryDirectory(prefix='report-ui-') as temp:
            state = Path(temp)
            env = {**os.environ, 'PYTHONPATH': str(root), 'ALPHALAB_RUNTIME_DIR': str(state / 'runtime'),
                   'ALPHALAB_APP_DATA_DIR': str(state / 'app'), 'ALPHALAB_CACHE_DIR': str(state / 'cache')}
            subprocess.run([args.python, '-c', SEED], cwd=root, env=env, check=True)
            api_port, web_port = free_port(), free_port()
            api_url = f'http://127.0.0.1:{api_port}'
            with (state / 'servers.log').open('w+') as log:
                code = "from fastapi import FastAPI; from apps.api.routers.conexus import router; import uvicorn; app=FastAPI(); app.include_router(router); uvicorn.run(app,host='127.0.0.1',port=" + str(api_port) + ")"
                processes.append(subprocess.Popen([args.python, '-c', code], cwd=root, env=env, stdout=log, stderr=log))
                ready(api_url + '/openapi.json', processes[-1])
                html.write_text(f'<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"></head><body><div id="root"></div><script type="module" src="/{name}.tsx"></script></body></html>')
                html.write_text(html.read_text().replace('<head>', '<head><script>localStorage.setItem("alphalab-language","zh");localStorage.setItem("alphalab-language-default-version","3");</script>'))
                tsx.write_text(HARNESS)
                processes.append(subprocess.Popen(['node', 'node_modules/vite/bin/vite.js', '--port', str(web_port)], cwd=desktop,
                    env={**env, 'WEB_ONLY': '1', 'ALPHALAB_API_ORIGIN': api_url}, stdout=log, stderr=log))
                url = f'http://127.0.0.1:{web_port}/{name}.html'
                ready(url, processes[-1])
                references = [
                    {'kind': 'strategy_package', 'revision': 1},
                    {'kind': 'validation_package', 'revision': 1},
                    {'kind': 'recipe_revision', 'revision': 1},
                    {'kind': 'data_manifest'},
                ]
                nodes = []
                for index, title in enumerate(['Momentum review', 'Second report'], start=1):
                    nodes.append({
                        'id': f'report-{index}', 'type': 'document', 'description': 'AlphaLab quantitative report',
                        'label': title, 'updatedAt': '2026-01-01T00:00:00Z',
                        'values': {'content': f'# {title}\n\nFixed evidence.', 'data': {
                            'provenance': {'projectId': 'research', 'references': references},
                        }},
                    })
                with sync_playwright() as browser_api:
                    browser = browser_api.chromium.launch(headless=True)
                    page = browser.new_page(viewport={'width': 1280, 'height': 900})
                    errors = []
                    page.on('pageerror', lambda error: errors.append(str(error)))
                    page.route('**/api/conexus/workspace', lambda route: route.fulfill(
                        status=200, content_type='application/json', body=json.dumps({'workspace': {'nodes': nodes}}),
                    ))
                    page.goto(url)
                    page.get_by_role('button', name='证据', exact=True).click()
                    expect(page.get_by_text('策略包 v1')).to_be_visible()
                    expect(page.get_by_text('验证包 v1')).to_be_visible()
                    expect(page.get_by_text('Data manifest unavailable', exact=False).first).to_be_visible()
                    if args.output:
                        args.output.mkdir(parents=True, exist_ok=True)
                        page.screenshot(path=str(args.output / 'report-evidence.png'))
                    page.get_by_role('button', name='比较', exact=True).click()
                    expect(page.get_by_text('Fixed evidence.', exact=False).first).to_be_visible()
                    page.get_by_label('搜索报告正文或 ID…').fill('Second')
                    expect(page.get_by_text('Second report', exact=True).first).to_be_visible()
                    expect(page.get_by_text('Momentum review', exact=True)).to_have_count(0)
                    page.get_by_role('button', name='导出证据', exact=True).click()
                    page.set_viewport_size({'width': 390, 'height': 800})
                    page.get_by_role('button', name='证据', exact=True).click()
                    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                    if args.output:
                        page.screenshot(path=str(args.output / 'report-evidence-narrow.png'))
                    assert not errors, errors
                    browser.close()
                print('PASS: report evidence, search, comparison, export and narrow layout')
    finally:
        for process in reversed(processes):
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
        html.unlink(missing_ok=True)
        tsx.unlink(missing_ok=True)


if __name__ == '__main__':
    main()
