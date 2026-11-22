import { appendFile, mkdir, readFile, readdir } from 'node:fs/promises'
import { createHash } from 'node:crypto'
import { execFileSync } from 'node:child_process'
import { join, resolve } from 'node:path'

// Opt-in cost and latency trace for issue #22. One JSONL file per Harness
// request: a run.meta header, then model, tool and request rows. Rows carry
// sizes, timings and digests only; prompts, tool payloads and keys stay out.

const CJK = /[　-鿿가-힯＀-￯]/g

/** DeepSeek's published rule of thumb: ~0.3 token per English and ~0.6 per Chinese character. */
export function estimateTokens(text) {
  const value = String(text ?? '')
  const cjk = value.match(CJK)?.length ?? 0
  return Math.ceil(cjk * 0.6 + (value.length - cjk) * 0.3)
}

export const digest = value =>
  createHash('sha256').update(typeof value === 'string' ? value : JSON.stringify(value ?? null)).digest('hex').slice(0, 12)

const text = value => typeof value === 'string' ? value : JSON.stringify(value ?? null)

/** `use` hides the AlphaLab command inside its input; attribution needs it. */
export function toolLabel(name, args) {
  const input = args?.input
  if (name === 'use' && input && typeof input === 'object' && input.command) return `use:${input.command}`
  if (name === 'use' && args?.node_id) return `use:${args.node_id}`
  return name
}

// Tool results are {success, output}; AlphaLab tools report failure as output.status.
// Only these top-level fields count: a successful read may list failed jobs.
const parsed = result => {
  if (typeof result !== 'string') return result
  try { return JSON.parse(result) } catch { return null }
}
const failedResult = result => {
  const value = parsed(result)
  if (!value || typeof value !== 'object') return false
  return value.success === false || (value.output ?? value)?.status === 'failed'
}
const errorCode = result => {
  const value = parsed(result)
  const output = value?.output ?? value
  return typeof output?.error_code === 'string' ? output.error_code : null
}

/** Top-level confirmation flags are the permission evidence a benchmark checks. */
export function confirmations(args) {
  const input = args?.input && typeof args.input === 'object' ? args.input : {}
  return Object.keys(input).filter(key => key.startsWith('confirm_') && input[key] === true).sort()
}

/** Versions that make two traces comparable: code, prompt bundle and benchmark labels. */
export async function bundleMetadata(sourceRoot, env = process.env) {
  const git = args => { try { return execFileSync('git', args, { cwd: sourceRoot, encoding: 'utf8' }).trim() } catch { return null } }
  const bundle = resolve(sourceRoot, 'integrations/conexus/alphalab-research-agent')
  const read = async path => readFile(resolve(bundle, path), 'utf8')
  const tools = (await readdir(resolve(bundle, 'tools'))).filter(file => file.endsWith('.tool.json')).sort()
  const prompt = { agent: await read('agents/AlphaLab-Research-Agent.agent.json'), skill: await read('skills/alphalab-sdk-v1/SKILL.md'),
    schema: await read('workspace-output.schema.json'), tools: await Promise.all(tools.map(file => read(`tools/${file}`))) }
  return {
    git_commit: git(['rev-parse', 'HEAD']),
    git_dirty: git(['status', '--porcelain', '--untracked-files=no']) ? true : false,
    prompt_digest: digest(prompt),
    agent_digest: digest(prompt.agent),
    skill_digest: digest(prompt.skill),
    tools_digest: digest(prompt.tools),
    benchmark: env.ALPHALAB_BENCHMARK_VERSION || null,
    task_id: env.ALPHALAB_BENCHMARK_TASK || null,
    label: env.ALPHALAB_TRACE_LABEL || null,
  }
}

