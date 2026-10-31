// A deterministic stand-in for the AlphaLab HTTP API.
//
// The real Tool node in integrations/conexus/alphalab-research-agent/tools/
// talks to the AlphaLab backend over `ALPHALAB_API_ORIGIN`. That backend needs
// a Python environment, a runtime data store and a project database, none of
// which an example should require. This stub answers the same small set of
// routes with fixed numbers, so the example exercises the integration shape —
// bounded reads, an asynchronous job, a pinned result — rather than the
// financial computation itself.
//
// The numbers here are arbitrary and carry no economic meaning.
import { createServer } from 'node:http'

const COVERAGE = {
  dataset: 'rq.bars',
  status: 'ready',
  first_session: '2024-01-02',
  last_session: '2024-12-31',
  session_count: 244,
  symbol_count: 8,
  missing_fields: [],
}

const SUMMARY = {
  run_id: 'bt_000001',
  strategy_id: 'main',
  return_periods_per_year: 252,
  total_return: 0.1432,
  annualized_volatility: 0.0934,
  max_drawdown: -0.0817,
  trades_attempted: 376,
  trades_filled: 376,
  execution_reliable: true,
  warnings: [],
}

/**
 * @param pollsBeforeDone  How many status reads report `running` before the
 *                         job reaches a terminal state. Models the real
 *                         submit/poll/wait boundary without a real backtest.
 */
export async function startAlphaLabApiStub({ pollsBeforeDone = 2 } = {}) {
  const jobs = new Map()
  let nextJob = 1
  const requests = []

  const routes = (method, path, body) => {
    requests.push({ method, path })

    if (method === 'GET' && path === '/api/agent/context') {
      return [200, { project_id: 'sdk-v1-default', strategy_id: 'main', datasets: ['rq.bars', 'rq.daily_factors'] }]
    }
    if (method === 'GET' && path === '/api/data/status') {
      return [200, COVERAGE]
    }
    if (method === 'POST' && path === '/api/backtests') {
      // The real API refuses execution without the confirmation flag, and so
      // does the stub: the confirmation boundary is part of what the example
      // demonstrates.
      if (body?.confirm_python_execution !== true) {
        return [403, { detail: 'Python execution requires confirm_python_execution=true.' }]
      }
      const id = `bt_${String(nextJob++).padStart(6, '0')}`
      jobs.set(id, { id, status: 'running', polls: 0 })
      // Submission returns a task id immediately; it never blocks on the run.
      return [202, { job_id: id, status: 'running' }]
    }
    const match = /^\/api\/backtests\/([A-Za-z0-9_]+)(\/summary)?$/.exec(path)
    if (method === 'GET' && match) {
      const job = jobs.get(match[1])
      if (!job) return [404, { detail: 'Unknown backtest job.' }]
      if (match[2]) {
        if (job.status !== 'succeeded') return [409, { detail: 'The backtest has not finished.' }]
        return [200, { ...SUMMARY, run_id: job.id }]
      }
      job.polls += 1
      if (job.polls > pollsBeforeDone) job.status = 'succeeded'
      return [200, { job_id: job.id, status: job.status, progress: Math.min(1, job.polls / (pollsBeforeDone + 1)) }]
    }
    return [404, { detail: 'Unknown route.' }]
  }

  const server = createServer((request, response) => {
    const chunks = []
    request.on('data', (chunk) => chunks.push(chunk))
    request.on('end', () => {
      let parsed
      try { parsed = chunks.length ? JSON.parse(Buffer.concat(chunks).toString('utf8')) : undefined }
      catch { parsed = undefined }
      const [status, payload] = routes(request.method, request.url, parsed)
      const body = JSON.stringify(payload)
      response.writeHead(status, { 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(body) }).end(body)
    })
  })
  await new Promise((done) => server.listen(0, '127.0.0.1', done))
  return {
    origin: `http://127.0.0.1:${server.address().port}`,
    requests,
    expectedSummary: SUMMARY,
    coverage: COVERAGE,
    close: () => new Promise((done) => server.close(done)),
  }
}
