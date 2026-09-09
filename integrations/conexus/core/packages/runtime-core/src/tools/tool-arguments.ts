export interface ParsedToolArguments {
  args: Record<string, unknown>
  recovered: boolean
  error?: string
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value)
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error)
}

function skipWhitespace(source: string, index: number): number {
  while (index < source.length && /\s/.test(source[index] ?? '')) index++
  return index
}

function stripCodeFence(source: string): string {
  const trimmed = source.trim()
  const match = trimmed.match(/^```(?:json)?\s*([\s\S]*?)\s*```$/i)
  return match?.[1]?.trim() ?? trimmed
}

function objectSlice(source: string): string {
  const stripped = stripCodeFence(source)
  const start = stripped.indexOf('{')
  const end = stripped.lastIndexOf('}')
  if (start < 0 || end <= start) throw new Error('Tool arguments do not contain an object')
  return stripped.slice(start, end + 1)
}

function nextLooksLikeProperty(source: string, index: number): boolean {
  index = skipWhitespace(source, index)
  if (source[index] === '"' || source[index] === "'") {
    const quote = source[index]
    index++
    while (index < source.length) {
      if (source[index] === '\\') {
        index += 2
        continue
      }
      if (source[index] === quote) {
        index = skipWhitespace(source, index + 1)
        return source[index] === ':'
      }
      index++
    }
    return false
  }

  const match = source.slice(index).match(/^[A-Za-z_$][A-Za-z0-9_$-]*/)
  if (!match) return false
  index = skipWhitespace(source, index + match[0].length)
  return source[index] === ':'
}

function isEscaped(source: string, index: number): boolean {
  let backslashes = 0
  for (let i = index - 1; i >= 0 && source[i] === '\\'; i--) backslashes++
  return backslashes % 2 === 1
}

function onlyTrailingObjectClose(source: string, closeIndex: number): boolean {
  let index = skipWhitespace(source, closeIndex + 1)
  if (source[index] === ',') index = skipWhitespace(source, index + 1)
  return index >= source.length
}

function decodeLooseString(source: string): string {
  let result = ''
  for (let i = 0; i < source.length; i++) {
    const char = source[i]
    if (char !== '\\') {
      result += char
      continue
    }

    const next = source[++i]
    if (next === undefined) {
      result += '\\'
    } else if (next === '"' || next === '\\' || next === '/') {
      result += next
    } else if (next === 'b') {
      result += '\b'
    } else if (next === 'f') {
      result += '\f'
    } else if (next === 'n') {
      result += '\n'
    } else if (next === 'r') {
      result += '\r'
    } else if (next === 't') {
      result += '\t'
    } else if (next === 'u' && /^[0-9a-fA-F]{4}$/.test(source.slice(i + 1, i + 5))) {
      result += String.fromCharCode(Number.parseInt(source.slice(i + 1, i + 5), 16))
      i += 4
    } else {
      result += `\\${next}`
    }
  }
  return result
}

function parseKey(source: string, index: number): { key: string; next: number } {
  index = skipWhitespace(source, index)
  const quote = source[index]
  if (quote === '"' || quote === "'") {
    const start = ++index
    while (index < source.length) {
      if (!isEscaped(source, index) && source[index] === quote) {
        return { key: decodeLooseString(source.slice(start, index)), next: index + 1 }
      }
      index++
    }
    throw new Error('Unterminated object key')
  }

  const match = source.slice(index).match(/^[A-Za-z_$][A-Za-z0-9_$-]*/)
  if (!match) throw new Error('Expected object key')
  return { key: match[0], next: index + match[0].length }
}

function parseLooseString(source: string, index: number): { value: string; next: number } {
  const quote = source[index]
  const start = index + 1
  index = start

  while (index < source.length) {
    if (!isEscaped(source, index) && source[index] === quote) {
      const afterQuote = skipWhitespace(source, index + 1)
      if (source[afterQuote] === ',' && nextLooksLikeProperty(source, afterQuote + 1)) {
        return { value: decodeLooseString(source.slice(start, index)), next: afterQuote }
      }
      if (source[afterQuote] === '}' && onlyTrailingObjectClose(source, afterQuote)) {
        return { value: decodeLooseString(source.slice(start, index)), next: afterQuote }
      }
    }
    index++
  }

  throw new Error('Unterminated string value')
}

function delimiterIndex(source: string, index: number): number {
  let depth = 0
  let quote: string | null = null

  while (index < source.length) {
    const char = source[index]
    if (quote) {
      if (char === '\\') {
        index += 2
        continue
      }
      if (char === quote) quote = null
      index++
      continue
    }

    if (char === '"' || char === "'") {
      quote = char
    } else if (char === '{' || char === '[') {
      depth++
    } else if (char === '}' || char === ']') {
      if (depth === 0) return index
      depth--
    } else if (char === ',' && depth === 0 && nextLooksLikeProperty(source, index + 1)) {
      return index
    }
    index++
  }

  return source.length
}

function parsePrimitive(source: string): unknown {
  const trimmed = source.trim()
  if (trimmed === 'true') return true
  if (trimmed === 'false') return false
  if (trimmed === 'null') return null
  if (/^-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?$/.test(trimmed)) return Number(trimmed)
  return trimmed
}

function parseValue(source: string, index: number): { value: unknown; next: number } {
  index = skipWhitespace(source, index)
  const char = source[index]

  if (char === '"' || char === "'") return parseLooseString(source, index)

  const end = delimiterIndex(source, index)
  const raw = source.slice(index, end).trim()
  if (raw.startsWith('{')) {
    try {
      return { value: JSON.parse(raw), next: end }
    } catch {
      return { value: parseLooseObject(raw), next: end }
    }
  }
  if (raw.startsWith('[')) {
    try {
      return { value: JSON.parse(raw), next: end }
    } catch {
      return { value: raw, next: end }
    }
  }
  return { value: parsePrimitive(raw), next: end }
}

function parseLooseObject(rawSource: string): Record<string, unknown> {
  const source = objectSlice(rawSource)
  const result: Record<string, unknown> = {}
  let index = skipWhitespace(source, 1)

  while (index < source.length) {
    index = skipWhitespace(source, index)
    if (source[index] === '}') return result
    if (source[index] === ',') {
      index++
      continue
    }

    const key = parseKey(source, index)
    index = skipWhitespace(source, key.next)
    if (source[index] !== ':') throw new Error(`Expected colon after ${key.key}`)
    const parsed = parseValue(source, index + 1)
    result[key.key] = parsed.value
    index = skipWhitespace(source, parsed.next)
    if (source[index] === ',') index++
  }

  return result
}

export function parseToolArguments(raw: string): ParsedToolArguments {
  try {
    const parsed = JSON.parse(raw) as unknown
    if (!isRecord(parsed)) return { args: {}, recovered: false, error: 'Tool arguments must be a JSON object.' }
    return { args: parsed, recovered: false }
  } catch (error) {
    const primaryError = errorMessage(error)
    try {
      return { args: parseLooseObject(raw), recovered: true, error: primaryError }
    } catch (fallbackError) {
      return {
        args: {},
        recovered: false,
        error: `${primaryError}; fallback parser failed: ${errorMessage(fallbackError)}`
      }
    }
  }
}
