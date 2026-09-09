export function normalizedSql(value: unknown): string {
  return typeof value === 'string' ? value.trim() : ''
}

function firstSqlVerb(sql: string): string {
  return sql
    .replace(/^\s*(--[^\n]*\n\s*)+/g, '')
    .replace(/^\s*\/\*[\s\S]*?\*\/\s*/g, '')
    .split(/\s+/)[0]
    ?.toLowerCase() ?? ''
}

export function isReadOnlySql(sql: string): boolean {
  const verb = firstSqlVerb(sql)
  return verb === 'select' || verb === 'with'
}

export function sqlRisk(sql: string): string | null {
  const compact = sql.toLowerCase().replace(/\s+/g, ' ')
  if (/\b(drop|truncate|alter)\b/.test(compact)) return 'DROP, TRUNCATE, and ALTER statements are blocked.'
  if (/\bdelete\s+from\b/.test(compact) && !/\bwhere\b/.test(compact)) return 'DELETE without WHERE is blocked.'
  if (/\bupdate\b/.test(compact) && !/\bwhere\b/.test(compact)) return 'UPDATE without WHERE is blocked.'
  return null
}

export function singleSqlStatement(sql: string): string {
  const cleaned = normalizedSql(sql).replace(/;+\s*$/, '')
  if (!cleaned) throw new Error('sql is required.')
  if (cleaned.includes(';')) throw new Error('Only one SQL statement is allowed per call.')
  return cleaned
}
