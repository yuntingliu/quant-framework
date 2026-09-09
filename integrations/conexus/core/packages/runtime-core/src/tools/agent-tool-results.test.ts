import assert from 'node:assert/strict'
import test from 'node:test'
import { browserAgentToolResult } from './browser.js'
import { executableAgentToolResult, sqlAgentToolResult } from './agent-tool-results.js'

test('shared SQL results expose only actionable read, write, and error fields', () => {
  assert.deepEqual(sqlAgentToolResult('read', {
    success: true,
    rows: [{ id: 1 }],
    rowCount: 1
  }), {
    success: true,
    rows: [{ id: 1 }],
    row_count: 1
  })
  assert.deepEqual(sqlAgentToolResult('write', {
    success: true,
    row_count: 3
  }), {
    success: true,
    affected_rows: 3
  })
  assert.deepEqual(sqlAgentToolResult('read', {
    success: false,
    error: ' denied '
  }), {
    success: false,
    error: 'denied'
  })
})

test('shared executable Tool results omit runtime and process telemetry', () => {
  assert.deepEqual(executableAgentToolResult({
    success: true,
    result: { value: 42 },
    output: { value: 42 },
    stdout: '',
    stderr: '',
    exitCode: 0,
    timedOut: false
  }), {
    success: true,
    output: { value: 42 }
  })
  assert.deepEqual(executableAgentToolResult({
    success: false,
    stdout: 'partial output',
    stderr: 'process failed',
    exit_code: 2,
    timed_out: false
  }), {
    success: false,
    output: 'partial output',
    error: 'process failed'
  })
})

test('shared Browser results omit transport messages and preview metadata', () => {
  assert.deepEqual(browserAgentToolResult('browser-1', {
    success: true,
    message: 'Navigation complete.',
    previewDataUrl: 'data:image/png;base64,internal',
    snapshot: {
      version: 4,
      url: 'https://example.com/',
      title: 'Example',
      pageText: 'Hello',
      elements: []
    }
  }), {
    success: true,
    browser_node_id: 'browser-1',
    snapshot: {
      url: 'https://example.com/',
      title: 'Example',
      text: 'Hello',
      elements: []
    }
  })
})
