import { useState } from "react"
import { Activity, ArrowRight, BarChart3, BookOpen, CalendarRange, ChartSpline, Database, FlaskConical, Gauge, Grid3X3, Sigma, Target, TrendingDown } from "lucide-react"

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
import { CustomMarketRiskFactor } from "./CustomMarketRiskFactor"

type FactorWorkbenchTab = "cross_section" | "market" | "timing"
type CrossSectionTab = "evaluate" | "custom" | "library"
type MarketFactorTab = "returns" | "statistics" | "annual" | "volatility" | "correlation" | "drawdown" | "custom"

const PRIMARY_TABS: Array<{ id: FactorWorkbenchTab; zh: string; en: string; icon: typeof Sigma }> = [
  { id: "cross_section", zh: "股票横截面因子", en: "Stock cross-section", icon: Target },
  { id: "market", zh: "市场风险因子", en: "Market risk factors", icon: ChartSpline },
  { id: "timing", zh: "择时信号", en: "Timing signals", icon: Activity },
]

const CROSS_SECTION_TABS: Array<{ id: CrossSectionTab; zh: string; en: string; icon: typeof Sigma }> = [
  { id: "evaluate", zh: "内置因子检验", en: "Built-in factor test", icon: FlaskConical },
  { id: "custom", zh: "自定义因子", en: "Custom factor", icon: Sigma },
  { id: "library", zh: "因子库", en: "Factor library", icon: BookOpen },
]

const MARKET_TABS: Array<{ id: MarketFactorTab; zh: string; en: string; icon: typeof Sigma }> = [
  { id: "returns", zh: "累计收益", en: "Returns", icon: ChartSpline },
  { id: "statistics", zh: "统计", en: "Statistics", icon: Sigma },
  { id: "annual", zh: "年度", en: "Annual", icon: BarChart3 },
  { id: "volatility", zh: "MKT 波动", en: "MKT volatility", icon: Gauge },
  { id: "correlation", zh: "相关性", en: "Correlation", icon: Grid3X3 },
  { id: "drawdown", zh: "MKT 回撤", en: "MKT drawdown", icon: TrendingDown },
  { id: "custom", zh: "自定义风险因子", en: "Custom risk factor", icon: FlaskConical },
]

