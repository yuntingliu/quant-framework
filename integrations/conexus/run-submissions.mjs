import { mkdir, readFile, rename, writeFile } from 'node:fs/promises'
import { dirname } from 'node:path'
import { createHash, timingSafeEqual } from 'node:crypto'

const KEY = /^[A-Za-z0-9._:~-]{1,420}$/
const digest = value => createHash('sha256').update(value).digest()
const authorized = (request, token) =>
  timingSafeEqual(digest(request.headers.authorization ?? ''), digest(`Bearer ${token}`))

/**
 * Make Run creation idempotent without changing the pinned core.
 *
 * The Host returns a Run's access token exactly once, so an acknowledgement lost
 * after acceptance would otherwise strand a billed Run with nobody able to read it.
 * A repeated key gets the original reply. The file outlives a Host restart because a
 * key forgotten there would let the same message start a second Run.
 */
export async function installRunSubmissions(app, { slug, token, path, limit = 200 }) {
  const route = `/api/public/harnesses/${slug}/runs`
  const accepted = new Map()
  const inFlight = new Set()
  try {
    for (const [key, reply] of Object.entries(JSON.parse(await readFile(path, 'utf8')))) accepted.set(key, reply)
  } catch (problem) {
    if (problem.code !== 'ENOENT') app.log.warn('Run submission records could not be read')
  }
  async function persist() {
    while (accepted.size > limit) accepted.delete(accepted.keys().next().value)
    await mkdir(dirname(path), { recursive: true })
    // Holds live Run credentials, so it has the same protection as the model provider secrets beside it.
    await writeFile(`${path}.tmp`, JSON.stringify(Object.fromEntries(accepted)), { mode: 0o600 })
    await rename(`${path}.tmp`, path)
  }
  const submissionKey = request => {
    const key = request.headers['idempotency-key']
    return request.method === 'POST' && request.routeOptions.url === route && typeof key === 'string' && KEY.test(key)
      ? key : null
  }
  app.addHook('preHandler', async (request, reply) => {
    const key = submissionKey(request)
    // An unauthorized caller falls through to the Host's own refusal and learns nothing here.
    if (!key || !authorized(request, token)) return
    if (accepted.has(key)) return reply.code(202).send(accepted.get(key))
    if (inFlight.has(key)) return reply.code(409).send({ error: { code: 'submission_in_progress', message: 'This submission is already being accepted.' } })
    inFlight.add(key)
    request.submissionKey = key
  })
  // Recorded before the reply leaves, so the acknowledgement can never outrun the record.
  app.addHook('onSend', async (request, reply, payload) => {
    if (request.submissionKey && reply.statusCode === 202 && !accepted.has(request.submissionKey)) {
      accepted.set(request.submissionKey, JSON.parse(payload))
      await persist()
    }
    return payload
  })
  app.addHook('onResponse', async request => {
    if (request.submissionKey) inFlight.delete(request.submissionKey)
  })
  app.get('/api/alphalab/submissions/:key', async (request, reply) => {
    if (!authorized(request, token)) return reply.code(401).send({ error: { code: 'local_host_error', message: 'Authentication required.' } })
    const key = request.params.key
    if (accepted.has(key)) return { known: true, state: 'accepted', ...accepted.get(key) }
    return inFlight.has(key) ? { known: true, state: 'in_flight' } : { known: false }
  })
}
