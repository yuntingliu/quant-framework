import { type ClassValue, clsx } from "clsx"
import { twMerge } from "tailwind-merge"

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

export function formatPercent(value: number, decimals = 2): string {
  return `${(value * 100).toFixed(decimals)}%`
}

export function formatNumber(value: number, decimals = 2): string {
  return value.toFixed(decimals)
}

/** Null-safe percent formatter — returns "N/A" for null/undefined/NaN. */
export function fmtPct(value: number | null | undefined, decimals = 2): string {
  if (value === null || value === undefined || isNaN(value)) return "N/A"
  return formatPercent(value, decimals)
}

/** Null-safe number formatter — returns "N/A" for null/undefined/NaN. */
export function fmtNum(value: number | null | undefined, decimals = 2): string {
  if (value === null || value === undefined || isNaN(value)) return "N/A"
  return formatNumber(value, decimals)
}

export function formatDate(dateStr: string): string {
  return new Date(dateStr).toLocaleDateString('zh-CN', { year: 'numeric', month: '2-digit' })
}

/**
 * Zip a dates/years array with a {key: values[]} series dict into chart-ready rows.
 * Works for both time series ({date, ...}) and annual data ({year, ...}).
 */
export function buildChartData(
  keys: string[],
  series: Record<string, (number | null)[]>,
  dateKey = "date",
): Record<string, number | string>[] {
  return keys.map((k, idx) => {
    const point: Record<string, number | string> = { [dateKey]: k }
    for (const [name, values] of Object.entries(series)) {
      point[name] = values[idx] ?? 0
    }
    return point
  })
}

/**
 * Derive chart series config from series keys + a color map.
 */
export function buildFactorSeries(
  seriesKeys: string[],
  colorMap: Record<string, string>,
): { key: string; name: string; color: string }[] {
  return seriesKeys.map((k) => ({
    key: k,
    name: k,
    color: colorMap[k] ?? "#999",
  }))
}
