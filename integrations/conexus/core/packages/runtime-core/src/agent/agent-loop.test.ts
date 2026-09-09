import assert from 'node:assert/strict'
import test from 'node:test'
import {
  runAgentLoop,
  type AgentLLMResult,
  type AgentMessage,
  type AgentToolCall
} from './agent-loop.js'
import { AgentRunController } from './agent-run-controller.js'

function toolCall(argumentsText: string, name = 'lookup', id = 'call-1'): AgentToolCall {
  return {
    id,
    type: 'function',
    function: {
      name,
      arguments: argumentsText
    }
  }
}

test('runAgentLoop returns an invalid-arguments tool result without executing the tool', async () => {
  const messages: AgentMessage[] = []
  const observedResults: string[] = []
  const observedArguments: Array<Record<string, unknown>> = []
  let llmCalls = 0
  let executeCalls = 0

  const result = await runAgentLoop({
    messages,
    maxIterations: 3,
    isAborted: () => false,
    callLLM: async (): Promise<AgentLLMResult> => {
      llmCalls++
      return llmCalls === 1
        ? { content: null, tool_calls: [toolCall('not-json')], finish_reason: 'tool_calls' }
        : { content: 'Recovered after the tool error.', finish_reason: 'stop' }
    },
    onNoToolCalls: () => ({ kind: 'return', value: 'finished' }),
    onToolCall: (_call, args) => {
      observedArguments.push(args)
    },
    executeTool: async () => {
      executeCalls++
      return JSON.stringify({ success: true })
    },
    onToolResult: (_call, toolResult) => {
      observedResults.push(toolResult)
    },
    onAbort: () => 'aborted',
    onLLMError: () => 'llm-error',
    onMaxIterations: () => 'max-iterations'
  })

  assert.equal(result, 'finished')
  assert.equal(executeCalls, 0)
  assert.deepEqual(observedArguments, [{}])
  assert.equal(observedResults.length, 1)
  const invalidResult = JSON.parse(observedResults[0] ?? '{}') as Record<string, unknown>
  assert.equal(invalidResult.success, false)
  assert.equal(invalidResult.error, 'Invalid JSON tool arguments.')
  assert.equal(typeof invalidResult.details, 'string')
  assert.match(observedResults[0] ?? '', /fallback parser failed/)
  assert.deepEqual(
    messages.map(({ role, name, tool_call_id: toolCallId }) => ({ role, name, toolCallId })),
    [
      { role: 'assistant', name: undefined, toolCallId: undefined },
      { role: 'tool', name: 'lookup', toolCallId: 'call-1' },
      { role: 'assistant', name: undefined, toolCallId: undefined }
    ]
  )
})

test('runAgentLoop executes a valid tool call and carries its transcript into the next LLM turn', async () => {
  const messages: AgentMessage[] = [{ role: 'user', content: 'Start.' }]
  const callbacks: string[] = []
  let llmCalls = 0

  const result = await runAgentLoop({
    messages,
    maxIterations: 3,
    isAborted: () => false,
    callLLM: async (currentMessages) => {
      llmCalls++
      if (llmCalls === 1) {
        assert.equal(currentMessages.length, 1)
        return {
          content: 'I will look that up.',
          tool_calls: [toolCall('{"value":3}')],
          finish_reason: 'tool_calls'
        }
      }

      assert.equal(currentMessages.length, 3)
      assert.deepEqual(currentMessages[2], {
        role: 'tool',
        content: '{"success":true,"value":6}',
        tool_call_id: 'call-1',
        name: 'lookup'
      })
      return { content: 'The answer is 6.', finish_reason: 'stop' }
    },
    onAssistantTurn: ({ content, toolCalls }) => {
      callbacks.push(`assistant-turn:${content}:${toolCalls.length}`)
    },
    onNoToolCalls: () => {
      callbacks.push('no-tools')
      return { kind: 'return', value: 'done' }
    },
    onToolCall: (call, args) => {
      callbacks.push(`tool:${call.function.name}:${String(args.value)}`)
    },
    executeTool: async (_call, args) => {
      callbacks.push('execute')
      return JSON.stringify({ success: true, value: Number(args.value) * 2 })
    },
    onToolResult: () => {
      callbacks.push('tool-result')
    },
    afterTool: () => {
      callbacks.push('after-tool')
    },
    onAbort: () => 'aborted',
    onLLMError: () => 'llm-error',
    onMaxIterations: () => 'max-iterations'
  })

  assert.equal(result, 'done')
  assert.equal(llmCalls, 2)
  assert.deepEqual(callbacks, [
    'assistant-turn:I will look that up.:1',
    'tool:lookup:3',
    'execute',
    'tool-result',
    'after-tool',
    'assistant-turn:The answer is 6.:0',
    'no-tools'
  ])
  assert.deepEqual(messages[3], { role: 'assistant', content: 'The answer is 6.' })
})

