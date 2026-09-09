// Single multiplexed WebSocket client for live trading data.
//
// One socket is shared across the whole app. Widgets subscribe to topics
// ("positions", "account", "orders", "fills", "events", "quotes:AAPL", ...)
// and receive the hub envelopes the backend broadcasts. The client tracks the
// union of desired topics, (re)subscribes on every (re)connect, and reconnects
// with exponential backoff. If the socket can't be established the app keeps
// working via the existing polling queries — realtime is strictly additive.

import { getApiOrigin } from "./api"

export interface RealtimeEnvelope<T = unknown> {
  v: number
  topic: string
  type: "snapshot" | "delta" | "hello" | "pong" | string
  seq?: number
  ts?: string
  market?: string | null
  data: T
}

type Listener = (msg: RealtimeEnvelope) => void

function resolveWsUrl(): string {
  const origin = getApiOrigin()
  if (origin) {
    return origin.replace(/^http/i, "ws") + "/api/ws"
  }
  // Browser/Vite dev: rely on the dev-server proxy (ws: true) for /api.
  if (typeof window !== "undefined" && window.location) {
    const proto = window.location.protocol === "https:" ? "wss" : "ws"
    return `${proto}://${window.location.host}/api/ws`
  }
  return "ws://127.0.0.1:8000/api/ws"
}

class RealtimeClient {
  private socket: WebSocket | null = null
  private readonly listeners = new Map<string, Set<Listener>>()
  private readonly refCounts = new Map<string, number>()
  private reconnectDelay = 500
  private readonly maxDelay = 15000
  private closedByUser = false
  private connecting = false

  private get topics(): string[] {
    return Array.from(this.refCounts.keys()).filter((t) => (this.refCounts.get(t) ?? 0) > 0)
  }

  private ensureSocket(): void {
    if (this.socket || this.connecting) return
    this.connecting = true
    this.closedByUser = false
    let ws: WebSocket
    try {
      ws = new WebSocket(resolveWsUrl())
    } catch {
      this.connecting = false
      this.scheduleReconnect()
      return
    }
    this.socket = ws

    ws.onopen = () => {
      this.connecting = false
      this.reconnectDelay = 500
      const topics = this.topics
      if (topics.length > 0) {
        this.send({ action: "subscribe", topics })
      }
    }
    ws.onmessage = (event) => {
      let msg: RealtimeEnvelope
      try {
        msg = JSON.parse(event.data)
      } catch {
        return
      }
      this.dispatch(msg)
    }
    ws.onclose = () => {
      this.socket = null
      this.connecting = false
      if (!this.closedByUser) this.scheduleReconnect()
    }
    ws.onerror = () => {
      try {
        ws.close()
      } catch {
        /* ignore */
      }
    }
  }

  private scheduleReconnect(): void {
    if (this.topics.length === 0) return
    const delay = this.reconnectDelay
    this.reconnectDelay = Math.min(this.reconnectDelay * 2, this.maxDelay)
    setTimeout(() => this.ensureSocket(), delay)
  }

  private send(payload: unknown): void {
    if (this.socket && this.socket.readyState === WebSocket.OPEN) {
      this.socket.send(JSON.stringify(payload))
    }
  }

  private dispatch(msg: RealtimeEnvelope): void {
    const direct = this.listeners.get(msg.topic)
    if (direct) direct.forEach((cb) => cb(msg))
    // Wildcard: a listener on "quotes:*" receives "quotes:AAPL".
    if (msg.topic.includes(":")) {
      const wildcard = this.listeners.get(`${msg.topic.split(":")[0]}:*`)
      if (wildcard) wildcard.forEach((cb) => cb(msg))
    }
  }

  /** Subscribe a listener to a topic. Returns an unsubscribe function. */
  subscribe(topic: string, listener: Listener): () => void {
    if (!this.listeners.has(topic)) this.listeners.set(topic, new Set())
    this.listeners.get(topic)!.add(listener)

    const subTopic = topic.endsWith(":*") ? topic : topic
    const next = (this.refCounts.get(subTopic) ?? 0) + 1
    this.refCounts.set(subTopic, next)
    if (next === 1) {
      this.ensureSocket()
      this.send({ action: "subscribe", topics: [subTopic] })
    }

    return () => {
      const set = this.listeners.get(topic)
      if (set) {
        set.delete(listener)
        if (set.size === 0) this.listeners.delete(topic)
      }
      const count = (this.refCounts.get(subTopic) ?? 1) - 1
      if (count <= 0) {
        this.refCounts.delete(subTopic)
        this.send({ action: "unsubscribe", topics: [subTopic] })
      } else {
        this.refCounts.set(subTopic, count)
      }
    }
  }

  /** Tear the socket down entirely (e.g. on full app teardown). */
  close(): void {
    this.closedByUser = true
    this.socket?.close()
    this.socket = null
  }
}

let client: RealtimeClient | null = null

export function getRealtimeClient(): RealtimeClient {
  if (!client) client = new RealtimeClient()
  return client
}
