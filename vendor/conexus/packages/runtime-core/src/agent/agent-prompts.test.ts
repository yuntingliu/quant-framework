import assert from 'node:assert/strict'
import test from 'node:test'
import {
  buildCanvasAgentSystemPrompt,
  CANVAS_AGENT_SYSTEM_PROMPT
} from './agent-prompts.js'

test('Agent node instructions extend the canonical canvas prompt', () => {
  const prompt = buildCanvasAgentSystemPrompt({
    hostPolicySections: ['Hosted authorization policy.'],
    nodeSystemPrompt: 'Write concisely.'
  })

  const baseIndex = prompt.indexOf(CANVAS_AGENT_SYSTEM_PROMPT)
  const workStyleIndex = prompt.indexOf('## Work Style')
  const customIndex = prompt.indexOf('## Node-Level Instructions\nWrite concisely.')
  const canvasIndex = prompt.indexOf('## Canvas Rules')
  const nodeCatalogIndex = prompt.indexOf('### Choose the Most Appropriate Node Type')
  const orchestrationIndex = prompt.indexOf('## Orchestration Strategy')
  const completionIndex = prompt.indexOf('## Completion Requirements')
  const hostedIndex = prompt.indexOf('Hosted authorization policy.')

  assert.equal(baseIndex, 0)
  assert.ok(workStyleIndex > baseIndex)
  assert.ok(canvasIndex > workStyleIndex)
  assert.ok(nodeCatalogIndex > canvasIndex)
  assert.ok(orchestrationIndex > nodeCatalogIndex)
  assert.ok(completionIndex > orchestrationIndex)
  assert.ok(hostedIndex > completionIndex)
  assert.ok(customIndex > hostedIndex)
  assert.doesNotMatch(prompt, /Available Capabilities|Tools available for this run|Ambient runtime nodes/)
  assert.doesNotMatch(prompt, /Durable Objective|Canvas Context/)
})

test('the canonical default prompt is not duplicated as node instructions', () => {
  const prompt = buildCanvasAgentSystemPrompt({ nodeSystemPrompt: CANVAS_AGENT_SYSTEM_PROMPT })

  assert.equal(prompt.split(CANVAS_AGENT_SYSTEM_PROMPT).length - 1, 1)
  assert.equal(prompt.includes('## Node-Level Instructions'), false)
})

test('the compact prompt retains decision policy while tool mechanics stay in schemas', () => {
  const prompt = buildCanvasAgentSystemPrompt()

  assert.match(prompt, /keep moving forward until you have produced a deliverable result/i)
  assert.match(prompt, /canvas is a semantic graph/i)
  assert.match(prompt, /prefer targeted updates/i)
  assert.match(prompt, /repeated bounded edit patch operations/)
  assert.match(prompt, /create schema embeds authoritative type-specific fields/)
  assert.match(prompt, /capability declared by the runtime-node context, find, or observe/)
  assert.doesNotMatch(prompt, /runtime:web-search|runtime:harness-library|runtime:image-generator/)
  assert.doesNotMatch(prompt, /contentAppend/)
  assert.match(prompt, /Only start asynchronous work when it is genuinely necessary/i)
  assert.match(prompt, /Every run MUST end with a successful call to the `complete` tool/)
  assert.match(prompt, /ordinary assistant text does not end the run/)
  assert.match(prompt, /final user-facing answer in `complete\.summary`/)
  assert.match(prompt, /`status=blocked` only when there is a concrete blocker/)
  assert.doesNotMatch(prompt, /## Tool Rules|## Harness Routing|## Multi-Agent Collaboration/)
})