test('runAgentLoop observes aborts raised by beforeIteration before calling the model', async () => {
  let aborted = false
  let llmCalls = 0
  let abortCalls = 0

  const result = await runAgentLoop({
    messages: [],
    maxIterations: 2,
    isAborted: () => aborted,
    beforeIteration: () => {
      aborted = true
    },
    callLLM: async () => {
      llmCalls++
      return { content: null, finish_reason: 'stop' }
    },
    onNoToolCalls: () => ({ kind: 'continue' }),
    executeTool: async () => '',
    onAbort: () => {
      abortCalls++
      return 'aborted'
    },
    onLLMError: () => 'llm-error',
    onMaxIterations: () => 'max-iterations'
  })

  assert.equal(result, 'aborted')
  assert.equal(abortCalls, 1)
  assert.equal(llmCalls, 0)
})

test('runAgentLoop invokes onMaxIterations after the configured number of continuing turns', async () => {
  let llmCalls = 0
  let noToolCalls = 0

  const result = await runAgentLoop({
    messages: [],
    maxIterations: 3,
    isAborted: () => false,
    callLLM: async () => {
      llmCalls++
      return { content: null, finish_reason: 'stop' }
    },
    onNoToolCalls: () => {
      noToolCalls++
      return { kind: 'continue' }
    },
    executeTool: async () => '',
    onAbort: () => 'aborted',
    onLLMError: () => 'llm-error',
    onMaxIterations: () => 'max-iterations'
  })

  assert.equal(result, 'max-iterations')
  assert.equal(llmCalls, 3)
  assert.equal(noToolCalls, 3)
})

test('runAgentLoop routes model failures through onLLMError', async () => {
  const failure = new Error('provider unavailable')
  let observed: unknown

  const result = await runAgentLoop({
    messages: [],
    maxIterations: 2,
    isAborted: () => false,
    callLLM: async () => {
      throw failure
    },
    onNoToolCalls: () => ({ kind: 'continue' }),
    executeTool: async () => '',
    onAbort: () => 'aborted',
    onLLMError: (error) => {
      observed = error
      return 'handled-llm-error'
    },
    onMaxIterations: () => 'max-iterations'
  })

  assert.equal(result, 'handled-llm-error')
  assert.equal(observed, failure)
})

test('runAgentLoop gives FIFO continuation input priority over completion and skips remaining tool calls', async () => {
  const controller = new AgentRunController()
  const messages: AgentMessage[] = [{ role: 'user', content: 'Start.' }]
  const executed: string[] = []
  const toolResults: Array<{ name: string; result: string }> = []
  const continuationBatches: string[][] = []
  let llmCalls = 0
  let afterToolCalls = 0

  const result = await runAgentLoop({
    messages,
    maxIterations: 3,
    controller,
    continuationMessage: (continuation) => ({
      role: 'user',
      content: `Additional input: ${continuation}`
    }),
    onContinuations: (continuations) => {
      continuationBatches.push([...continuations])
    },
    callLLM: async (currentMessages) => {
      llmCalls++
      if (llmCalls === 1) {
        return {
          content: 'I am finishing the original request.',
          tool_calls: [
            toolCall('{"status":"done"}', 'complete', 'call-complete'),
            toolCall('{"value":2}', 'lookup', 'call-skipped')
          ],
          finish_reason: 'tool_calls'
        }
      }

      assert.deepEqual(currentMessages.map((message) => message.role), [
        'user',
        'assistant',
        'tool',
        'tool',
        'user',
        'user'
      ])
      assert.equal(currentMessages[2]?.tool_call_id, 'call-complete')
      assert.equal(currentMessages[3]?.tool_call_id, 'call-skipped')
      const skipped = JSON.parse(String(currentMessages[3]?.content)) as Record<string, unknown>
      assert.equal(skipped.skipped, true)
      assert.equal(currentMessages[4]?.content, 'Additional input: first follow-up')
      assert.equal(currentMessages[5]?.content, 'Additional input: second follow-up')
      return { content: 'Handled both follow-ups.', finish_reason: 'stop' }
    },
    onNoToolCalls: () => ({ kind: 'return', value: 'continued' }),
    executeTool: async (call) => {
      executed.push(call.function.name)
      controller.continue('  first follow-up  ')
      controller.continue({ text: 'second follow-up' })
      return JSON.stringify({ success: true, status: 'done' })
    },
    onToolResult: (call, toolResult) => {
      toolResults.push({ name: call.function.name, result: toolResult })
    },
    afterTool: () => {
      afterToolCalls++
      return { kind: 'return', value: 'premature-completion' }
    },
    onAbort: () => 'aborted',
    onLLMError: () => 'llm-error',
    onMaxIterations: () => 'max-iterations'
  })

  assert.equal(result, 'continued')
  assert.equal(llmCalls, 2)
  assert.deepEqual(executed, ['complete'])
  assert.equal(afterToolCalls, 0)
  assert.deepEqual(continuationBatches, [['first follow-up', 'second follow-up']])
  assert.deepEqual(toolResults.map((item) => item.name), ['complete', 'lookup'])
  assert.equal((JSON.parse(toolResults[1]?.result ?? '{}') as Record<string, unknown>).skipped, true)
})

