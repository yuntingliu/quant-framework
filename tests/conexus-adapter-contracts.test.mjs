// Conformance tests for the adapters AlphaLab supplies to the pinned Conexus
// core, and for the doubles a benchmark substitutes for them.
//
//   npm --prefix apps/desktop test
//
// These need no built runtime, no provider account and no network: the real
// adapters take an injectable `fetchImpl`, so the whole failure surface is
// reachable offline. The contracts themselves live in
// integrations/conexus/contracts/adapter-contracts.v1.json;
// tests/contracts/test_conexus_adapter_contracts.py re-validates the same
// document with `jsonschema` so the hand-written validator cannot drift.
import assert from 'node:assert/strict'
import { test } from 'node:test'
import {
  CONTRACT_DOCUMENT, CONTRACT_IDS, CONTRACT_VERSION, VERIFIED_CORE_REVISION,
  AdapterContractError, assertContract, assertEquivalentProfiles, checkContract,
  contractDocumentProblems, isDeclaredCode, isRetryableCode, profileShortfall, withContracts,
} from '../integrations/conexus/contracts/adapters.mjs'
import { unsupportedKeywords } from '../integrations/conexus/contracts/validate.mjs'
import {
  TEST_CREDENTIALS, contractCompletion, contractFinancialTool, contractSearch,
  memoryStateStore, substituteProfile, textTurn, toolTurn,
} from '../integrations/conexus/contracts/doubles.mjs'
import { REDACTED, assertNoSecrets, redact } from '../integrations/conexus/contracts/redact.mjs'
import { configuredWebSearch } from '../integrations/conexus/web-search.mjs'
import { configuredCompletion, readActiveProvider } from '../integrations/conexus/model-providers.mjs'

const MISSING = '/nonexistent/alphalab-model-providers.json'
const SECRETS = Object.values(TEST_CREDENTIALS)
const refuse = () => { throw new Error('Unexpected network request.') }
const searchRequest = input => ({ input, signal: new AbortController().signal })
const jsonReply = data => new Response(JSON.stringify(data), { headers: { 'Content-Type': 'application/json' } })

// The profile the bundled local Host publishes, copied from the one valid
// example in the contract document rather than restated here.
const HOST_PROFILE = CONTRACT_DOCUMENT.contracts['runtime.adapter.profile'].examples.valid[0]

test('the contract document is internally consistent', () => {
  assert.deepEqual(contractDocumentProblems(), [])
  assert.equal(CONTRACT_VERSION, 1)
  assert.match(VERIFIED_CORE_REVISION, /^[0-9a-f]{40}$/)
})

test('every declared example validates as the document says it does', () => {
  for (const contractId of CONTRACT_IDS) {
    const { valid, invalid } = CONTRACT_DOCUMENT.contracts[contractId].examples
    for (const [index, value] of valid.entries()) {
      assert.deepEqual(checkContract(contractId, value), [], `${contractId} valid[${index}]`)
    }
    for (const [index, expected] of invalid.entries()) {
      const details = checkContract(contractId, expected.value)
      assert.ok(
        details.some(detail => detail.path === expected.path && detail.keyword === expected.keyword),
        `${contractId} invalid[${index}] expected ${expected.path} ${expected.keyword}, got ${JSON.stringify(details)}`,
      )
    }
  }
})

test('a schema keyword the validator would ignore is reported, not ignored', () => {
  // The document's own schemas are clean, which is what
  // contractDocumentProblems asserts above. This is the guard that keeps them
  // that way: a keyword added without an implementation fails loudly, because
  // a silently ignored constraint is worse than no constraint.
  assert.deepEqual(unsupportedKeywords({ type: 'string', maximum: 3 }),
    ['/: unsupported schema keyword: maximum'])
  assert.deepEqual(unsupportedKeywords({ type: 'object', additionalProperties: { type: 'string' } }),
    ['/: additionalProperties must be false, not a subschema'])
  assert.deepEqual(unsupportedKeywords({ type: 'array', items: [{ type: 'string' }] }),
    ['/: tuple-form items is not supported'])
  // Nesting is walked, so a keyword buried in a branch is still found.
  assert.deepEqual(
    unsupportedKeywords({ properties: { inner: { oneOf: [{ type: 'string', format: 'uri' }] } } }),
    ['/inner/: unsupported schema keyword: format'],
  )
})

