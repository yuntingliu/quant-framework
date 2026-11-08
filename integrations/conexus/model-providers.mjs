import { readFile } from 'node:fs/promises'

/** Guard SQLite-backed provider results, including URL-encoded reflected keys. */
export function containsProviderCredential(payload, credentials) {
  const secrets = credentials.filter(value => typeof value === 'string' && value)
  if (!secrets.length) return false
  const pending = [payload]
  let nodes = 0
  while (pending.length) {
    if (++nodes > 8192) return true
    let value = pending.pop()
    if (value && typeof value === 'object') pending.push(...Object.keys(value), ...Object.values(value))
    else if (typeof value === 'string') {
      for (let depth = 0; depth <= 5; depth++) {
        if (secrets.some(secret => value.includes(secret))) return true
        // Decode valid escape runs with UTF-8 replacement, preserving literal
        // percent characters. A malformed escape cannot hide another valid run.
        const decoded = value.replace(/(?:%[0-9a-f]{2})+/gi, part => Buffer.from(part.replaceAll('%', ''), 'hex').toString('utf8'))
        if (decoded === value) break
        if (depth === 5) return true
        value = decoded
      }
    }
  }
  return false
}

export async function readActiveProvider(path, env = process.env) {
  let settings = { profiles: [], active_id: null }
  try { settings = JSON.parse(await readFile(path, 'utf8')) } catch (error) {
    if (error.code !== 'ENOENT') throw new Error('Local model settings could not be read.')
  }
  const saved = settings.profiles.find(row => row.id === settings.active_id)
  const profile = saved || {
    base_url: env.CONEXUS_MODEL_BASE_URL?.trim(),
    model: env.CONEXUS_MODEL_ID?.trim(),
    api_key: env.CONEXUS_MODEL_API_KEY?.trim() || '',
  }
  return profile.base_url && profile.model ? profile : null
}

/** Private resolution only: the snapshot is supplied by the SQLite composition. */
export function providerFromSnapshot(snapshot, env = {}, profileId = null) {
  const settings = snapshot.documents.models
  const choice = snapshot.selections.models
  let profile
  if (profileId !== null) profile = settings.profiles.find(row => row.id === profileId)
  else if (choice.kind === 'none') return null
  else if (choice.kind === 'profile') profile = settings.profiles.find(row => row.id === choice.id)
  else if (choice.kind === 'environment') profile = {
    base_url: env.CONEXUS_MODEL_BASE_URL, model: env.CONEXUS_MODEL_ID, api_key: env.CONEXUS_MODEL_API_KEY,
  }
  if (!profile || typeof profile.base_url !== 'string' || typeof profile.model !== 'string'
    || (profile.api_key !== undefined && typeof profile.api_key !== 'string')) return null
  const base = profile.base_url.trim().replace(/\/+$/, ''), model = profile.model.trim(), key = profile.api_key?.trim() || ''
  try {
    const url = new URL(base), local = ['localhost', '127.0.0.1', '[::1]'].includes(url.hostname)
    if (!model || !url.hostname || url.username || url.password || url.search || url.hash
      || (url.protocol !== 'https:' && !(url.protocol === 'http:' && local)) || (!local && !key)) return null
  } catch { return null }
  return { base_url: base, model, api_key: key }
}

/** Freeze provider selection on the run's signal; switching affects new runs. */
export function configuredCompletion(path, createModelCompletion, env = process.env, { resolveProvider } = {}) {
  const completions = new WeakMap()
  const create = profile => {
    if (!profile) throw new Error('Open Model providers and configure a model before starting the Agent.')
    const complete = createModelCompletion({ baseUrl: profile.base_url, model: profile.model, apiKey: profile.api_key })
    return input => complete({ ...input, model: profile.model,
      ...(input.tools ? { tools: input.tools.map(tool => tool.type === 'function'
        ? { ...tool, function: { strict: false, ...tool.function } } : tool) } : {}),
    })
  }
  return async request => {
    let completion = completions.get(request.signal)
    if (!completion) {
      // These tools contain conditional optional fields (for example,
      // complete.blocker only applies to blocked runs). Preserve those semantics
      // when a provider routes Chat Completions through a strict Responses API.
      // Injected selection is cached before the read starts, so parallel first
      // calls on one run cannot capture different database generations.
      completion = resolveProvider
        ? Promise.resolve().then(() => resolveProvider(request.signal)).then(create)
        : create(await readActiveProvider(path, env))
      completions.set(request.signal, completion)
    }
    return (await completion)(request)
  }
}
