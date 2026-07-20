export function analyticsError(error: unknown): string | null {
  if (!error) return null
  return error instanceof Error ? error.message : String(error)
}
