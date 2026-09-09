/**
 * Shared chart theming for Recharts components.
 * Centralizes tick styles, tooltip styles, and formatters
 * so all charts have a consistent Bloomberg-like appearance.
 */

export const TICK_STYLE = {
  fontFamily: "IBM Plex Mono, monospace",
  fontSize: 10,
  fill: "hsl(var(--muted-foreground))",
} as const

export const TOOLTIP_STYLE = {
  background: "hsl(var(--card))",
  border: "1px solid hsl(var(--border))",
  borderRadius: "var(--radius)",
  fontSize: 11,
  fontFamily: "IBM Plex Mono, monospace",
  boxShadow: "0 4px 12px hsl(var(--background) / 0.5)",
} as const

export const GRID_STYLE = {
  strokeDasharray: "3 3",
  opacity: 0.08,
} as const

export const LEGEND_STYLE = {
  fontSize: 11,
  fontFamily: "DM Sans, sans-serif",
} as const

/** Format date for chart axis: YYYY-MM */
export function formatChartDate(value: string): string {
  if (!value) return ""
  return value.slice(0, 7)
}


/** Strategy / benchmark line colors */
export const LINE_COLORS = {
  strategy: "#2962FF",
  benchmark: "#FF6D00",
  position: "hsl(var(--primary))",
  vol: "#26A69A",
} as const

/** Color interpolation for heatmap cells */
export function interpolateHeatColor(
  value: number,
  maxAbs: number
): { bg: string; text: string } {
  if (value == null || Number.isNaN(value)) {
    return { bg: "transparent", text: "hsl(var(--muted-foreground))" }
  }
  const norm = Math.min(Math.abs(value) / (maxAbs || 1), 1)
  const alpha = Math.round(norm * 255)
    .toString(16)
    .padStart(2, "0")

  if (value > 0) {
    return {
      bg: `#00C853${alpha}`,
      text: norm > 0.6 ? "#fff" : "hsl(var(--foreground))",
    }
  }
  if (value < 0) {
    return {
      bg: `#FF1744${alpha}`,
      text: norm > 0.6 ? "#fff" : "hsl(var(--foreground))",
    }
  }
  return { bg: "transparent", text: "hsl(var(--muted-foreground))" }
}