test('an unknown contract id is a programming error, not a silent pass', () => {
  assert.throws(() => checkContract('model.completion.v2', {}), /Unknown adapter contract/)
})

test('a contract violation names the failing path and keyword', () => {
  assert.throws(
    () => assertContract('search.response', { result: { success: false, error: 'No code.' } }),
    error => {
      assert.ok(error instanceof AdapterContractError)
      assert.equal(error.code, 'adapter_contract_violated')
      assert.deepEqual(error.details.map(detail => [detail.path, detail.keyword]), [['/result', 'oneOf']])
      return true
    },
  )
})

test('the code taxonomy covers what the real search adapter can return', () => {
  // Every `failure(code, …)` literal in the shipped adapter must be declared,
  // or a caller branching on the taxonomy will miss a case.
  const declared = CONTRACT_DOCUMENT.errorCodes.search.codes
  for (const code of declared) assert.ok(isDeclaredCode(code), code)
  assert.ok(isRetryableCode('search_provider_error'))
  assert.equal(isRetryableCode('search_not_configured'), false)
  assert.equal(isDeclaredCode('quota_exceeded'), false)
})

test('search refuses an unusable request with a stable code and no side effects', async () => {
  const search = configuredWebSearch(MISSING, { env: {}, fetchImpl: refuse })
  for (const input of [{ query: '' }, { query: '   ' }, { query: 'x'.repeat(2001) }]) {
    const response = await search(searchRequest(input))
    assertContract('search.response', response)
    assert.equal(response.result.code, 'invalid_query')
    assert.equal(response.result.retryable, false)
  }
  // An unconfigured provider is also a refusal rather than an attempt, so the
  // Agent is told not to retry instead of hammering a missing credential.
  const unconfigured = await search(searchRequest({ query: 'momentum factor decay' }))
  assertContract('search.response', unconfigured)
  assert.equal(unconfigured.result.code, 'search_not_configured')
})

test('disabled search answers in contract instead of pretending to be broken', async () => {
  const search = configuredWebSearch(MISSING, {
    env: {}, fetchImpl: refuse, settingsOverride: { version: 1, provider: 'off' },
  })
  const response = await search(searchRequest({ query: 'factor timing' }))
  assertContract('search.response', response)
  assert.equal(response.result.code, 'search_disabled')
  assert.equal(response.result.retryable, false)
})

test('a Brave success conforms and never echoes the subscription key', async () => {
  let sentHeaders
  const search = configuredWebSearch(MISSING, {
    env: {},
    settingsOverride: { version: 1, provider: 'brave', brave_api_key: TEST_CREDENTIALS.searchApiKey },
    fetchImpl: async (url, options) => {
      sentHeaders = options.headers
      assert.equal(new URL(url).searchParams.get('count'), '3')
      return jsonReply({ web: { results: [
        { title: 'Paper', url: 'https://example.com/paper', description: 'Excerpt' },
        { title: 'Note', url: 'https://example.com/note' },
      ] } })
    },
  })
  const response = await search(searchRequest({ query: 'cross-sectional momentum', count: 3 }))
  assertContract('search.response', response)
  assert.equal(response.result.provider, 'brave')
  assert.equal(response.result.results.length, 2)
  // The key reaches the provider and nothing else.
  assert.equal(sentHeaders['X-Subscription-Token'], TEST_CREDENTIALS.searchApiKey)
  assertNoSecrets(response, SECRETS, 'the Brave search response')
})

test('a provider HTTP failure is reported by status alone', async () => {
  const search = configuredWebSearch(MISSING, {
    env: {},
    settingsOverride: { version: 1, provider: 'brave', brave_api_key: TEST_CREDENTIALS.searchApiKey },
    // A provider error body can repeat the request, headers included. The
    // adapter must not forward it.
    fetchImpl: async () => new Response(JSON.stringify({ message: `rejected key ${TEST_CREDENTIALS.searchApiKey}` }), { status: 403 }),
  })
  const response = await search(searchRequest({ query: 'quality minus junk' }))
  assertContract('search.response', response)
  assert.equal(response.result.code, 'search_provider_error')
  assert.match(response.result.error, /HTTP 403/)
  assertNoSecrets(response, SECRETS, 'the Brave failure response')
})

