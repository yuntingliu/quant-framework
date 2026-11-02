import assert from "node:assert/strict"
import { describe, it } from "node:test"
import { ConexusClientError, isUndecidedSubmission, readWithRecovery, resumeRunEvents, reusableSubmission, standingSubmission, submitRunOnce } from "../src/lib/conexus/runConnection.ts"
import type { AgentSessionSnapshot } from "../src/lib/conexus/types.ts"

const encode = new TextEncoder()
function block(sequence: number, status = "running", runId = "run-1") {
  return `id: ${sequence}\ndata: ${JSON.stringify({ sequence, runId, status, type: `run.${status}`, at: "now" })}\n\n`
}
function stream(text: string, broken = false) {
  let delivered = false
  return new ReadableStream<Uint8Array>({ pull(controller) {
    if (!delivered) { delivered = true; controller.enqueue(encode.encode(text)) }
    else if (broken) controller.error(new TypeError("Failed to fetch"))
    else controller.close()
  } })
}
const wait = async () => undefined

describe("Undecided submissions", () => {
  const pending = { conversationId: "chat-1", title: "T", createdAt: "now", messageId: "user-1", message: "Research this" }
  it("treats only outcomes the server could not decide as the same send", () => {
    assert.equal(isUndecidedSubmission(new TypeError("Failed to fetch")), true)
    assert.equal(isUndecidedSubmission(new ConexusClientError("Bad gateway", 502)), true)
    assert.equal(isUndecidedSubmission(new ConexusClientError("Gateway timeout", 504)), true)
    assert.equal(isUndecidedSubmission(new ConexusClientError("Unavailable", 503)), true, "a gateway answered, not the server")
    assert.equal(isUndecidedSubmission(new ConexusClientError("Queue full", 429)), false, "the server declined it")
    assert.equal(isUndecidedSubmission(new ConexusClientError("No model", 503, "not_configured")), false)
    assert.equal(isUndecidedSubmission(new ConexusClientError("Stopping", 503, "local_host_error")), false)
    assert.equal(isUndecidedSubmission(new ConexusClientError("Busy", 409, "submission_in_progress")), true)
    assert.equal(isUndecidedSubmission(new ConexusClientError("Unknown", 409, "submission_unconfirmed")), true)
    assert.equal(isUndecidedSubmission(new ConexusClientError("Invalid", 422)), false)
    assert.equal(isUndecidedSubmission(new ConexusClientError("Conflict", 409)), false)
  })
  it("reuses the identity only for the same text in the same conversation", () => {
    assert.equal(reusableSubmission(pending, "Research this", "chat-1"), pending)
    assert.equal(reusableSubmission(pending, "Research this", null), pending, "a first message has no conversation yet")
    assert.equal(reusableSubmission(pending, "Something else", "chat-1"), null, "different text is a deliberate new request")
    assert.equal(reusableSubmission(pending, "Research this", "chat-2"), null)
    assert.equal(reusableSubmission(null, "Research this", "chat-1"), null)
  })
})

describe("Keeping an undecided submission across reads", () => {
  const local = { conversationId: "chat-1", title: "T", createdAt: "now", messageId: "user-1", message: "Research this" }
  const open = { ...local, messageId: "user-0", fromServer: true }
  it("keeps a send the server may simply not have received yet", () => {
    assert.equal(standingSubmission(local, null, []), local)
    assert.equal(standingSubmission(local, open, []), local)
    assert.equal(standingSubmission(local, null, [{ id: "chat-1", messages: [{ id: "other" }] }]), local)
  })
  it("ends it once the message is on record", () => {
    assert.equal(standingSubmission(local, null, [{ id: "chat-1", messages: [{ id: "user-1" }] }]), null)
  })
  it("lets the server end a send that only the server reported", () => {
    assert.equal(standingSubmission(open, null, []), null)
    assert.equal(standingSubmission(null, open, []), open)
  })
})

describe("Agent connection recovery", () => {
  it("resumes after a mid-stream failure, skips replays and stops on completion", async () => {
    const seen: number[] = [], cursors: number[] = []
    await resumeRunEvents({ runId: "run-1", signal: new AbortController().signal, wait,
      open: async (cursor) => {
        cursors.push(cursor)
        return cursors.length === 1 ? stream(block(1), true)
          : stream(block(1) + block(99, "running", "other-run") + block(2, "completed"))
      }, onEvent: event => { seen.push(event.sequence) },
    })
    assert.deepEqual(cursors, [0, 1])
    assert.deepEqual(seen, [1, 2])
  })
  it("reconnects a clean but premature close and a failed initial fetch", async () => {
    let opens = 0
    await resumeRunEvents({ runId: "run-1", signal: new AbortController().signal, wait,
      open: async () => {
        opens += 1
        if (opens === 1) throw new TypeError("Failed to fetch")
        return stream(opens === 2 ? ": heartbeat\n\n" : block(1, "completed"))
      }, onEvent: () => undefined,
    })
    assert.equal(opens, 3)
  })
  it("does not retry authentication errors or event application errors", async () => {
    let opens = 0
    await assert.rejects(resumeRunEvents({ runId: "run-1", signal: new AbortController().signal, wait,
      open: async () => { opens += 1; throw new ConexusClientError("Sign in", 401) }, onEvent: () => undefined,
    }), /Sign in/)
    assert.equal(opens, 1)
    await assert.rejects(resumeRunEvents({ runId: "run-1", signal: new AbortController().signal, wait,
      open: async () => stream(block(1)), onEvent: () => { throw new TypeError("application error") },
    }), /application error/)
  })
  it("stops reconnecting when the panel is closed", async () => {
    const controller = new AbortController()
    let opens = 0
    await resumeRunEvents({ runId: "run-1", signal: controller.signal,
      open: async () => { opens += 1; return stream(": heartbeat\n\n") },
      wait: async () => { controller.abort() }, onEvent: () => undefined,
    })
    assert.equal(opens, 1)
  })
  it("retries reads with a bound and preserves permanent errors", async () => {
    let attempts = 0
    assert.equal(await readWithRecovery(async () => {
      if (++attempts < 3) throw new TypeError("Failed to fetch")
      return "recovered"
    }, undefined, wait), "recovered")
    assert.equal(attempts, 3)
    attempts = 0
    await assert.rejects(readWithRecovery(async () => { attempts += 1; throw new TypeError("offline") }, undefined, wait), /offline/)
    assert.equal(attempts, 3)
  })
  it("recovers an accepted submission without repeating the billed POST", async () => {
    const session: AgentSessionSnapshot = {
      conversations: [{ id: "chat", title: "check", createdAt: "now", updatedAt: "now", messages: [
        { id: "message", role: "user", content: "check", createdAt: "now", runId: "run-1" },
      ] }], runs: [{ conversationId: "chat", run: { id: "run-1", slug: "agent", version: 1, status: "running", createdAt: "now" } }],
    }
    let posts = 0
    const create = async (): Promise<never> => { posts += 1; throw new TypeError("Failed to fetch") }
    const result = await submitRunOnce(create, async () => session, { id: "chat", messageId: "message" })
    assert.equal(result.run.id, "run-1")
    assert.equal(posts, 1)
    await assert.rejects(submitRunOnce(create, async () => session, { id: "chat", messageId: "not-accepted" }), /Failed to fetch/)
    assert.equal(posts, 2)
  })
})
