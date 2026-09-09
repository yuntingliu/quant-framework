import { HostedRuntimeError, parseOpenAiCompletion } from '@conexus/runtime-core'

/** An OpenAI-compatible endpoint supplied by the operator, including a local model. */
export function createModelCompletion({ baseUrl, model, apiKey = '', timeoutMs = 120000, fetchImpl = fetch }) {
  const url = new URL(baseUrl)
  const loopback = ['localhost', '127.0.0.1', '[::1]'].includes(url.hostname)
  if (url.username || url.password || (url.protocol !== 'https:' && !(url.protocol === 'http:' && loopback))) {
    throw new Error('Model endpoint must use HTTPS or loopback HTTP, without credentials in its URL.')
  }
  if (!model?.trim()) throw new Error('Configure a model identifier.')
  if (!loopback && !apiKey.trim()) throw new Error('A remote model endpoint requires an API key.')
  url.pathname = `${url.pathname.replace(/\/+$/, '')}/chat/completions`
  return async request => {
    if (request.model !== model) throw new HostedRuntimeError('The requested model is not configured on this host.', 'model_not_allowed')
    const response = await fetchImpl(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...(apiKey ? { Authorization: `Bearer ${apiKey}` } : {}) },
      body: JSON.stringify({
        model, messages: request.messages, stream: false,
        ...(request.tools?.length ? { tools: request.tools } : {}),
        ...(request.toolChoice ? { tool_choice: request.toolChoice } : {}),
        ...(request.maxTokens ? { max_tokens: request.maxTokens } : {}),
      }),
      signal: AbortSignal.any([request.signal, AbortSignal.timeout(timeoutMs)]),
    })
    // Provider error bodies can contain credentials or request details; expose only the status.
    if (!response.ok) {
      await response.body?.cancel()
      throw new HostedRuntimeError(`Model endpoint returned HTTP ${response.status}.`, 'model_provider_error')
    }
    const chunks = []
    let bytes = 0
    for await (const chunk of response.body) {
      bytes += chunk.length
      if (bytes > 5 * 1024 * 1024) throw new HostedRuntimeError('Model response exceeds 5 MiB.', 'invalid_model_response')
      chunks.push(chunk)
    }
    return parseOpenAiCompletion(JSON.parse(Buffer.concat(chunks).toString('utf8')))
  }
}
