/**
 * Chart indicator selector — checkbox dropdown persisted per widget key.
 */
import { useState } from "react"
import { LineChart as LineChartIcon } from "lucide-react"

import { cn } from "@/lib/utils"
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover"

export type IndicatorId = "ma20" | "ma60" | "ema12" | "boll" | "rsi" | "macd"

export const INDICATOR_OPTIONS: Array<{ id: IndicatorId; label: string; pane: "overlay" | "sub" }> = [
  { id: "ma20", label: "MA 20", pane: "overlay" },
  { id: "ma60", label: "MA 60", pane: "overlay" },
  { id: "ema12", label: "EMA 12", pane: "overlay" },
  { id: "boll", label: "BOLL (20, 2)", pane: "overlay" },
  { id: "rsi", label: "RSI 14", pane: "sub" },
  { id: "macd", label: "MACD (12, 26, 9)", pane: "sub" },
]

export function useIndicatorSelection(widgetKey: string): [IndicatorId[], (next: IndicatorId[]) => void] {
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
    } catch { /* ignore */ }
    return []
  })
  const update = (next: IndicatorId[]) => {
    setSelected(next)
    try { localStorage.setItem(storageKey, JSON.stringify(next)) } catch { /* ignore */ }
  }
  return [selected, update]
}

export function IndicatorMenu({
  selected,
  onChange,
  className,
}: {
  selected: IndicatorId[]
  onChange: (next: IndicatorId[]) => void
  className?: string
}) {
  const toggle = (id: IndicatorId) => {
    onChange(selected.includes(id) ? selected.filter((value) => value !== id) : [...selected, id])
  }

  return (
    <Popover>
      <PopoverTrigger asChild>
        <button
          type="button"
          className={cn(
            "flex h-6 items-center gap-1 rounded border px-1.5 text-[10px]",
            selected.length > 0
              ? "border-sky-400/60 bg-sky-500/10 text-sky-300"
              : "border-border/70 text-muted-foreground hover:bg-muted",
            className,
          )}
          title="Technical indicators"
        >
          <LineChartIcon className="h-3 w-3" />
          Indicators{selected.length > 0 ? ` (${selected.length})` : ""}
        </button>
      </PopoverTrigger>
      <PopoverContent align="end" className="w-44 p-1">
        {INDICATOR_OPTIONS.map((option) => (
          <label
            key={option.id}
            className="flex cursor-pointer items-center gap-2 rounded px-2 py-1 text-[11px] hover:bg-muted"
          >
            <input
              type="checkbox"
              checked={selected.includes(option.id)}
              onChange={() => toggle(option.id)}
            />
            <span className="flex-1">{option.label}</span>
            <span className="text-[9px] uppercase text-muted-foreground">{option.pane}</span>
          </label>
        ))}
      </PopoverContent>
    </Popover>
  )
}
