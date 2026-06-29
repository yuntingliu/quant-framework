export type RechartsLabelFormatter = (label: unknown) => string
export type RechartsValueFormatter = (value: unknown, name: unknown) => [string, string]

export function toDateLabel(formatter: (value: string) => string): RechartsLabelFormatter {
  return (label) => formatter(String(label ?? ""))
}

export function toNamedValue(
  formatter: (value: number) => string,
  fallbackName = "",
): RechartsValueFormatter {
  return (value, name) => [formatter(Number(value)), String(name ?? fallbackName)]
}

export function toFixedValue(decimals = 1): RechartsValueFormatter {
  return (value) => [Number(value).toFixed(decimals), ""]
}
