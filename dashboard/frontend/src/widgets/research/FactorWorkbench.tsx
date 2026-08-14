import { useState } from "react"
import { BarChart3, BookOpen, CalendarRange, ChartSpline, Database, FlaskConical, Gauge, Grid3X3, Sigma, TrendingDown } from "lucide-react"

import { useGlobalFilter } from "@/contexts/GlobalFilterContext"
import { useLanguage } from "@/contexts/LanguageContext"
import { ALL_FACTORS } from "@/lib/constants"
import { useDataProfile, type DataProfile } from "@/lib/data-profile"
import { cn } from "@/lib/utils"
import { AnnualReturnsWidget } from "@/widgets/market/AnnualReturns"
import { CorrelationMatrixWidget } from "@/widgets/market/CorrelationMatrix"
import { CumulativeReturnsWidget } from "@/widgets/market/CumulativeReturns"
import { DrawdownAnalysisWidget } from "@/widgets/market/DrawdownAnalysis"
import { FactorStatsWidget } from "@/widgets/market/FactorStats"
import { VolatilityAnalysisWidget } from "@/widgets/market/VolatilityAnalysis"

import { FactorLibraryWidget } from "./FactorLibrary"
import { FactorResearchLab, type FactorResearchSelection } from "./FactorResearchLab"

type FactorWorkbenchTab = "evaluate" | "library" | "market"
type MarketFactorTab = "returns" | "statistics" | "annual" | "volatility" | "correlation" | "drawdown"

const PRIMARY_TABS: Array<{ id: FactorWorkbenchTab; zh: string; en: string; icon: typeof Sigma }> = [
  { id: "evaluate", zh: "选股因子检验", en: "Signal factor test", icon: FlaskConical },
  { id: "library", zh: "选股因子库", en: "Signal factor library", icon: BookOpen },
  { id: "market", zh: "市场因子收益", en: "Market factor returns", icon: ChartSpline },
]

const MARKET_TABS: Array<{ id: MarketFactorTab; zh: string; en: string; icon: typeof Sigma }> = [
  { id: "returns", zh: "累计收益", en: "Returns", icon: ChartSpline },
  { id: "statistics", zh: "统计", en: "Statistics", icon: Sigma },
  { id: "annual", zh: "年度", en: "Annual", icon: BarChart3 },
  { id: "volatility", zh: "波动", en: "Volatility", icon: Gauge },
  { id: "correlation", zh: "相关性", en: "Correlation", icon: Grid3X3 },
  { id: "drawdown", zh: "回撤", en: "Drawdown", icon: TrendingDown },
]