export function FactorWorkbenchWidget() {
  const { language } = useLanguage()
  const { startDate, endDate, selectedFactors, setStartDate, setEndDate, toggleFactor } = useGlobalFilter()
  const [profile, setProfile] = useDataProfile()
  const [tab, setTab] = useState<FactorWorkbenchTab>("cross_section")
  const [crossSectionTab, setCrossSectionTab] = useState<CrossSectionTab>("evaluate")
  const [marketTab, setMarketTab] = useState<MarketFactorTab>("returns")
  const [requestedFactor, setRequestedFactor] = useState<FactorResearchSelection | null>(null)

  const copy = language === "zh"
    ? {
        title: "因子工作台",
        description: "按用途区分股票横截面因子、市场风险因子和择时信号，避免混淆不同研究对象。",
        range: "共享研究区间",
        selected: "已选市场因子",
        start: "因子分析开始月份",
        end: "因子分析结束月份",
        views: "因子工作台视图",
        crossSectionViews: "股票横截面因子视图",
        marketViews: "市场因子收益视图",
        dataSource: "共享数据源",
        demo: "演示数据",
        runtime: "本地 RQ 数据",
        crossSectionHint: "用于同一时点给股票排序，并检验因子值与下一期相对收益的关系；使用完整合格股票池。",
        marketHint: "MKT、SMB、HML 用于解释市场与组合收益或风格暴露，不直接生成个股排名。",
        timingTitle: "择时信号目录",
        timingDescription: "择时信号研究市场时间序列，输出整体市场仓位；它不是股票横截面因子。",
        timingCount: "3 类内置信号",
        trend: "趋势",
        trendDetail: "比较市场累计净值与回看窗口移动平均；高于阈值时信号为 1，否则为 0。",
        momentum: "动量",
        momentumDetail: "计算回看窗口内的 MKT 累计收益；高于阈值时信号为 1，否则为 0。",
        volatility: "波动率控制",
        volatilityDetail: "用目标年化波动率除以实际年化波动率，并把信号限制在 0–1。",
        timingBoundary: "多个信号按权重合成后映射到最低/最高市场仓位；回测会将月末信号滞后一个月执行，避免未来数据。",
        timingLocation: "完整研究位于：策略工作台 → 择时信号 → 信号设计 / 组合构建 / 风险控制 / 交易执行。",
        openStrategy: "配置或编写自定义择时",
      }
    : {
        title: "Factor Workbench",
        description: "Separate stock cross-sectional factors, market risk factors, and timing signals by research purpose.",
        range: "Shared research range",
        selected: "Selected market factors",
        start: "Factor analysis start month",
        end: "Factor analysis end month",
        views: "Factor workbench view",
        crossSectionViews: "Stock cross-sectional factor view",
        marketViews: "Market factor return view",
        dataSource: "Shared data source",
        demo: "Demo data",
        runtime: "Local RQ data",
        crossSectionHint: "Ranks stocks at the same point in time and tests factor values against next-period relative returns across the full eligible universe.",
        marketHint: "MKT, SMB, and HML explain market or portfolio returns and style exposure; they do not directly rank stocks.",
        timingTitle: "Timing signal catalog",
        timingDescription: "Timing signals study a market time series and output aggregate market exposure; they are not stock cross-sectional factors.",
        timingCount: "3 built-in signals",
        trend: "Trend",
        trendDetail: "Compares cumulative market wealth with its lookback moving average; the score is 1 above the threshold and 0 otherwise.",
        momentum: "Momentum",
        momentumDetail: "Computes cumulative MKT return over the lookback window; the score is 1 above the threshold and 0 otherwise.",
        volatility: "Volatility control",
        volatilityDetail: "Divides target annualized volatility by realized annualized volatility and clips the score to 0–1.",
        timingBoundary: "Signals are weight-combined and mapped to minimum/maximum market exposure. Backtests lag month-end signals by one month to avoid look-ahead.",
        timingLocation: "Full research lives in Strategy Workbench → Market timing → Signal design / Portfolio construction / Risk controls / Trade execution.",
        openStrategy: "Configure or code custom timing",
      }

  function evaluateFromLibrary(factor: FactorResearchSelection) {
    setRequestedFactor({ ...factor })
    setTab("cross_section")
    setCrossSectionTab("evaluate")
  }

  function openStrategyWorkbench() {
    const url = new URL(window.location.href)
    url.searchParams.set("strategyType", "market_timing")
    window.history.replaceState(window.history.state, "", url)
    window.dispatchEvent(new CustomEvent("alphalab:switchMode", { detail: "strategy" }))
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
        {tab === "cross_section" && (
          <div className="flex h-full min-h-0 flex-col">
            <div className="factor-market-toolbar">
              <span>{copy.crossSectionHint}</span>
              <div className="factor-market-selector" role="tablist" aria-label={copy.crossSectionViews}>
                {CROSS_SECTION_TABS.map(({ id, zh, en, icon: Icon }) => (
                  <button
                    key={id}
                    type="button"
                    role="tab"
                    aria-selected={crossSectionTab === id}
                    className={cn(
                      "flex items-center gap-1 rounded border px-2 py-1 text-[11px] font-medium",
                      crossSectionTab === id
                        ? "border-primary/40 bg-primary/10 text-primary"
                        : "border-border text-muted-foreground hover:bg-muted",
                    )}
                    onClick={() => setCrossSectionTab(id)}
                  >
                    <Icon className="h-3.5 w-3.5" />
                    {language === "zh" ? zh : en}
                  </button>
                ))}
              </div>
            </div>
            <div className="min-h-0 flex-1 overflow-hidden">
              {crossSectionTab === "evaluate" && (
                <div className="factor-workbench-scroll-view">
                  <FactorResearchLab mode="builtin" requestedFactor={requestedFactor} />
                </div>
              )}
              {crossSectionTab === "custom" && (
                <div className="factor-workbench-scroll-view">
                  <FactorResearchLab mode="custom" />
                </div>
              )}
              {crossSectionTab === "library" && (
                <div className="factor-workbench-scroll-view">
                  <FactorLibraryWidget onEvaluate={evaluateFromLibrary} />
                </div>
              )}
            </div>
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
              {marketTab === "custom" && (
                <div className="factor-workbench-scroll-view">
                  <CustomMarketRiskFactor profile={profile} />
                </div>
              )}
            </div>
          </div>
        )}
        {tab === "timing" && (
          <div className="factor-workbench-scroll-view">
            <section className="panel">
              <div className="panel-heading">
                <div>
                  <h2>{copy.timingTitle}</h2>
                  <p>{copy.timingDescription}</p>
                </div>
                <span className="status-pill ready"><Activity size={13} /> {copy.timingCount}</span>
              </div>
              <div className="factor-library-grid">
                <article className="factor-library-card">
                  <div className="factor-library-card-heading"><ChartSpline size={15} /><strong>{copy.trend}</strong><span>0 / 1</span></div>
                  <p>{copy.trendDetail}</p>
                </article>
                <article className="factor-library-card">
                  <div className="factor-library-card-heading"><TrendingDown size={15} /><strong>{copy.momentum}</strong><span>0 / 1</span></div>
                  <p>{copy.momentumDetail}</p>
                </article>
                <article className="factor-library-card">
                  <div className="factor-library-card-heading"><Gauge size={15} /><strong>{copy.volatility}</strong><span>0–1</span></div>
                  <p>{copy.volatilityDetail}</p>
                </article>
              </div>
              <div className="mt-3 flex flex-wrap items-center justify-between gap-3 rounded-md border border-border bg-background/60 p-3">
                <div className="min-w-[16rem] flex-1 text-[11px] text-muted-foreground">
                  <p className="m-0 text-foreground">{copy.timingBoundary}</p>
                  <p className="mb-0 mt-1">{copy.timingLocation}</p>
                </div>
                <button type="button" className="strategy-link-button" onClick={openStrategyWorkbench}>
                  {copy.openStrategy}<ArrowRight size={13} />
                </button>
              </div>
            </section>
          </div>
        )}
      </div>
    </div>
  )
}