test('runAgentLoop closes every stale tool call before applying input received during the model call', async () => {
  const controller = new AgentRunController()
  const messages: AgentMessage[] = [{ role: 'user', content: 'Original request.' }]
  const executed: string[] = []
  const observedResults: Array<{ id: string; result: Record<string, unknown> }> = []
  let llmCalls = 0

  const result = await runAgentLoop({
    messages,
    maxIterations: 3,
    controller,
    callLLM: async (currentMessages) => {
      llmCalls++
      if (llmCalls === 1) {
        controller.continue('Changed request.')
        return {
          content: 'Working from the original request.',
          tool_calls: [
            toolCall('{"value":1}', 'first_tool', 'call-first'),
            toolCall('{"value":2}', 'second_tool', 'call-second')
          ],
          finish_reason: 'tool_calls'
        }
      }

      assert.deepEqual(currentMessages.map((message) => message.role), [
        'user',
        'assistant',
        'tool',
        'tool',
        'user'
      ])
      assert.deepEqual(
        currentMessages.slice(2, 4).map((message) => message.tool_call_id),
        ['call-first', 'call-second']
      )
      return { content: 'Handled the changed request.', finish_reason: 'stop' }
    },
    onNoToolCalls: () => ({ kind: 'return', value: 'done' }),
    executeTool: async (call) => {
      executed.push(call.id)
      return '{"success":true}'
    },
    onToolResult: (call, toolResult) => {
      observedResults.push({ id: call.id, result: JSON.parse(toolResult) as Record<string, unknown> })
    },
    onAbort: () => 'aborted',
    onLLMError: () => 'llm-error',
    onMaxIterations: () => 'max-iterations'
  })

  assert.equal(result, 'done')
  assert.equal(llmCalls, 2)
  assert.deepEqual(executed, [])
  assert.deepEqual(observedResults.map(({ id }) => id), ['call-first', 'call-second'])
  assert.ok(observedResults.every(({ result: toolResult }) => toolResult.skipped === true))
})

test('runAgentLoop appends a completed tool result before honoring an abort and closes remaining calls', async () => {
  const controller = new AgentRunController()
  const messages: AgentMessage[] = []
  const observedResults: Array<{ id: string; result: Record<string, unknown> }> = []

  const result = await runAgentLoop({
    messages,
    maxIterations: 2,
    controller,
    callLLM: async () => ({
      content: null,
      tool_calls: [
        toolCall('{}', 'first_tool', 'call-first'),
        toolCall('{}', 'second_tool', 'call-second')
      ],
      finish_reason: 'tool_calls'
    }),
    onNoToolCalls: () => ({ kind: 'return', value: 'done' }),
    executeTool: async (call) => {
      assert.equal(call.id, 'call-first')
      controller.abort('stop-after-tool')
      return '{"success":true,"value":42}'
    },
    onToolResult: (call, toolResult) => {
      observedResults.push({ id: call.id, result: JSON.parse(toolResult) as Record<string, unknown> })
    },
    onAbort: () => 'aborted',
    onLLMError: () => 'llm-error',
    onMaxIterations: () => 'max-iterations'
  })

  assert.equal(result, 'aborted')
  assert.deepEqual(messages.map((message) => message.role), ['assistant', 'tool', 'tool'])
  assert.deepEqual(messages.slice(1).map((message) => message.tool_call_id), ['call-first', 'call-second'])
  assert.deepEqual(observedResults[0], {
    id: 'call-first',
    result: { success: true, value: 42 }
  })
  assert.equal(observedResults[1]?.id, 'call-second')
  assert.equal(observedResults[1]?.result.skipped, true)
  assert.match(String(observedResults[1]?.result.reason), /aborted/)
})

