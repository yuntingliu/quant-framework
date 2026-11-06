import assert from 'node:assert/strict'
import { test } from 'node:test'
import { configuredWebSearch } from '../integrations/conexus/web-search.mjs'
import { mkdtemp, readFile, rm, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

const path = '/nonexistent/alphalab-search-provider.json'
const profileEnv = {
  ALPHALAB_WEB_SEARCH_PROVIDER: 'openrouter',
  CONEXUS_MODEL_BASE_URL: 'https://openrouter.ai/api/v1',
  CONEXUS_MODEL_ID: 'test-model',
  CONEXUS_MODEL_API_KEY: 'fixture-model-key',
}
const request = input => ({ input, signal: new AbortController().signal })
const requestWithSignal = (input, signal) => ({ input, signal })
const reply = data => new Response(JSON.stringify(data), { headers: { 'Content-Type': 'application/json' } })

test('missing search setup is non-retryable and makes no network request', async () => {
  const search = configuredWebSearch(path, { env: {}, fetchImpl: () => { throw new Error('Unexpected request') } })
  const { result } = await search(request({ query: 'query' }))
  assert.equal(result.code, 'search_not_configured')
  assert.equal(result.retryable, false)
})

test('OpenRouter search uses the active private profile and bounds searches and results', async () => {
  let body
  const search = configuredWebSearch(path, { env: profileEnv, fetchImpl: async (url, options) => {
    assert.equal(url, 'https://openrouter.ai/api/v1/chat/completions')
    assert.equal(options.headers.Authorization, 'Bearer fixture-model-key')
    body = JSON.parse(options.body)
    return reply({ usage: { server_tool_use: { web_search_requests: 1 } }, choices: [{ message: { annotations: [
      { type: 'url_citation', url_citation: { url: 'https://example.com/paper', title: 'Paper', content: 'Source excerpt' } },
      { type: 'url_citation', url_citation: { url: 'https://example.com/paper', title: 'Duplicate' } },
      { type: 'url_citation', url_citation: { url: 'javascript:bad', title: 'Reject' } },
    ] } }] })
  } })
  const { result } = await search(request({ query: 'original query', count: 999 }))
  assert.equal(result.success, true)
  assert.equal(result.provider, 'openrouter')
  assert.deepEqual(result.results, [{ url: 'https://example.com/paper', title: 'Paper', snippet: 'Source excerpt' }])
  assert.equal(JSON.stringify(result).includes('fixture-model-key'), false)
  assert.equal(body.model, 'test-model')
  assert.equal(body.max_tool_calls, 1)
  assert.equal(body.tools[0].parameters.max_uses, 1)
  assert.equal(body.tools[0].parameters.max_results, 10)
  assert.equal(body.tools[0].parameters.max_total_results, 10)
  assert.equal(body.messages[1].content, 'original query')
  assert.equal(body.messages.length, 2)
})

test('OpenRouter search refuses another endpoint without transmitting its key', async () => {
  const search = configuredWebSearch(path, {
    env: { ...profileEnv, CONEXUS_MODEL_BASE_URL: 'https://another.example/v1' },
    fetchImpl: () => { throw new Error('Unexpected request') },
  })
  assert.equal((await search(request({ query: 'query' }))).result.code, 'search_not_configured')
})

test('search does not return provider error bodies or credentials', async () => {
  const search = configuredWebSearch(path, { env: profileEnv, fetchImpl: async () => new Response('sensitive provider body', { status: 401 }) })
  const { result } = await search(request({ query: 'query' }))
  assert.equal(result.success, false)
  assert.equal(result.retryable, false)
  assert.match(result.error, /HTTP 401/)
  assert.equal(JSON.stringify(result).includes('sensitive provider body'), false)
})

test('answers without search citations or with zero executed searches are not search evidence', async () => {
  for (const payload of [{ choices: [{ message: { content: 'Unverified answer' } }] }, { usage: { server_tool_use: { web_search_requests: 0 } }, choices: [] }]) {
    const search = configuredWebSearch(path, { env: profileEnv, fetchImpl: async () => reply(payload) })
    assert.equal((await search(request({ query: 'query' }))).result.code, 'search_no_sources')
  }
})

test('Brave configuration remains supported independently of model selection', async () => {
  const search = configuredWebSearch(path, { env: { BRAVE_SEARCH_API_KEY: 'fixture-brave-key' }, fetchImpl: async (url, options) => {
    assert.equal(url.hostname, 'api.search.brave.com')
    assert.equal(url.searchParams.get('q'), 'query')
    assert.equal(url.searchParams.get('freshness'), 'pw')
    assert.equal(options.headers['X-Subscription-Token'], 'fixture-brave-key')
    return reply({ web: { results: [{ title: 'Source', url: 'https://example.com', description: 'Excerpt' }] } })
  } })
  assert.deepEqual((await search(request({ query: 'query', freshness: 'pw' }))).result.results, [{ title: 'Source', url: 'https://example.com', snippet: 'Excerpt' }])
})

test('oversized queries are rejected before model or search traffic', async () => {
  const search = configuredWebSearch(path, { env: profileEnv, fetchImpl: () => { throw new Error('Unexpected request') } })
  assert.equal((await search(request({ query: 'a'.repeat(2001) }))).result.code, 'invalid_query')
})

test('legacy v1 search settings remain supported and can use an inactive model profile', async () => {
  const root = await mkdtemp(join(tmpdir(), 'alphalab-search-'))
  const providers = join(root, 'model-providers.json')
  const settings = join(root, 'search-providers.json')
  let calls = 0
  await writeFile(providers, JSON.stringify({ active_id: 'local', profiles: [
    { id: 'local', base_url: 'http://localhost:12345/v1', model: 'local' },
    { id: 'search', base_url: 'https://openrouter.ai/api/v1', model: 'search-model', api_key: 'fixture-search-key' },
  ] }))
  const search = configuredWebSearch(providers, { env: profileEnv, fetchImpl: async (_url, options) => {
    calls += 1
    assert.equal(options.headers.Authorization, 'Bearer fixture-search-key')
    assert.equal(JSON.parse(options.body).model, 'search-model')
    return reply({ choices: [{ message: { annotations: [{ type: 'url_citation', url_citation: { url: 'https://example.com/source' } }] } }] })
  } })
  try {
    await writeFile(settings, JSON.stringify({ version: 1, provider: 'off' }))
    assert.equal((await search(request({ query: 'query' }))).result.code, 'search_disabled')
    assert.equal(calls, 0)
    await writeFile(settings, JSON.stringify({ version: 1, provider: 'openrouter', model_profile_id: 'search' }))
    assert.equal((await search(request({ query: 'query' }))).result.success, true)
    assert.equal(calls, 1)
  } finally { await rm(root, { recursive: true, force: true }) }
})

test('named v2 search profiles use the active saved profile', async () => {
  const root = await mkdtemp(join(tmpdir(), 'alphalab-search-v2-'))
  const providers = join(root, 'model-providers.json')
  const settings = join(root, 'search-providers.json')
  await writeFile(providers, JSON.stringify({ active_id: 'local', profiles: [
    { id: 'local', base_url: 'http://localhost:12345/v1', model: 'local' },
    { id: 'search', base_url: 'https://openrouter.ai/api/v1', model: 'search-model', api_key: 'fixture-search-key' },
  ] }))
  await writeFile(settings, JSON.stringify({ version: 2, active_id: 'named-search', profiles: [
    { id: 'named-search', name: 'Named Search', provider: 'openrouter', model_profile_id: 'search', brave_api_key: '' },
    { id: 'off-profile', name: 'Off', provider: 'off', model_profile_id: null, brave_api_key: '' },
  ] }))
  try {
    const search = configuredWebSearch(providers, { env: profileEnv, fetchImpl: async (_url, options) => {
      assert.equal(options.headers.Authorization, 'Bearer fixture-search-key')
      assert.equal(JSON.parse(options.body).model, 'search-model')
      return reply({ choices: [{ message: { annotations: [{ type: 'url_citation', url_citation: { url: 'https://example.com/source' } }] } }] })
    } })
    assert.equal((await search(request({ query: 'query' }))).result.success, true)
  } finally { await rm(root, { recursive: true, force: true }) }
})

test('explicit saved OpenRouter model references never fall back to unrelated credentials', async () => {
  const root = await mkdtemp(join(tmpdir(), 'alphalab-search-explicit-'))
  const providers = join(root, 'model-providers.json')
  const settings = join(root, 'search-providers.json')
  await writeFile(providers, JSON.stringify({ active_id: 'active', profiles: [
    { id: 'active', base_url: 'https://openrouter.ai/api/v1', model: 'active-model', api_key: 'active-key' },
  ] }))
  await writeFile(settings, JSON.stringify({ version: 2, active_id: 'saved', profiles: [
    { id: 'saved', name: 'Saved', provider: 'openrouter', model_profile_id: 'missing', brave_api_key: '' },
  ] }))
  try {
    const search = configuredWebSearch(providers, {
      env: profileEnv,
      fetchImpl: () => { throw new Error('Unexpected request') },
    })
    assert.equal((await search(request({ query: 'query' }))).result.code, 'search_not_configured')
  } finally { await rm(root, { recursive: true, force: true }) }
})

test('saved profile changes do not affect calls that reuse the same operation signal', async () => {
  const root = await mkdtemp(join(tmpdir(), 'alphalab-search-freeze-'))
  const providers = join(root, 'model-providers.json')
  const settings = join(root, 'search-providers.json')
  const authorizations = []
  await writeFile(providers, JSON.stringify({ active_id: 'local', profiles: [
    { id: 'local', base_url: 'http://localhost:12345/v1', model: 'local' },
    { id: 'search', base_url: 'https://openrouter.ai/api/v1', model: 'search-model', api_key: 'fixture-search-key' },
  ] }))
  await writeFile(settings, JSON.stringify({ version: 2, active_id: 'saved', profiles: [
    { id: 'saved', name: 'Saved', provider: 'openrouter', model_profile_id: 'search', brave_api_key: '' },
  ] }))
  const controller = new AbortController()
  const search = configuredWebSearch(providers, { fetchImpl: async (_url, options) => {
    authorizations.push(options.headers.Authorization)
    return reply({ choices: [{ message: { annotations: [{ type: 'url_citation', url_citation: { url: 'https://example.com/source' } }] } }] })
  } })
  try {
    assert.equal((await search(requestWithSignal({ query: 'first query' }, controller.signal))).result.success, true)
    await writeFile(settings, JSON.stringify({ version: 2, active_id: 'off-profile', profiles: [
      { id: 'saved', name: 'Saved', provider: 'openrouter', model_profile_id: 'search', brave_api_key: '' },
      { id: 'off-profile', name: 'Off', provider: 'off', model_profile_id: null, brave_api_key: '' },
    ] }))
    assert.equal((await search(requestWithSignal({ query: 'second query' }, controller.signal))).result.success, true)
    assert.deepEqual(authorizations, ['Bearer fixture-search-key', 'Bearer fixture-search-key'])
    assert.equal((await search(request({ query: 'third query' }))).result.code, 'search_disabled')
  } finally { await rm(root, { recursive: true, force: true }) }
})

test('an unsaved search test override does not change the saved selection', async () => {
  const root = await mkdtemp(join(tmpdir(), 'alphalab-search-override-'))
  const settings = join(root, 'search-providers.json')
  const value = JSON.stringify({ version: 2, active_id: 'stored', profiles: [
    { id: 'stored', name: 'Stored', provider: 'brave', model_profile_id: null, brave_api_key: 'fixture-stored-key' },
  ] })
  await writeFile(settings, value)
  try {
    const search = configuredWebSearch(join(root, 'model-providers.json'), { settingsOverride: { provider: 'off' } })
    assert.equal((await search(request({ query: 'query' }))).result.code, 'search_disabled')
    assert.equal(await readFile(settings, 'utf8'), value)
  } finally { await rm(root, { recursive: true, force: true }) }
})

test('a saved cleared Brave key never falls back to the environment or another profile', async () => {
  const root = await mkdtemp(join(tmpdir(), 'alphalab-search-cleared-'))
  await writeFile(join(root, 'search-providers.json'), JSON.stringify({ version: 2, active_id: 'cleared', profiles: [
    { id: 'cleared', name: 'Cleared', provider: 'brave', brave_api_key: '' },
    { id: 'other', name: 'Other', provider: 'brave', brave_api_key: 'other-key' },
  ] }))
  try {
    const search = configuredWebSearch(join(root, 'model-providers.json'), {
      env: { BRAVE_SEARCH_API_KEY: 'environment-key' },
      fetchImpl: () => { throw new Error('Unexpected request') },
    })
    assert.equal((await search(request({ query: 'query' }))).result.code, 'search_not_configured')
  } finally { await rm(root, { recursive: true, force: true }) }
})

test('invalid saved settings fail closed without modifying the file or borrowing environment keys', async () => {
  const root = await mkdtemp(join(tmpdir(), 'alphalab-search-invalid-'))
  const settings = join(root, 'search-providers.json')
  const profile = { id: 'a', name: 'A', provider: 'brave', brave_api_key: 'private-key' }
  const invalidSettings = [
    null, [], { version: 999 }, { version: true, provider: 'off' },
    { version: 2, profiles: [profile], active_id: 'missing' },
    { version: 2, profiles: [profile], active_id: '' },
    { version: 2, profiles: [profile, profile], active_id: 'a' },
    { version: 2, profiles: Array(21).fill(profile) },
    { version: 1, provider: 'brave', model_profile_id: [] },
    { version: 1, provider: 'brave', brave_api_key: { secret: 'private-key' } },
    { version: 1, provider: 'brave', brave_api_key: null },
  ]
  try {
    const search = configuredWebSearch(join(root, 'model-providers.json'), {
      env: profileEnv, fetchImpl: () => { throw new Error('Unexpected request') },
    })
    for (const invalid of invalidSettings) {
      const raw = JSON.stringify(invalid)
      await writeFile(settings, raw)
      const { result } = await search(request({ query: 'query' }))
      assert.equal(result.code, 'search_request_failed')
      assert.equal(result.retryable, false)
      assert.equal(JSON.stringify(result).includes('private-key'), false)
      assert.equal(await readFile(settings, 'utf8'), raw)
    }
  } finally { await rm(root, { recursive: true, force: true }) }
})

test('selected OpenRouter profiles require their own valid endpoint, model and key', async () => {
  const root = await mkdtemp(join(tmpdir(), 'alphalab-search-model-validation-'))
  const providers = join(root, 'model-providers.json')
  const base = { id: 'selected', base_url: 'https://openrouter.ai/api/v1', model: 'model', api_key: 'selected-key' }
  await writeFile(join(root, 'search-providers.json'), JSON.stringify({ version: 2, active_id: 'a', profiles: [
    { id: 'a', name: 'A', provider: 'openrouter', model_profile_id: 'selected' },
  ] }))
  try {
    const search = configuredWebSearch(providers, {
      env: profileEnv, fetchImpl: () => { throw new Error('Unexpected request') },
    })
    for (const invalid of [{ ...base, base_url: 'https://other.example/v1' }, { ...base, model: '' }, { ...base, api_key: '' }]) {
      await writeFile(providers, JSON.stringify({ active_id: null, profiles: [invalid] }))
      assert.equal((await search(request({ query: 'query' }))).result.code, 'search_not_configured')
    }
  } finally { await rm(root, { recursive: true, force: true }) }
})

test('parallel Conexus capability calls capture one profile and new sessions see rotated credentials', async () => {
  const root = await mkdtemp(join(tmpdir(), 'alphalab-search-concurrent-'))
  const providers = join(root, 'model-providers.json')
  const save = key => writeFile(join(root, 'search-providers.json'), JSON.stringify({ version: 2, active_id: 'a', profiles: [
    { id: 'a', name: 'A', provider: 'brave', brave_api_key: key },
  ] }))
  const tokens = []
  const search = configuredWebSearch(providers, { env: {}, fetchImpl: async (_url, options) => {
    tokens.push(options.headers['X-Subscription-Token'])
    return reply({ web: { results: [{ url: 'https://example.com', title: 'Source' }] } })
  } })
  const signal = new AbortController().signal
  const capabilityCall = signal => ({ runId: 'run', nodeId: 'runtime:web-search', capability: 'web.search',
    ownerNodeId: 'agent', workspace: {}, input: { query: 'query' }, signal })
  try {
    await save('first-key')
    const results = await Promise.all([search(capabilityCall(signal)), search(capabilityCall(signal))])
    assert.ok(results.every(value => value.result.success))
    await save('second-key')
    await search(capabilityCall(signal))
    await search(capabilityCall(new AbortController().signal))
    await search({ input: { query: 'query' } })
    assert.deepEqual(tokens, ['first-key', 'first-key', 'first-key', 'second-key', 'second-key'])
    assert.equal(JSON.stringify(results).includes('first-key'), false)
  } finally { await rm(root, { recursive: true, force: true }) }
})

test('legacy environment Brave search remains compatible with a saved or cleared default profile', async () => {
  const root = await mkdtemp(join(tmpdir(), 'alphalab-search-legacy-env-'))
  const providers = join(root, 'model-providers.json')
  const settings = join(root, 'search-providers.json')
  const env = { ALPHALAB_WEB_SEARCH_PROVIDER: 'brave', BRAVE_SEARCH_API_KEY: 'fixture-environment-key' }
  const keys = []
  const search = configuredWebSearch(providers, { env, fetchImpl: async (_url, options) => {
    keys.push(options.headers['X-Subscription-Token'])
    return reply({ web: { results: [{ title: 'Source', url: 'https://example.com/source' }] } })
  } })
  const save = key => writeFile(settings, JSON.stringify({ version: 2, active_id: 'default', profiles: [
    { id: 'default', name: 'Default', provider: 'brave', model_profile_id: null, brave_api_key: key },
  ] }))
  try {
    const environmentResult = await search(request({ query: 'query' }))
    assert.equal(environmentResult.result.success, true)
    assert.equal(JSON.stringify(environmentResult).includes('fixture-environment-key'), false)
    await save('fixture-environment-key')
    env.BRAVE_SEARCH_API_KEY = 'fixture-rotated-key'
    assert.equal((await search(request({ query: 'query' }))).result.success, true)
    await save('')
    const before = await readFile(settings, 'utf8')
    assert.equal((await search(request({ query: 'query' }))).result.code, 'search_not_configured')
    assert.equal(await readFile(settings, 'utf8'), before)
    assert.deepEqual(keys, ['fixture-environment-key', 'fixture-environment-key'])
  } finally { await rm(root, { recursive: true, force: true }) }
})

test('unsaved Brave probe overrides never borrow environment credentials for an absent or cleared key', async () => {
  const env = { ALPHALAB_WEB_SEARCH_PROVIDER: 'brave', BRAVE_SEARCH_API_KEY: 'fixture-environment-key' }
  for (const override of [{ provider: 'brave' }, { provider: 'brave', brave_api_key: '' }]) {
    const search = configuredWebSearch(path, { env, settingsOverride: override,
      fetchImpl: () => { throw new Error('Unexpected request') },
    })
    const { result } = await search(request({ query: 'query' }))
    assert.equal(result.code, 'search_not_configured')
    assert.equal(JSON.stringify(result).includes('fixture-environment-key'), false)
  }
})