test('OpenRouter search bounds its sources and keeps the model key out of the result', async () => {
  const search = configuredWebSearch(MISSING, {
    env: {
      ALPHALAB_WEB_SEARCH_PROVIDER: 'openrouter',
      CONEXUS_MODEL_BASE_URL: 'https://openrouter.ai/api/v1',
      CONEXUS_MODEL_ID: 'vendor/model-a',
      CONEXUS_MODEL_API_KEY: TEST_CREDENTIALS.modelApiKey,
    },
    fetchImpl: async (_url, options) => {
      assert.equal(options.headers.Authorization, `Bearer ${TEST_CREDENTIALS.modelApiKey}`)
      return jsonReply({
        usage: { server_tool_use: { web_search_requests: 1 } },
        choices: [{ message: { annotations: [
          { type: 'url_citation', url_citation: { url: 'https://example.com/a', title: 'A', content: 'x' } },
          // Rejected: not http(s). A benchmark must not be able to reach a
          // non-web scheme through a search result.
          { type: 'url_citation', url_citation: { url: 'javascript:alert(1)', title: 'B' } },
        ] } }],
      })
    },
  })
  const response = await search(searchRequest({ query: 'value spread', count: 4 }))
  assertContract('search.response', response)
  assert.equal(response.result.provider, 'openrouter')
  assert.deepEqual(response.result.results.map(result => result.url), ['https://example.com/a'])
  assertNoSecrets(response, SECRETS, 'the OpenRouter search response')
})

test('a provider that answers without searching is not treated as evidence', async () => {
  const search = configuredWebSearch(MISSING, {
    env: {},
    settingsOverride: { version: 1, provider: 'openrouter' },
    fetchImpl: refuse,
  })
  // No active OpenRouter profile: refused before any request.
  const response = await search(searchRequest({ query: 'carry trade' }))
  assertContract('search.response', response)
  assert.equal(response.result.code, 'search_not_configured')
})

test('the model adapter refuses to start a run before a provider is configured', async () => {
  const complete = configuredCompletion(MISSING, () => refuse, {})
  await assert.rejects(
    () => complete({ messages: [{ role: 'user', content: 'go' }], signal: new AbortController().signal }),
    /configure a model before starting the Agent/,
  )
  assert.equal(await readActiveProvider(MISSING, {}), null)
})

test('the model adapter freezes provider selection for the lifetime of a run', async () => {
  let built = 0
  const env = {
    CONEXUS_MODEL_BASE_URL: 'https://api.example.com/v1',
    CONEXUS_MODEL_ID: 'vendor/model-a',
    CONEXUS_MODEL_API_KEY: TEST_CREDENTIALS.modelApiKey,
  }
  const requests = []
  const createModelCompletion = ({ model, apiKey }) => {
    built += 1
    assert.equal(apiKey, TEST_CREDENTIALS.modelApiKey)
    return async request => {
      requests.push(request)
      assertContract('model.completion.request', { ...request, signal: undefined })
      return assertContract('model.completion.response', { content: `answered by ${model}`, finish_reason: 'stop' })
    }
  }
  const complete = configuredCompletion(MISSING, createModelCompletion, env)

  const first = new AbortController().signal
  const request = {
    signal: first,
    messages: [{ role: 'user', content: 'Summarize.' }],
    tools: [{ type: 'function', function: { name: 'complete', parameters: { type: 'object' } } }],
    maxTokens: 512,
  }
  assertContract('model.completion.response', await complete(request))
  assertContract('model.completion.response', await complete(request))
  // One client per run signal: switching providers mid-run is impossible.
  assert.equal(built, 1)
  assertContract('model.completion.response', await complete({ ...request, signal: new AbortController().signal }))
  assert.equal(built, 2)

  // Conditionally optional tool fields survive a strict Responses API.
  assert.equal(requests[0].tools[0].function.strict, false)
  assert.equal(requests[0].model, 'vendor/model-a')
  assertNoSecrets(requests, SECRETS, 'the model completion request')
})

test('an explicit strict tool schema is preserved, not overwritten', async () => {
  const env = { CONEXUS_MODEL_BASE_URL: 'https://api.example.com/v1', CONEXUS_MODEL_ID: 'm', CONEXUS_MODEL_API_KEY: 'k' }
  let seen
  const complete = configuredCompletion(MISSING, () => async request => {
    seen = request
    return { content: '', finish_reason: 'stop' }
  }, env)
  await complete({
    signal: new AbortController().signal,
    messages: [{ role: 'user', content: 'go' }],
    tools: [
      { type: 'function', function: { name: 'strictly', strict: true, parameters: { type: 'object' } } },
      { type: 'custom', name: 'passthrough' },
    ],
  })
  assert.equal(seen.tools[0].function.strict, true)
  assert.deepEqual(seen.tools[1], { type: 'custom', name: 'passthrough' })
})