test('runAgentLoop closes remaining calls when a tool returns the terminal result', async () => {
  const messages: AgentMessage[] = []

  const result = await runAgentLoop({
    messages,
    maxIterations: 2,
    callLLM: async () => ({
      content: null,
      tool_calls: [
        toolCall('{}', 'complete', 'call-complete'),
        toolCall('{}', 'late_tool', 'call-late')
      ],
      finish_reason: 'tool_calls'
    }),
    onNoToolCalls: () => ({ kind: 'return', value: 'unexpected' }),
    executeTool: async (call) => {
      assert.equal(call.id, 'call-complete')
      return '{"success":true}'
    },
    afterTool: () => ({ kind: 'return', value: 'done' }),
    onAbort: () => 'aborted',
    onLLMError: () => 'llm-error',
    onMaxIterations: () => 'max-iterations'
  })

  assert.equal(result, 'done')
  assert.deepEqual(messages.map((message) => message.role), ['assistant', 'tool', 'tool'])
  assert.deepEqual(messages.slice(1).map((message) => message.tool_call_id), ['call-complete', 'call-late'])
  const skipped = JSON.parse(String(messages[2]?.content)) as Record<string, unknown>
  assert.equal(skipped.skipped, true)
  assert.match(String(skipped.reason), /completed/)
})

test('runAgentLoop drains continuation input before accepting a no-tool return', async () => {
  const controller = new AgentRunController()
  const messages: AgentMessage[] = [{ role: 'user', content: 'Initial request.' }]
  const observedContinuations: string[] = []
  let llmCalls = 0
  let noToolCalls = 0

  const result = await runAgentLoop({
    messages,
    maxIterations: 3,
    controller,
    callLLM: async (currentMessages) => {
      llmCalls++
      if (llmCalls === 1) {
        controller.continue({ text: 'Please also verify it.' })
        return { content: 'Draft answer.', finish_reason: 'stop' }
      }
      assert.deepEqual(currentMessages.slice(-2), [
        { role: 'assistant', content: 'Draft answer.' },
        { role: 'user', content: 'Please also verify it.' }
      ])
      return { content: 'Verified answer.', finish_reason: 'stop' }
    },
    onContinuations: (continuations) => {
      observedContinuations.push(...continuations)
    },
    onNoToolCalls: () => {
      noToolCalls++
      return { kind: 'return', value: 'done' }
    },
    executeTool: async () => '',
    onAbort: () => 'aborted',
    onLLMError: () => 'llm-error',
    onMaxIterations: () => 'max-iterations'
  })

  assert.equal(result, 'done')
  assert.equal(llmCalls, 2)
  assert.equal(noToolCalls, 1)
  assert.deepEqual(observedContinuations, ['Please also verify it.'])
})

test('runAgentLoop routes a controller abort during a model call through onAbort', async () => {
  const controller = new AgentRunController()
  let llmErrorCalls = 0
  let noToolCalls = 0

  const result = await runAgentLoop({
    messages: [],
    maxIterations: 2,
    controller,
    callLLM: async () => {
      controller.abort('stop-now')
      throw new Error('provider rejected after abort')
    },
    onNoToolCalls: () => {
      noToolCalls++
      return { kind: 'return', value: 'done' }
    },
    executeTool: async () => '',
    onAbort: () => 'aborted',
    onLLMError: () => {
      llmErrorCalls++
      return 'llm-error'
    },
    onMaxIterations: () => 'max-iterations'
  })

  assert.equal(result, 'aborted')
  assert.equal(llmErrorCalls, 0)
  assert.equal(noToolCalls, 0)
})

test('runAgentLoop has no iteration ceiling when maxIterations is omitted', async () => {
  let calls = 0
  const result = await runAgentLoop({
    messages: [],
    callLLM: async () => {
      calls += 1
      return { content: `turn ${calls}`, finish_reason: 'stop' }
    },
    onNoToolCalls: () => calls >= 60
      ? { kind: 'return', value: 'done' }
      : { kind: 'continue' },
    executeTool: async () => '',
    onAbort: () => 'aborted',
    onLLMError: () => 'llm-error',
    onMaxIterations: () => 'max-iterations'
  })

  assert.equal(result, 'done')
  assert.equal(calls, 60)
})
