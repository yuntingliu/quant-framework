// Host-only serialized stdio bridge. Keep a worker warm while busy, close it
// after one idle second. Every request is still its own durable DB transaction.
import { spawn } from 'node:child_process'
import { fileURLToPath } from 'node:url'
import { CollaborationError } from './contracts.mjs'

const cwd = fileURLToPath(new URL('../../../', import.meta.url))
export function createControlClient({ pythonExecutable, storeRoot, evidenceRoot, taskId }) {
  for (const value of [pythonExecutable, storeRoot, evidenceRoot, taskId]) {
    if (typeof value !== 'string' || !value) throw new CollaborationError('INVALID_CONTRACT')
  }
  let worker, idle, pending, buffer = Buffer.alloc(0), queue = Promise.resolve()
  const stop = () => {
    const child = worker
    worker = undefined
    buffer = Buffer.alloc(0)
    child?.kill()
    clearTimeout(idle)
  }
  function start() {
    if (worker) return
    const child = spawn(pythonExecutable, ['-m', 'alphalab.collaboration.control_worker',
      '--store-root', storeRoot, '--evidence-root', evidenceRoot], {
      cwd, shell: false, windowsHide: true, stdio: ['pipe', 'pipe', 'ignore'],
    })
    worker = child
    const failed = () => { if (worker === child) { pending?.reject(new CollaborationError('STORAGE_UNAVAILABLE')); stop() } }
    child.on('error', failed)
    child.on('close', failed)
    child.stdin.on('error', failed)
    child.stdout.on('data', chunk => {
      if (worker !== child) return
      buffer = Buffer.concat([buffer, chunk])
      if (buffer.length > 8 * 1024 * 1024) { failed(); return }
      const newline = buffer.indexOf(10)
      if (newline < 0) return
      try {
        const response = JSON.parse(buffer.subarray(0, newline).toString('utf8'))
        buffer = buffer.subarray(newline + 1)
        if (response.status !== 'succeeded') pending?.reject(new CollaborationError(response.error_code ?? 'STORAGE_UNAVAILABLE'))
        else pending?.resolve(response.result)
      } catch { failed() }
    })
  }
  return (operation, args = {}) => {
    const payload = JSON.stringify({ operation, task_id: taskId, args }) + '\n'
    if (Buffer.byteLength(payload) > 600000) return Promise.reject(new CollaborationError('EVIDENCE_LIMIT'))
    const request = queue.then(() => new Promise((resolve, reject) => {
      clearTimeout(idle)
      start()
      const timer = setTimeout(() => { pending?.reject(new CollaborationError('STORAGE_UNAVAILABLE')); stop() }, 15000)
      const finish = callback => value => {
        clearTimeout(timer)
        pending = undefined
        idle = setTimeout(stop, 1000)
        idle.unref()
        callback(value)
      }
      pending = { resolve: finish(resolve), reject: finish(reject) }
      worker.stdin.write(payload)
    }))
    queue = request.catch(() => {})
    return request
  }
}
