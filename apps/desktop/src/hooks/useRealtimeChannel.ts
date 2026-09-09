// React hooks that bind the shared WebSocket client to component lifecycles and
// the TanStack Query cache. Realtime is additive: when the socket is down these
// hooks simply deliver nothing and the widgets keep using their polling queries.

import { useEffect, useRef } from "react"
import { useQueryClient } from "@tanstack/react-query"

import { getRealtimeClient, type RealtimeEnvelope } from "../lib/realtime"

/** Subscribe to a topic for the lifetime of the component. */
export function useRealtimeChannel(
  topic: string | null,
  onMessage: (msg: RealtimeEnvelope) => void,
): void {
  const handler = useRef(onMessage)
  handler.current = onMessage

  useEffect(() => {
    if (!topic) return
    const unsubscribe = getRealtimeClient().subscribe(topic, (msg) => handler.current(msg))
    return unsubscribe
  }, [topic])
}

/**
 * Subscribe to a topic and invalidate a TanStack query whenever a message
 * arrives, so an existing `useQuery` consumer refetches live. Use this for
 * topics that signal "something changed" (orders, fills, positions deltas).
 */
export function useRealtimeInvalidate(
  topic: string | null,
  queryKey: readonly unknown[],
): void {
  const queryClient = useQueryClient()
  useRealtimeChannel(topic, () => {
    queryClient.invalidateQueries({ queryKey })
  })
}

/**
 * Subscribe to a topic and write the payload straight into the query cache.
 * Use this for topics that carry the full current value (account, a quote),
 * avoiding an extra HTTP round-trip.
 */
export function useRealtimeData<T>(
  topic: string | null,
  queryKey: readonly unknown[],
): void {
  const queryClient = useQueryClient()
  useRealtimeChannel(topic, (msg) => {
    if (msg.type === "snapshot" || msg.type === "delta") {
      queryClient.setQueryData<T>(queryKey, msg.data as T)
    }
  })
}
