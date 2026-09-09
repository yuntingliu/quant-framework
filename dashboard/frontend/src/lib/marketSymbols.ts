/** Normalize complete RQData exchange suffixes without changing name/partial searches. */
export function normalizeMarketSymbol(value: string): string {
  const normalized = value.trim().toUpperCase()
  const suffixes: Record<string, string> = { XSHG: "SH", XSHE: "SZ", XBEI: "BJ" }
  return normalized.replace(/\.(XSHG|XSHE|XBEI)$/, (_, suffix: string) => `.${suffixes[suffix]}`)
}
