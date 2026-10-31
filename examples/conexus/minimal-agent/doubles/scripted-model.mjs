// A deterministic stand-in for a model provider.
//
// The Conexus Host only ever needs a `complete(request)` function, so a test
// double is a script of turns rather than a model. Two shapes are provided
// because they exercise different amounts of the real path:
//
//   scriptedCompletion  - the in-process callback `createLocalHost` accepts.
//                         Fastest, and what the lifecycle tests use.
//   startModelEndpoint  - a loopback OpenAI-compatible HTTP endpoint, so the
//                         example also exercises `createModelCompletion`,
//                         its URL and credential rules, and its 5 MiB cap.
//
// Neither needs a provider account or an API key.
import { createServer } from 'node:http'
import { randomUUID } from 'node:crypto'

/** One scripted assistant turn that calls a tool. */
export function toolCall(name, args) {
  return {
    content: null,
    finish_reason: 'tool_calls',
    tool_calls: [{ id: randomUUID(), type: 'function', function: { name, arguments: JSON.stringify(args) } }],
  }
}

/** One scripted assistant turn that only speaks. */
export function say(content) {
  return { content, finish_reason: 'stop' }
}

/**
 * Builds a `complete` callback from an ordered list of turns. Each entry is
 * either a turn or a function of (request, turnIndex) returning one, so a
 * script can branch on what the Agent was told by the previous tool result.
 * Running past the end of the script is a scripting error, not a silent stop.
 */
export function scriptedCompletion(script, { onRequest } = {}) {
  let turn = 0
  const requests = []
  const complete = async (request) => {
    requests.push(request)
    onRequest?.(request, turn)
    const entry = script[turn]
    turn += 1
    if (!entry) throw new Error(`The scripted model ran out of turns at turn ${turn}.`)
    return typeof entry === 'function' ? entry(request, turn - 1) : entry
  }
  complete.requests = requests
  return complete
}

/**
 * Serves the same script over an OpenAI-compatible `/v1/chat/completions`
 * endpoint bound to 127.0.0.1. Returns the base URL plus a close function.
 */
export async function startModelEndpoint(script, { model = 'example/local' } = {}) {
  const complete = scriptedCompletion(script)
  const server = createServer((request, response) => {
    if (request.method !== 'POST' || !request.url.endsWith('/chat/completions')) {
      response.writeHead(404).end('{}')
      return
    }
    const chunks = []
    request.on('data', (chunk) => chunks.push(chunk))
    request.on('end', async () => {
      try {
        const payload = JSON.parse(Buffer.concat(chunks).toString('utf8'))
        const turn = await complete(payload)
        const body = JSON.stringify({
          id: `chatcmpl-${randomUUID()}`,
          object: 'chat.completion',
          model,
          choices: [{
            index: 0,
            message: { role: 'assistant', content: turn.content, ...(turn.tool_calls ? { tool_calls: turn.tool_calls } : {}) },
            finish_reason: turn.finish_reason,
          }],
        })
        response.writeHead(200, { 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(body) }).end(body)
      } catch (problem) {
        // A failing double must look like a provider failure, not a hang.
        const body = JSON.stringify({ error: { message: problem.message } })
        response.writeHead(500, { 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(body) }).end(body)
      }
    })
  })
  await new Promise((done) => server.listen(0, '127.0.0.1', done))
  return {
    model,
    baseUrl: `http://127.0.0.1:${server.address().port}/v1`,
    requests: complete.requests,
    close: () => new Promise((done) => server.close(done)),
  }
}
