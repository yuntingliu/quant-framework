// Offline foundation acceptance entry point; no application publication.
import assert from 'node:assert/strict'
import { randomUUID } from 'node:crypto'
import { writeFile } from 'node:fs/promises'
import { resolve, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { acceptanceScenarios, privateOutput, runAcceptanceScenario } from './lib/collaboration-acceptance.mjs'

// Keep the focused Phase 5-7 test API stable.
export { scenarios, prepareFixture, runScenario } from './lib/collaboration-host-scenarios.mjs'
export { acceptanceScenarios, runAcceptanceScenario }

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const args = process.argv.slice(2)
  if (args.includes('--help')) {
    console.log('Usage: node scripts/check_collaboration_foundation.mjs --mode scripted --scenario clean|all|seeded-errors|stale-evidence|' + acceptanceScenarios.filter(s => s !== 'clean').join('|') + ' --output build/member1/foundation [--python executable]')
    console.log('Default: clean. Local scripted responses only. Output stays under ignored build/. No final publication or financial-quality evaluation.')
  } else {
    const get = (flag, fallback) => args.includes(flag) ? args[args.indexOf(flag) + 1] : fallback
    assert(args.every((arg, index) => index % 2 ? args[index - 1].startsWith('--') : ['--mode', '--scenario', '--output', '--python'].includes(arg)), 'Unknown or malformed CLI option.')
    assert.equal(args.length % 2, 0, 'Each option needs a value.')
    assert.equal(get('--mode', 'scripted'), 'scripted', 'Only scripted mode is supported.')
    const selected = get('--scenario', 'clean')
    assert(['all', 'seeded-errors', 'stale-evidence', ...acceptanceScenarios].includes(selected), 'Unknown scenario.')
    const output = await privateOutput(resolve(get('--output', 'build/member1/foundation')))
    const pythonExecutable = resolve(get('--python', 'build/member1/venv/Scripts/python.exe'))
    const trial = await privateOutput(join(output, `trial-${randomUUID().slice(0, 12)}`))
    const summary = []
    for (const scenario of selected === 'all' ? acceptanceScenarios : [selected]) {
      try {
        const result = await runAcceptanceScenario({ scenario, output: join(trial, scenario), pythonExecutable })
        summary.push(result)
        console.log(JSON.stringify(result))
      } catch {
        const failed = { scenario, status: 'failed', artifacts: join(trial, scenario), error_code: 'ACCEPTANCE_FAILED' }
        summary.push(failed)
        console.log(JSON.stringify(failed))
        process.exitCode = 1
      }
    }
    await writeFile(join(trial, 'summary.json'), JSON.stringify({ status: summary.every(r => r.status === 'passed') ? 'passed' : 'failed', scenarios: summary }, null, 2) + '\n')
  }
}
