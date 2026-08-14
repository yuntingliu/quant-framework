import { useState } from "react"
import { BarChart3, BookOpen, CalendarRange, ChartSpline, FlaskConical, Gauge, Grid3X3, Sigma, TrendingDown } from "lucide-react"

import { useGlobalFilter } from "@/contexts/GlobalFilterContext"
import { useLanguage } from "@/contexts/LanguageContext"
import { ALL_FACTORS } from "@/lib/constants"
import { cn } from "@/lib/utils"
import { AnnualReturnsWidget } from "@/widgets/market/AnnualReturns"
import { CorrelationMatrixWidget } from "@/widgets/market/CorrelationMatrix"
import { CumulativeReturnsWidget } from "@/widgets/market/CumulativeReturns"
import { DrawdownAnalysisWidget } from "@/widgets/market/DrawdownAnalysis"
import { FactorStatsWidget } from "@/widgets/market/FactorStats"
import { VolatilityAnalysisWidget } from "@/widgets/market/VolatilityAnalysis"

import { FactorLibraryWidget } from "./FactorLibrary"
import { FactorResearchLab } from "./FactorResearchLab"

type FactorWorkbenchTab = "evaluate" | "library" | "returns" | "statistics" | "annual" | "volatility" | "correlation" | "drawdown"

const TABS: Array<{ id: FactorWorkbenchTab; zh: string; en: string; icon: typeof Sigma }> = [
  { id: "evaluate", zh: "检验", en: "Evaluate", icon: FlaskConical },
  { id: "library", zh: "因子库", en: "Library", icon: BookOpen },
  { id: "returns", zh: "收益", en: "Returns", icon: ChartSpline },
  { id: "statistics", zh: "统计", en: "Statistics", icon: Sigma },
  { id: "annual", zh: "年度", en: "Annual", icon: BarChart3 },
  { id: "volatility", zh: "波动", en: "Volatility", icon: Gauge },
  { id: "correlation", zh: "相关性", en: "Correlation", icon: Grid3X3 },
  { id: "drawdown", zh: "回撤", en: "Drawdown", icon: TrendingDown },
]

export function FactorWorkbenchWidget() {
  const { language } = useLanguage()
  const { startDate, endDate, selectedFactors, setStartDate, setEndDate, toggleFactor } = useGlobalFilter()
  const [tab, setTab] = useState<FactorWorkbenchTab>("evaluate")

  const copy = language === "zh"
    ? { title: "因子工作台", description: "选择因子与研究区间，在一个工作站完成定义检查和表现分析。", range: "共享研究区间" }
    : { title: "Factor Workbench", description: "Select factors and a research range, then inspect definitions and performance in one workstation.", range: "Shared research range" }

  return (
    <div className="flex h-full min-h-0 min-w-0 flex-col overflow-hidden bg-card">
      <div className="shrink-0 border-b border-border px-3 py-2">
        <div className="flex min-w-0 flex-wrap items-start gap-3">
          <div className="min-w-0 flex-1">
            <div className="text-sm font-semibold text-foreground">{copy.title}</div>
            <div className="mt-0.5 text-[11px] text-muted-foreground">{copy.description}</div>
          </div>
          <div className="flex flex-wrap items-center gap-1" aria-label="Selected factors">
            {ALL_FACTORS.map((factor) => (
              <button
                key={factor}
                type="button"
                aria-pressed={selectedFactors.includes(factor)}
                className={cn(
                  "rounded border px-2 py-1 text-[11px] font-medium",
                  selectedFactors.includes(factor)
                    ? "border-primary/40 bg-primary/10 text-primary"
                    : "border-border text-muted-foreground hover:bg-muted",
                )}
                onClick={() => toggleFactor(factor)}
              >
                {factor}
              </button>
            ))}
          </div>
          <div className="flex items-center gap-1 rounded border border-border bg-background px-2 py-1" title={copy.range}>
            <CalendarRange className="h-3.5 w-3.5 text-muted-foreground" />
            <input className="w-[7.5rem] bg-transparent text-[11px]" aria-label="Factor analysis start month" type="month" value={startDate} onChange={(event) => setStartDate(event.target.value)} />
            <span className="text-[10px] text-muted-foreground">—</span>
            <input className="w-[7.5rem] bg-transparent text-[11px]" aria-label="Factor analysis end month" type="month" value={endDate} onChange={(event) => setEndDate(event.target.value)} />
          </div>
        </div>
      </div>

      <div className="flex shrink-0 items-center gap-1 overflow-x-auto border-b border-border px-2 py-1.5" role="tablist" aria-label="Factor workbench view">
        {TABS.map(({ id, zh, en, icon: Icon }) => (
          <button
            key={id}
            type="button"
            role="tab"
            aria-selected={tab === id}
            className={cn(
              "flex h-7 shrink-0 items-center gap-1.5 rounded px-2 text-xs",
              tab === id ? "bg-primary/10 text-primary" : "text-muted-foreground hover:bg-muted hover:text-foreground",
            )}
            onClick={() => setTab(id)}
          >
            <Icon className="h-3.5 w-3.5" />
            {language === "zh" ? zh : en}
          </button>
        ))}
      </div>

      <div className="min-h-0 min-w-0 flex-1 overflow-hidden [&_.widget-frame]:border-0">
        {tab === "evaluate" && <FactorResearchLab />}
        {tab === "library" && <FactorLibraryWidget />}
        {tab === "returns" && <CumulativeReturnsWidget />}
        {tab === "statistics" && <FactorStatsWidget />}
        {tab === "annual" && <AnnualReturnsWidget />}
        {tab === "volatility" && <VolatilityAnalysisWidget />}
        {tab === "correlation" && <CorrelationMatrixWidget />}
        {tab === "drawdown" && <DrawdownAnalysisWidget />}
      </div>
    </div>
  )
}
