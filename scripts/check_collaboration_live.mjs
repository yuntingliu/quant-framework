import assert from 'node:assert/strict'
import { randomUUID } from 'node:crypto'
import { writeFile } from 'node:fs/promises'
import { join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { liveScenarios, root, runLiveScenario, requireLive } from './lib/collaboration-live.mjs'
import { privateOutput } from './lib/collaboration-acceptance.mjs'

export { liveScenarios, runLiveScenario }

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const args = process.argv.slice(2)
  if (args.includes('--help')) {
    console.log('Usage: node scripts/check_collaboration_live.mjs --live --scenario all|' + liveScenarios.join('|')
      + ' [--provider-settings private-json-path] [--python executable] [--output build/member1/live]')
    console.log('Opt-in paid requests; default clean, 12 requests/4096 output tokens/180 seconds per normal case. No hard input-token or monetary guarantee. No production data changes. Credentials are read from the active saved profile, never command arguments.')
    console.log('Use a comma-separated --scenario list to resume selected cases without repeating paid checks.')
  } else {
    requireLive(args.includes('--live'))
    const pairs = args.filter(arg => arg !== '--live')
    assert.equal(pairs.length % 2, 0)
    for (let i = 0; i < pairs.length; i += 2) assert(['--scenario', '--provider-settings', '--python', '--output'].includes(pairs[i]))
    const get = (flag, fallback) => pairs.includes(flag) ? pairs[pairs.indexOf(flag) + 1] : fallback
    const selected = get('--scenario', 'clean')
    const scenarios = selected === 'all' ? liveScenarios : selected.split(',')
    assert(scenarios.length && scenarios.every(scenario => liveScenarios.includes(scenario)))
    assert.equal(new Set(scenarios).size, scenarios.length)
    const trial = await privateOutput(join(resolve(get('--output', 'build/member1/live')), `trial-${randomUUID().slice(0, 12)}`))
    const rows = []
    for (const scenario of scenarios) {
      let result
      try {
        result = await runLiveScenario({ scenario, output: join(trial, scenario), enabled: true,
          pythonExecutable: resolve(get('--python', 'build/member1/venv/Scripts/python.exe')),
          settingsPath: resolve(get('--provider-settings', join(root, 'data/runtime/conexus/secrets/model-providers.json'))) })
      } catch { result = { scenario, status: 'failed', error_code: 'LIVE_RUNNER_FAILED', artifacts: join(trial, scenario) } }
      rows.push(result)
      console.log(JSON.stringify(result))
      if (result.status !== 'passed') process.exitCode = 1
      await writeFile(join(trial, 'summary.json'), JSON.stringify({ mode: 'live', status: rows.every(r => r.status === 'passed') ? 'passed' : 'failed', scenarios: rows }, null, 2))
    }
  }
}
