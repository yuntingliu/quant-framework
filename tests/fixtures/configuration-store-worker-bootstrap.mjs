// Used only by test-owned Node subprocesses via --import. No live adapter uses it.
import { isMainThread, workerData, parentPort } from 'node:worker_threads'
import { existsSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'

if (!isMainThread) {
  const behavior = process.env.ALPHALAB_TEST_WORKER_BEHAVIOR
  const canary = 'fixture-node-worker-diagnostic-secret-5506'
  if (['result-delay-exit', 'result-crash', 'result-nonzero', 'result-hang'].includes(behavior)) {
    const post = parentPort.postMessage.bind(parentPort), close = parentPort.close.bind(parentPort)
    parentPort.postMessage = (...args) => {
      const result = post(...args)
      writeFileSync(join(workerData.root, 'worker-' + behavior + '-sent'), '')
      if (behavior === 'result-delay-exit') setTimeout(close, 150)
      else if (behavior === 'result-crash') setTimeout(() => { throw new Error(canary, { cause: new Error(canary) }) }, 50)
      else if (behavior === 'result-nonzero') setTimeout(() => process.exit(7), 50)
      else setInterval(() => {}, 1000)
      return result
    }
    if (behavior === 'result-delay-exit') parentPort.close = () => {}
  }
  if (['stdio', 'crash', 'hang', 'malformed-message'].includes(behavior)) writeFileSync(join(workerData.root, 'worker-' + behavior + '-entered'), '')
  if (behavior === 'stdio') {
    console.log(canary)
    console.error(canary)
  } else if (behavior === 'crash') {
    throw new Error(canary, { cause: new Error(canary) })
  } else if (behavior === 'hang') {
    Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0)
  } else if (behavior === 'malformed-message') {
    parentPort.postMessage({ ok: false, code: canary, rqdata: { password: canary } })
  } else if (behavior === 'pause' || behavior === 'pause-no-begin') {
    const { DatabaseSync } = await import('node:sqlite')
    const prepare = DatabaseSync.prototype.prepare
    const exec = DatabaseSync.prototype.exec
    if (behavior === 'pause-no-begin') {
      DatabaseSync.prototype.exec = function(sql) {
        return exec.call(this, sql.replace('; BEGIN', ''))
      }
    }
    DatabaseSync.prototype.prepare = function(sql) {
      if (sql.startsWith('SELECT models, search, selections')) {
        writeFileSync(join(workerData.root, 'reader-ready'), '')
        const wait = new Int32Array(new SharedArrayBuffer(4))
        while (!existsSync(join(workerData.root, 'release-reader'))) Atomics.wait(wait, 0, 0, 10)
      }
      return prepare.call(this, sql)
    }
  }
}
