function collectTemplatePlaceholders(value: unknown, output: Set<string>): void {
  if (typeof value === 'string') {
    for (const match of value.matchAll(/{{\s*([a-zA-Z0-9_.-]+)\s*}}/g)) {
      if (match[1]) output.add(match[1])
    }
    return
  }
  if (Array.isArray(value)) {
    for (const item of value) collectTemplatePlaceholders(item, output)
    return
  }
  if (value && typeof value === 'object') {
    for (const nested of Object.values(value)) collectTemplatePlaceholders(nested, output)
  }
}

export function extractHarnessTemplatePlaceholders(...values: unknown[]): string[] {
  const placeholders = new Set<string>()
  for (const value of values) collectTemplatePlaceholders(value, placeholders)
  return [...placeholders].sort()
}
