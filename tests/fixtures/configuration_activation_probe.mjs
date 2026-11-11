/** Test-owned Node child, real durable composition and synthetic providers. */
import { readAgentHandoff } from '../../integrations/conexus/configuration-agent-auth.mjs'
import { createCoordinatedAgentConfiguration } from '../../integrations/conexus/configuration-backend.mjs'
import { spawn } from 'node:child_process'
import { fileURLToPath } from 'node:url'
const mode = process.argv[2] || 'valid'
const handoff = await readAgentHandoff()
let constructed = false, runtime
try {
  if (mode === 'copy') {
    const child = spawn(process.execPath, [fileURLToPath(import.meta.url), 'valid'], {
      windowsHide: true, stdio: ['pipe', 'pipe', 'pipe'],
    })
    let output = ''
    child.stdout.on('data', part => { output += part })
    child.stderr.resume()
    const timer = setTimeout(() => child.kill(), 15000)
    try {
      child.stdin.end(JSON.stringify({ contract: handoff.contract, secret: handoff.secret,
        token: handoff.token, launch: handoff.launch, api_origin: handoff.apiOrigin }) + '\n')
      await new Promise((resolve, reject) => { child.once('error', reject); child.once('close', resolve) })
      const result = JSON.parse(output)
      process.stdout.write(JSON.stringify({ copiedRejected: !result.accepted && !result.constructed }))
    } finally { clearTimeout(timer) }
  } else {
    const contract = { ...handoff.contract }
    if (mode === 'wrong-installation') contract.installation_id = '0'.repeat(32)
    if (mode === 'wrong-decision') contract.decision_id = '0'.repeat(32)
    runtime = await createCoordinatedAgentConfiguration(contract, profile => {
      constructed = true
      return async () => ({ content: profile.apiKey === 'migration-model-key-6101' ? 'matched' : 'different' })
    }, { production: true, ownerProof: mode === 'wrong-secret' ? '0'.repeat(64) : handoff.secret,
      env: { CONEXUS_MODEL_API_KEY: 'must-not-be-borrowed' } })
    const result = await runtime.complete({ messages: [], signal: new AbortController().signal })
    process.stdout.write(JSON.stringify({ accepted: true, matched: result.content === 'matched', constructed }))
  }
} catch (error) {
  process.stdout.write(JSON.stringify({ accepted: false, code: error.code, constructed }))
} finally { await runtime?.close(); process.stdin.destroy() }
