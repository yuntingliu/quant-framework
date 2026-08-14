/** Chart indicator selector for the market workbench. */
import { LineChart as LineChartIcon } from "lucide-react"

import { cn } from "@/lib/utils"
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover"
import { useLanguage } from "@/contexts/LanguageContext"
import { INDICATOR_OPTIONS, type IndicatorId } from "@/hooks/useIndicatorSelection"

export function IndicatorMenu({
  selected,
  onChange,
  className,
}: {
  selected: IndicatorId[]
  onChange: (next: IndicatorId[]) => void
  className?: string
}) {
  const { language } = useLanguage()
  const copy = language === "zh"
    ? { title: "技术指标", indicators: "指标", overlay: "主图", sub: "副图" }
    : { title: "Technical indicators", indicators: "Indicators", overlay: "overlay", sub: "sub" }
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
          title={copy.title}
        >
          <LineChartIcon className="h-3 w-3" />
          {copy.indicators}{selected.length > 0 ? ` (${selected.length})` : ""}
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
            <span className="text-[9px] uppercase text-muted-foreground">{copy[option.pane]}</span>
          </label>
        ))}
      </PopoverContent>
    </Popover>
  )
}
