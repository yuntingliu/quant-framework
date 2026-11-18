import { readFile } from 'node:fs/promises'
import { budgetedCompletion } from './run-budget.mjs'

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
export function configuredCompletion(path, createModelCompletion, env = process.env, { trace } = {}) {
  const completions = new WeakMap()
  const configured = async request => {
    let completion = completions.get(request.signal)
    if (!completion) {
      const profile = await readActiveProvider(path, env)
      if (!profile) throw new Error('Open Model providers and configure a model before starting the Agent.')
      const meter = trace?.meter({ model: profile.model, baseUrl: profile.base_url })
      const complete = createModelCompletion({ baseUrl: profile.base_url, model: profile.model, apiKey: profile.api_key,
        ...(meter ? { fetchImpl: meter.fetch } : {}) })
      // These tools contain conditional optional fields (for example,
      // complete.blocker only applies to blocked runs). Preserve those semantics
      // when a provider routes Chat Completions through a strict Responses API.
      completion = ({ runBudget, ...input }) => complete({ ...input, model: profile.model,
        ...(input.tools ? { tools: input.tools.map(tool => tool.type === 'function'
          ? { ...tool, function: { strict: false, ...tool.function } } : tool) } : {}),
      })
      if (meter) completion = meter.wrap(completion)
      completion = budgetedCompletion(completion)
      completions.set(request.signal, completion)
    }
    return completion(request)
  }
  // Conexus reports tool and request lifecycle events through `complete.trace`.
  if (trace) configured.trace = trace.event
  return configured
}