test('a resolved provider profile is safe to log or export only after redaction', async () => {
  const env = {
    CONEXUS_MODEL_BASE_URL: 'https://api.example.com/v1',
    CONEXUS_MODEL_ID: 'vendor/model-a',
    CONEXUS_MODEL_API_KEY: TEST_CREDENTIALS.modelApiKey,
  }
  const profile = await readActiveProvider(MISSING, env)
  // The profile is the adapter's own configuration and does hold the key: it
  // is the exported or logged copy that must not.
  assert.equal(profile.api_key, TEST_CREDENTIALS.modelApiKey)
  const exported = redact(profile)
  assert.equal(exported.api_key, REDACTED)
  assert.equal(exported.base_url, 'https://api.example.com/v1')
  assertNoSecrets(exported, SECRETS, 'the exported provider profile')
})

test('redaction removes credential shapes the adapters never name', () => {
  const diagnostic = redact({
    endpoint: `https://operator:${TEST_CREDENTIALS.modelApiKey}@api.example.com/v1`,
    header: `Authorization: Bearer ${TEST_CREDENTIALS.workspaceToken}`,
    sourceSha256: 'a'.repeat(64),
    nested: [{ brave_api_key: TEST_CREDENTIALS.searchApiKey }],
  })
  assertNoSecrets(diagnostic, SECRETS, 'a redacted diagnostic')
  assert.equal(diagnostic.sourceSha256, REDACTED)
  assert.equal(diagnostic.nested[0].brave_api_key, REDACTED)
})

test('assertNoSecrets refuses a fixture too short to be evidence', () => {
  assert.throws(() => assertNoSecrets({}, ['short']), /at least 8 characters/)
})

test('the model double enforces the completion contract in both directions', async () => {
  const complete = contractCompletion([
    toolTurn('use', { node_id: 'series-stats', capability: 'tool.invoke', input: { values: [1, 2] } }),
    textTurn('done'),
  ], { model: 'vendor/model-a' })

  const signal = new AbortController().signal
  assertContract('model.completion.response', await complete({ model: 'vendor/model-a', messages: [{ role: 'user', content: 'go' }], signal }))
  assertContract('model.completion.response', await complete({ model: 'vendor/model-a', messages: [{ role: 'user', content: 'go' }], signal }))
  assert.equal(complete.requests.length, 2)

  // A request the core would never send is rejected rather than answered.
  await assert.rejects(() => complete({ messages: [], signal }), AdapterContractError)
  // A model the host did not configure gets the core's own refusal code.
  await assert.rejects(
    () => complete({ model: 'vendor/model-b', messages: [{ role: 'user', content: 'go' }], signal }),
    error => error.code === 'model_not_allowed',
  )
})

test('a cancelled run reaches the model double as a cancellation, not a turn', async () => {
  const complete = contractCompletion([textTurn('unreachable')])
  const controller = new AbortController()
  controller.abort()
  await assert.rejects(() => complete({ messages: [{ role: 'user', content: 'go' }], signal: controller.signal }))
  assert.equal(complete.requests.length, 0)
})

test('the search double matches the real adapter refusal and cancellation shapes', async () => {
  const double = contractSearch({ sources: [{ url: 'https://example.com/a', title: 'A', snippet: 's' }] })
  assertContract('search.response', await double(searchRequest({ query: 'x' })))

  const invalid = await double(searchRequest({ query: '' }))
  const real = await configuredWebSearch(MISSING, { env: {}, fetchImpl: refuse })(searchRequest({ query: '' }))
  // Equivalent tool access means equivalent refusals: a benchmark Agent must
  // see the same code and the same retry advice from either backend.
  assert.deepEqual(
    { code: invalid.result.code, retryable: invalid.result.retryable },
    { code: real.result.code, retryable: real.result.retryable },
  )

  // A search already in flight when the Run is cancelled.
  const slow = contractSearch({ delayMs: 60000 })
  const controller = new AbortController()
  const pending = slow({ input: { query: 'slow' }, signal: controller.signal })
  controller.abort()
  const cancelled = await pending
  assertContract('search.response', cancelled)
  assert.equal(cancelled.result.code, 'search_request_failed')
  assert.equal(cancelled.result.retryable, true)
})