export function createTraceRecorder({ dir, meta = {}, clock = Date.now }) {
  const runs = new Map()
  let pending = Promise.resolve()
  const now = () => new Date(clock()).toISOString()
  const run = requestId => {
    const id = String(requestId || 'untraced').replace(/[^A-Za-z0-9_.-]/g, '_')
    if (!runs.has(id)) runs.set(id, { id, file: join(dir, `${id}.jsonl`), header: false, tools: new Map(), seen: new Set(), lastPromptChars: 0 })
    return runs.get(id)
  }
  // Appends are serialized so rows keep their order; a write failure never fails the Agent.
  const write = (state, row) => {
    pending = pending.then(async () => {
      await mkdir(dir, { recursive: true })
      if (!state.header) {
        state.header = true
        await appendFile(state.file, JSON.stringify({ type: 'run.meta', requestId: state.id, at: now(), ...meta }) + '\n')
      }
      await appendFile(state.file, JSON.stringify(row) + '\n')
    }).catch(() => {})
    return pending
  }

  function event(item) {
    const state = run(item.requestId)
    const base = { requestId: state.id, at: item.at || now(), modelCallId: item.modelCallId, iteration: item.iteration }
    if (item.type === 'tool.started') {
      state.tools.set(item.toolCallId, { started: clock(), label: toolLabel(item.toolName, item.arguments), args: digest(item.arguments),
        confirms: confirmations(item.arguments) })
      return
    }
    if (item.type === 'tool.completed' || item.type === 'tool.failed') {
      const started = state.tools.get(item.toolCallId)
      state.tools.delete(item.toolCallId)
      const label = started?.label ?? item.toolName
      const key = `${label}|${started?.args}`
      const repeat = state.seen.has(key)
      state.seen.add(key)
      const result = text(item.result)
      return write(state, { type: 'tool', ...base, tool: item.toolName, label, args_digest: started?.args ?? null,
        confirms: started?.confirms ?? [], ms: started ? clock() - started.started : null,
        ok: item.type === 'tool.completed' && !failedResult(item.result), error_code: item.errorCode ?? errorCode(item.result),
        repeat, result_chars: result.length, result_tokens_est: estimateTokens(result) })
    }
    if (item.type.startsWith('request.')) return write(state, { type: item.type, ...base, error_code: item.errorCode ?? null })
  }

  /** Per-run model meter: captures provider `usage`, which the Conexus parser discards. */
  function meter({ model, baseUrl, fetchImpl = fetch }) {
    let usage = null
    const host = (() => { try { return new URL(baseUrl).host } catch { return null } })()
    return {
      fetch: async (url, init) => {
        const response = await fetchImpl(url, init)
        if (response.ok) usage = response.clone().json().then(body => body?.usage ?? null, () => null)
        return response
      },
      wrap: complete => async input => {
        const state = run(input.trace?.requestId)
        const system = input.messages?.filter(message => message.role === 'system').map(message => text(message.content)).join('') ?? ''
        const prompt = text(input.messages)
        const toolsText = text(input.tools ?? [])
        const started = clock()
        usage = null
        const row = { type: 'model', requestId: state.id, at: now(), modelCallId: input.trace?.modelCallId, iteration: input.trace?.iteration,
          model, provider_host: host, messages: input.messages?.length ?? 0, prompt_chars: prompt.length,
          prompt_growth_chars: prompt.length - state.lastPromptChars, system_chars: system.length, tool_schema_chars: toolsText.length,
          est_tokens_in: estimateTokens(prompt) + estimateTokens(toolsText),
          ...(input.runBudget ? { budget_phase: input.runBudget.phase, budget_call: input.runBudget.call } : {}),
          ...(input.compaction ? { compacted_messages: input.compaction.messages, compacted_saved_chars: input.compaction.saved_chars } : {}) }
        state.lastPromptChars = prompt.length
        try {
          const result = await complete(input)
          const reported = await usage
          const out = text(result?.content ?? '') + text(result?.tool_calls ?? [])
          await write(state, { ...row, ms: clock() - started, ok: true, finish_reason: result?.finish_reason ?? null,
            tool_calls: (result?.tool_calls ?? []).map(call => { let args = null; try { args = JSON.parse(call.function.arguments) } catch {} return toolLabel(call.function.name, args) }),
            usage_source: reported ? 'provider' : 'estimate',
            tokens_in: reported?.prompt_tokens ?? row.est_tokens_in,
            tokens_out: reported?.completion_tokens ?? estimateTokens(out),
            cached_tokens_in: reported?.prompt_cache_hit_tokens ?? reported?.prompt_tokens_details?.cached_tokens ?? null })
          return result
        } catch (problem) {
          await write(state, { ...row, ms: clock() - started, ok: false, error_code: problem?.code ?? 'model_call_failed',
            error: String(problem?.message ?? problem).slice(0, 200) })
          throw problem
        }
      },
    }
  }

  return { event, meter, flush: () => pending }
}
