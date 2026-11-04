// Root-pinned adapter over the supported Conexus completion/fetch seam.
// No credentials, endpoint, messages or provider response text enter the ledger.
import { createHash } from 'node:crypto'
import { createModelCompletion } from '../../../build/conexus/apps/local-host/src/index.mjs'
import { readActiveProvider } from '../model-providers.mjs'
import { CollaborationError } from './contracts.mjs'

const digest = value => createHash('sha256').update(JSON.stringify(value)).digest('hex')
const copy = value => structuredClone(value)

export async function pinConfiguredModelProvider({ settingsPath, env = process.env, ...options }) {
  const profile = await readActiveProvider(settingsPath, env)
  if (!profile) throw new CollaborationError('MODEL_NOT_CONFIGURED')
  return createPinnedModelProvider({ ...options, profile })
}

/**
 * A hard-cap adapter must attest its input bound and endpoint output-cap support.
 * Generic OpenAI-compatible endpoints default to accounting-only. The local
 * acceptance endpoint has an explicit bounded synthetic token contract; it is
 * not evidence that an arbitrary external endpoint honors these bounds.
 */
export function createPinnedModelProvider({ profile, pricing = null, providerId = 'openai-compatible',
  boundsId = null, inputTokenBound, supportsOutputCap = false, fetchImpl = fetch }) {
  const pinned = copy(profile), price = copy(pricing)
  const configuration = {
    provider_id: providerId, model_id: pinned.model, profile_id: pinned.id ?? 'environment',
    configuration_id: digest({ endpoint: pinned.base_url, model: pinned.model, profile: pinned.id ?? null }),
    bounds_id: boundsId, supports_output_cap: supportsOutputCap, pricing: price,
  }
  return Object.freeze({
    get configuration() { return copy(configuration) },
    boundInput: request => inputTokenBound ? inputTokenBound(request) : null,
    async complete({ messages, tools, signal, maxTokens }) {
      let usage = null
      // A separate capture closure for every request prevents root/child or late
      // response races from assigning usage to the wrong reservation.
      const completion = createModelCompletion({ baseUrl: pinned.base_url, model: pinned.model,
        apiKey: pinned.api_key ?? '', fetchImpl: async (url, init) => {
          const response = await fetchImpl(url, init)
          const chunks = []
          let bytes = 0
          for await (const chunk of response.body ?? []) {
            bytes += chunk.length
            if (bytes > 5 * 1024 * 1024) throw new CollaborationError('INVALID_MODEL_RESPONSE')
            chunks.push(chunk)
          }
          const body = Buffer.concat(chunks)
          try {
            const raw = JSON.parse(body.toString('utf8')).usage
            if (raw && typeof raw === 'object') usage = {
              input_tokens: raw.prompt_tokens, output_tokens: raw.completion_tokens, total_tokens: raw.total_tokens,
              cached_input_tokens: raw.prompt_tokens_details?.cached_tokens ?? raw.prompt_cache_hit_tokens,
              reasoning_output_tokens: raw.completion_tokens_details?.reasoning_tokens,
              // Optional adapter extension: integer micro-units, never a float.
              cost_micros: raw.cost_micros, currency: raw.currency,
            }
          } catch { /* The canonical completion parser owns malformed bodies. */ }
          return new Response(body, { status: response.status, headers: { 'Content-Type': 'application/json' } })
        } })
      try {
        const response = await completion({ model: pinned.model, messages, signal, maxTokens,
          tools: tools?.map(tool => tool.type === 'function'
            ? { ...tool, function: { strict: false, ...tool.function } } : tool) })
        return { response, usage }
      } catch {
        throw Object.assign(new CollaborationError('MODEL_FAILED'), { usage })
      }
    },
  })
}