test('the financial tool double gates execution and reports async work in contract', async () => {
  const tool = contractFinancialTool({ steps: 2 })

  const ungated = await tool({ command: 'backtest.run', args: { strategy_id: 'main' } })
  assertContract('financial.tool.response', ungated)
  assert.equal(ungated.success, false)
  assert.match(ungated.error, /confirm_python_execution=true/)

  // A confirmation smuggled into `args` does not authorize anything.
  const smuggled = await tool({ command: 'backtest.run', args: { confirm_python_execution: true } })
  assert.equal(smuggled.success, false)
  assert.match(smuggled.error, /Invalid tool input: \/args not/)

  const started = await tool({ command: 'backtest.run', confirm_python_execution: true })
  assertContract('financial.tool.response', started)
  assert.equal(started.status, 'queued')

  const polled = await tool({ command: 'backtest.status', args: { job_id: started.job_id } })
  assert.equal(polled.status, 'running')
  const waited = await tool({ command: 'backtest.wait', args: { job_id: started.job_id } })
  assert.equal(waited.status, 'succeeded')
  assert.equal(waited.timed_out, undefined)

  const summary = await tool({ command: 'backtest.summary', args: { job_id: started.job_id } })
  assertContract('financial.tool.response', summary)
  assert.equal(summary.result.metrics.execution_reliable, true)

  for (const bad of [{ command: 'backtest.summary' }, { command: 'nope.command' }, { command: 'Backtest.Run' }]) {
    const refused = await tool(bad)
    assertContract('financial.tool.response', refused)
    assert.equal(refused.success, false)
    assert.ok(refused.error.length <= 400)
  }
})

test('an unfinished wait is a successful call reporting an unfinished job', async () => {
  const tool = contractFinancialTool({ steps: 5 })
  const started = await tool({ command: 'backtest.run', confirm_python_execution: true })
  const waited = await tool({ command: 'backtest.wait', args: { job_id: started.job_id } })
  assert.equal(waited.success, true)
  assert.equal(waited.status, 'succeeded')

  const slow = contractFinancialTool({ steps: 2 })
  const job = await slow({ command: 'backtest.run', confirm_python_execution: true })
  await slow({ command: 'backtest.status', args: { job_id: job.job_id } })
  const summary = await slow({ command: 'backtest.summary', args: { job_id: job.job_id } })
  assert.equal(summary.success, false)
  assert.match(summary.error, /has not finished/)
})

test('the state store refuses a snapshot the Host would not read back', async () => {
  const store = memoryStateStore()
  store.state.runs.push({ id: 'run-1', status: 'queued', tokenHash: 'abc', sequence: 0, events: [] })
  assertContract('state.snapshot', await store.save())

  store.state.runs.push({ id: 'run-2', status: 'finished' })
  await assert.rejects(() => store.save(), AdapterContractError)
  assert.equal(store.writes.length, 1)
})

test('a substitute adapter is equivalent only when it offers no less', () => {
  const equivalent = substituteProfile(HOST_PROFILE)
  assert.deepEqual(profileShortfall(HOST_PROFILE, equivalent), [])
  assertEquivalentProfiles(HOST_PROFILE, equivalent)

  const narrowed = substituteProfile(HOST_PROFILE, { capabilityIds: [] })
  assert.deepEqual(profileShortfall(HOST_PROFILE, narrowed), ['capabilityIds: web.search'])
  assert.throws(() => assertEquivalentProfiles(HOST_PROFILE, narrowed), /does not offer capabilityIds: web\.search/)

  // Offering more is allowed: it cannot make a benchmark task unreachable.
  const wider = substituteProfile(HOST_PROFILE, { capabilityIds: ['web.search', 'files.read'] })
  assert.deepEqual(profileShortfall(HOST_PROFILE, wider), [])
})

test('withContracts checks a wrapped adapter on every call', async () => {
  const checked = withContracts(
    async () => ({ result: { success: false, error: 'missing code' } }),
    { request: 'model.completion.request', response: 'capability.response' },
  )
  await assert.rejects(
    () => checked({ messages: [{ role: 'user', content: 'go' }] }),
    error => error.contractId === 'capability.response',
  )
})
