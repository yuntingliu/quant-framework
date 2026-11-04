// Trusted Host creates this closure. Model input can select only a bounded read;
// it cannot supply the executable, store path, task identity or manifest grants.
import { spawn } from 'node:child_process'
import { fileURLToPath } from 'node:url'
import { CollaborationError } from './contracts.mjs'

const repositoryRoot = fileURLToPath(new URL('../../../', import.meta.url))
export function createEvidenceClient({ pythonExecutable, storeRoot, taskId, manifests, timeoutMs = 15000 }) {
  if (typeof pythonExecutable !== 'string' || !pythonExecutable || typeof storeRoot !== 'string' || !storeRoot ||
      !Number.isInteger(timeoutMs) || timeoutMs < 1 || timeoutMs > 30000) throw new CollaborationError('INVALID_CONTRACT')
  const scope = structuredClone({ task_id: taskId, manifests })
  return async (request, { signal } = {}) => {
    if (!request || typeof request !== 'object' || Array.isArray(request) ||
        !Object.keys(request).every(key => ['operation', 'manifest_id', 'artifact_id', 'locator', 'offset', 'limit'].includes(key))) {
      throw new CollaborationError('INVALID_CONTRACT')
    }
    const payload = JSON.stringify({ scope, request })
    if (Buffer.byteLength(payload) > 65536) throw new CollaborationError('EVIDENCE_LIMIT')
    return new Promise((resolve, reject) => {
      let finished = false, size = 0
      const chunks = []
      const child = spawn(pythonExecutable, ['-m', 'alphalab.collaboration.evidence_reader', '--store-root', storeRoot], {
        cwd: repositoryRoot, shell: false, windowsHide: true, stdio: ['pipe', 'pipe', 'ignore'],
      })
      const fail = code => {
        if (finished) return
        finished = true
        clearTimeout(timer)
        signal?.removeEventListener('abort', aborted)
        child.kill()
        reject(new CollaborationError(code))
      }
      const aborted = () => fail('STORAGE_UNAVAILABLE')
      const timer = setTimeout(() => fail('STORAGE_UNAVAILABLE'), timeoutMs)
      child.on('error', () => fail('STORAGE_UNAVAILABLE'))
      child.stdin.on('error', () => fail('STORAGE_UNAVAILABLE'))
      signal?.addEventListener('abort', aborted, { once: true })
      if (signal?.aborted) { aborted(); return }
      child.stdout.on('data', chunk => {
        size += chunk.length
        if (size > 600000) fail('EVIDENCE_LIMIT')
        else chunks.push(chunk)
      })
      child.on('close', () => {
        if (finished) return
        clearTimeout(timer)
        signal?.removeEventListener('abort', aborted)
        try {
          const response = JSON.parse(Buffer.concat(chunks).toString('utf8'))
          if (response.status !== 'succeeded') { fail(response.error_code ?? 'STORAGE_UNAVAILABLE'); return }
          finished = true
          resolve(response.result)
        } catch { fail('STORAGE_UNAVAILABLE') }
      })
      child.stdin.end(payload)
    })
  }
}
