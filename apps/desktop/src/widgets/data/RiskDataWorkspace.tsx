import { useState } from "react"
import { BarChart3, ChartSpline, FlaskConical, Gauge, Grid3X3, Sigma, TrendingDown } from "lucide-react"

import { useGlobalFilter } from "@/contexts/GlobalFilterContext"
import { useLanguage } from "@/contexts/LanguageContext"
import { ALL_FACTORS } from "@/lib/constants"
import type { DataProfile } from "@/lib/data-profile"
import { cn } from "@/lib/utils"
import { AnnualReturnsWidget } from "@/widgets/market/AnnualReturns"
import { CorrelationMatrixWidget } from "@/widgets/market/CorrelationMatrix"
import { CumulativeReturnsWidget } from "@/widgets/market/CumulativeReturns"
import { DrawdownAnalysisWidget } from "@/widgets/market/DrawdownAnalysis"
import { FactorStatsWidget } from "@/widgets/market/FactorStats"
import { VolatilityAnalysisWidget } from "@/widgets/market/VolatilityAnalysis"
import { CustomMarketRiskFactor } from "@/widgets/research/CustomMarketRiskFactor"

type RiskDataTab = "returns" | "statistics" | "annual" | "volatility" | "correlation" | "drawdown" | "custom"

const RISK_DATA_TABS: Array<{ id: RiskDataTab; zh: string; en: string; icon: typeof Sigma }> = [
  { id: "returns", zh: "累计收益", en: "Returns", icon: ChartSpline },
  { id: "statistics", zh: "统计", en: "Statistics", icon: Sigma },
  { id: "annual", zh: "年度", en: "Annual", icon: BarChart3 },
  { id: "volatility", zh: "MKT 波动", en: "MKT volatility", icon: Gauge },
  { id: "correlation", zh: "相关性", en: "Correlation", icon: Grid3X3 },
  { id: "drawdown", zh: "MKT 回撤", en: "MKT drawdown", icon: TrendingDown },
  { id: "custom", zh: "自定义风险序列", en: "Custom risk series", icon: FlaskConical },
]

export function RiskDataWorkspace({ profile }: { profile: DataProfile }) {
  const { language } = useLanguage()
  const { startDate, endDate, selectedFactors, setStartDate, setEndDate, toggleFactor } = useGlobalFilter()
  const [tab, setTab] = useState<RiskDataTab>("returns")
  const copy = language === "zh"
    ? {
        title: "市场与风格风险数据",
        hint: "MKT、SMB、HML、MOM、RMW 与 rf 是市场/风格收益序列，用于基准、归因和风险暴露分析；这里不把它们当作选股或择时信号。",
        selected: "已选风险序列",
        range: "风险数据区间",
        start: "风险数据开始月份",
        end: "风险数据结束月份",
        views: "风险数据视图",
      }
    : {
        title: "Market and style risk data",
        hint: "MKT, SMB, HML, MOM, RMW, and rf are market/style return series for benchmarks, attribution, and exposure analysis; they are not stock-selection or timing signals here.",
        selected: "Selected risk series",
        range: "Risk data range",
        start: "Risk data start month",
        end: "Risk data end month",
        views: "Risk data view",
      }

  return (
    <div className="flex min-h-[28rem] min-w-0 flex-col overflow-hidden rounded-md border border-border bg-card">
      <div className="factor-market-toolbar flex-wrap">
        <div className="min-w-[16rem] flex-1">
          <strong className="block text-xs text-foreground">{copy.title}</strong>
          <span>{copy.hint}</span>
        </div>
        <div className="flex items-center gap-1 rounded border border-border bg-background px-2 py-1" title={copy.range}>
          <input className="w-[7.5rem] bg-transparent text-[11px]" aria-label={copy.start} type="month" value={startDate} onChange={(event) => setStartDate(event.target.value)} />
          <span className="text-[10px] text-muted-foreground">—</span>
          <input className="w-[7.5rem] bg-transparent text-[11px]" aria-label={copy.end} type="month" value={endDate} onChange={(event) => setEndDate(event.target.value)} />
        </div>
        <div className="factor-market-selector" aria-label={copy.selected}>
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
      </div>
      <div className="flex shrink-0 items-center gap-1 overflow-x-auto border-b border-border px-2 py-1.5" role="tablist" aria-label={copy.views}>
        {RISK_DATA_TABS.map(({ id, zh, en, icon: Icon }) => (
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
      <div className="min-h-[24rem] min-w-0 flex-1 overflow-hidden [&_.widget-frame]:border-0">
        {tab === "returns" && <CumulativeReturnsWidget />}
        {tab === "statistics" && <FactorStatsWidget />}
        {tab === "annual" && <AnnualReturnsWidget />}
        {tab === "volatility" && <VolatilityAnalysisWidget />}
        {tab === "correlation" && <CorrelationMatrixWidget />}
        {tab === "drawdown" && <DrawdownAnalysisWidget />}
        {tab === "custom" && (
          <div className="factor-workbench-scroll-view">
            <CustomMarketRiskFactor profile={profile} />
          </div>
        )}
      </div>
    </div>
  )
}
