import { useState } from "react"

export type IndicatorId = "ma20" | "ma60" | "ema12" | "boll" | "rsi" | "macd"

export const INDICATOR_OPTIONS: Array<{
  id: IndicatorId
  label: string
  pane: "overlay" | "sub"
}> = [
  { id: "ma20", label: "MA 20", pane: "overlay" },
  { id: "ma60", label: "MA 60", pane: "overlay" },
  { id: "ema12", label: "EMA 12", pane: "overlay" },
  { id: "boll", label: "BOLL (20, 2)", pane: "overlay" },
  { id: "rsi", label: "RSI 14", pane: "sub" },
  { id: "macd", label: "MACD (12, 26, 9)", pane: "sub" },
]

export function useIndicatorSelection(
  widgetKey: string,
): [IndicatorId[], (next: IndicatorId[]) => void] {
  const storageKey = `alphalab-chart-indicators:${widgetKey}`
  const [selected, setSelected] = useState<IndicatorId[]>(() => {
    try {
      const raw = localStorage.getItem(storageKey)
      if (raw) {
        const parsed: unknown = JSON.parse(raw)
        if (Array.isArray(parsed)) {
          return parsed.filter((id): id is IndicatorId =>
            INDICATOR_OPTIONS.some((option) => option.id === id),
          )
        }
      }
    } catch {
      // Local persistence is optional; keep the chart usable when storage is blocked.
    }
    return []
  })

  const update = (next: IndicatorId[]) => {
    setSelected(next)
    try {
      localStorage.setItem(storageKey, JSON.stringify(next))
    } catch {
      // Local persistence is optional.
    }
  }

  return [selected, update]
}
