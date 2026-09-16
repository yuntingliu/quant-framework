import { readFile } from 'node:fs/promises'

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

/** Freeze provider selection on the run's signal; switching affects new runs. */
export function configuredCompletion(path, createModelCompletion, env = process.env) {
  const completions = new WeakMap()
  return async request => {
    let completion = completions.get(request.signal)
    if (!completion) {
      const profile = await readActiveProvider(path, env)
      if (!profile) throw new Error('Open Model providers and configure a model before starting the Agent.')
      const complete = createModelCompletion({ baseUrl: profile.base_url, model: profile.model, apiKey: profile.api_key })
      completion = input => complete({ ...input, model: profile.model })
      completions.set(request.signal, completion)
    }
    return completion(request)
  }
}
