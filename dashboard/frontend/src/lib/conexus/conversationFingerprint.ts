/** Compare persisted meaning, including JSON key order and equivalent UTC dates. */
export function conversationFingerprint(value: unknown): string {
  function canonical(item: unknown, key = ""): unknown {
    if ((key === "createdAt" || key === "updatedAt") && typeof item === "string") {
      const timestamp = Date.parse(item)
      if (Number.isFinite(timestamp)) return new Date(timestamp).toISOString()
    }
    if (Array.isArray(item)) return item.map((entry) => canonical(entry))
    if (item && typeof item === "object") {
      return Object.fromEntries(Object.entries(item)
        .sort(([left], [right]) => left.localeCompare(right))
        .map(([name, entry]) => [name, canonical(entry, name)]))
    }
    return item
  }
  return JSON.stringify(canonical(value))
}