export function FactorWorkbenchWidget() {
  const { language } = useLanguage()
  const { startDate, endDate, selectedFactors, setStartDate, setEndDate, toggleFactor } = useGlobalFilter()
  const [profile, setProfile] = useDataProfile()
  const [tab, setTab] = useState<FactorWorkbenchTab>("evaluate")
  const [marketTab, setMarketTab] = useState<MarketFactorTab>("returns")
  const [requestedFactor, setRequestedFactor] = useState<FactorResearchSelection | null>(null)

  const copy = language === "zh"
    ? {
        title: "因子工作台",
        description: "检验股票打分因子；MKT、SMB、HML 等收益序列在独立区域分析。",
        range: "共享研究区间",
        selected: "已选市场因子",
        start: "因子分析开始月份",
        end: "因子分析结束月份",
        views: "因子工作台视图",
        marketViews: "市场因子收益视图",
        dataSource: "检验数据源",
        demo: "演示数据",
        runtime: "本地 RQ 数据",
        marketHint: "这里分析 MKT、SMB、HML 的收益序列，不是个股选股因子检验。",
      }
    : {
        title: "Factor Workbench",
        description: "Test stock-ranking signals; analyze MKT, SMB, and HML return series separately.",
        range: "Shared research range",
        selected: "Selected market factors",
        start: "Factor analysis start month",
        end: "Factor analysis end month",
        views: "Factor workbench view",
        marketViews: "Market factor return view",
        dataSource: "Test data source",
        demo: "Demo data",
        runtime: "Local RQ data",
        marketHint: "This area analyzes MKT, SMB, and HML return series, not stock-selection signals.",
      }

  function evaluateFromLibrary(factor: FactorResearchSelection) {
    setRequestedFactor({ ...factor })
    setTab("evaluate")
  }

  return (
    <div className="flex h-full min-h-0 min-w-0 flex-col overflow-hidden bg-card">
      <div className="shrink-0 border-b border-border px-3 py-2">
        <div className="flex min-w-0 flex-wrap items-start gap-3">
          <div className="min-w-0 flex-1">
            <div className="text-sm font-semibold text-foreground">{copy.title}</div>
            <div className="mt-0.5 text-[11px] text-muted-foreground">{copy.description}</div>
          </div>
          <label className="factor-profile-control" title={copy.dataSource}>
            <Database className="h-3.5 w-3.5" />
            <span>{copy.dataSource}</span>
            <select value={profile} onChange={(event) => setProfile(event.target.value as DataProfile)}>
              <option value="demo">{copy.demo}</option>
              <option value="runtime">{copy.runtime}</option>
            </select>
          </label>
          <div className="flex items-center gap-1 rounded border border-border bg-background px-2 py-1" title={copy.range}>
            <CalendarRange className="h-3.5 w-3.5 text-muted-foreground" />
            <input className="w-[7.5rem] bg-transparent text-[11px]" aria-label={copy.start} type="month" value={startDate} onChange={(event) => setStartDate(event.target.value)} />
            <span className="text-[10px] text-muted-foreground">—</span>
            <input className="w-[7.5rem] bg-transparent text-[11px]" aria-label={copy.end} type="month" value={endDate} onChange={(event) => setEndDate(event.target.value)} />
          </div>
        </div>
      </div>

      <div className="flex shrink-0 items-center gap-1 overflow-x-auto border-b border-border px-2 py-1.5" role="tablist" aria-label={copy.views}>
        {PRIMARY_TABS.map(({ id, zh, en, icon: Icon }) => (
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
        {tab === "evaluate" && (
          <div className="factor-workbench-scroll-view">
            <FactorResearchLab requestedFactor={requestedFactor} />
          </div>
        )}
        {tab === "library" && (
          <div className="factor-workbench-scroll-view">
            <FactorLibraryWidget onEvaluate={evaluateFromLibrary} />
          </div>
        )}
        {tab === "market" && (
          <div className="flex h-full min-h-0 flex-col">
            <div className="factor-market-toolbar">
              <span>{copy.marketHint}</span>
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
            <div className="flex shrink-0 items-center gap-1 overflow-x-auto border-b border-border px-2 py-1.5" role="tablist" aria-label={copy.marketViews}>
              {MARKET_TABS.map(({ id, zh, en, icon: Icon }) => (
                <button
                  key={id}
                  type="button"
                  role="tab"
                  aria-selected={marketTab === id}
                  className={cn(
                    "flex h-7 shrink-0 items-center gap-1.5 rounded px-2 text-xs",
                    marketTab === id ? "bg-primary/10 text-primary" : "text-muted-foreground hover:bg-muted hover:text-foreground",
                  )}
                  onClick={() => setMarketTab(id)}
                >
                  <Icon className="h-3.5 w-3.5" />
                  {language === "zh" ? zh : en}
                </button>
              ))}
            </div>
            <div className="min-h-0 flex-1 overflow-hidden">
              {marketTab === "returns" && <CumulativeReturnsWidget />}
              {marketTab === "statistics" && <FactorStatsWidget />}
              {marketTab === "annual" && <AnnualReturnsWidget />}
              {marketTab === "volatility" && <VolatilityAnalysisWidget />}
              {marketTab === "correlation" && <CorrelationMatrixWidget />}
              {marketTab === "drawdown" && <DrawdownAnalysisWidget />}
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
