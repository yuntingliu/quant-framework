/**
 * Widget registry — original AlphaLab GUI IDs mapped onto barebone-safe panels.
 */
import type { ComponentType } from "react"

import { BacktestCompareWidget } from "../backtest/BacktestCompare"
import { BacktestExplorerWidget } from "../backtest/BacktestExplorer"
import { BacktestRunnerWidget } from "../backtest/BacktestRunner"
import { BacktestWorkbenchWidget } from "../backtest/BacktestWorkbench"
import { StrategyListWidget } from "../backtest/StrategyList"
import { AdapterSlotsWidget } from "../data/AdapterSlots"
import { DataCenterWidget } from "../data/DataCenter"
import { FactorReturnsWidget } from "../data/FactorReturns"
import { PaperExecutionDeskWidget } from "../execution/PaperExecutionDesk"
import { RiskConsoleWidget } from "../execution/RiskConsole"
import { WorkstationHomeWidget } from "../home/WorkstationHome"
import { HistoricalMarketWidget } from "../market/HistoricalMarket"
import { AnnualReturnsWidget } from "../market/AnnualReturns"
import { CorrelationMatrixWidget } from "../market/CorrelationMatrix"
import { CumulativeReturnsWidget } from "../market/CumulativeReturns"
import { DrawdownAnalysisWidget } from "../market/DrawdownAnalysis"
import { FactorStatsWidget } from "../market/FactorStats"
import { MarketKPIWidget } from "../market/MarketKPI"
import { VolatilityAnalysisWidget } from "../market/VolatilityAnalysis"
import { StrategyEditorWidget } from "../research/StrategyEditor"
import { SignalDeskWidget } from "../research/SignalDesk"
import { ResearchAgentPanel } from "../research/ResearchAgent"
import { ResearchResultViewerWidget } from "../research/ResearchResultViewer"
import { AdapterDisabledWidget } from "../system/AdapterDisabled"
import { HelpWidget } from "../system/Help"
import { LogWidget } from "../system/Log"

import { widgetCatalog, widgetCategory, widgetDescription, widgetTitleById } from "./catalog"

function disabled(widgetId: string): ComponentType {
  function DisabledPanel() {
    const meta = widgetCatalog.find((item) => item.id === widgetId)
    return (
      <AdapterDisabledWidget
        title={widgetTitleById(widgetId, "zh", widgetId)}
        category={meta ? widgetCategory(meta, "zh") : undefined}
        description={meta ? widgetDescription(meta, "zh") : undefined}
      />
    )
  }
  return DisabledPanel
}

const disabledComponents = Object.fromEntries(
  widgetCatalog.map((widget) => [widget.id, disabled(widget.id)]),
) as Record<string, ComponentType>

export const widgetComponents: Record<string, ComponentType> = {
  ...disabledComponents,

  // Barebone-active panels.
  "home.index-board": WorkstationHomeWidget,
  "market.history": HistoricalMarketWidget,
  "data.center": DataCenterWidget,
  "data.factor-returns": FactorReturnsWidget,
  "data.freshness": AdapterSlotsWidget,
  "data.dolphindb": AdapterSlotsWidget,
  "strategies.hub": StrategyListWidget,
  "backtest.explorer": BacktestExplorerWidget,
  "backtest.runner": BacktestRunnerWidget,
  "backtest.strategies": StrategyListWidget,
  "strategy.editor": StrategyEditorWidget,
  "research.signal-preview": SignalDeskWidget,
  "research.agent": ResearchAgentPanel,
  "research.result-viewer": ResearchResultViewerWidget,
  "market.kpi": MarketKPIWidget,
  "market.cumulative-returns": CumulativeReturnsWidget,
  "market.factor-stats": FactorStatsWidget,
  "market.drawdowns": DrawdownAnalysisWidget,
  "market.annual-returns": AnnualReturnsWidget,
  "market.volatility": VolatilityAnalysisWidget,
  "market.correlation": CorrelationMatrixWidget,
  "backtest.compare": BacktestCompareWidget,
  "backtest.workbench": BacktestWorkbenchWidget,
  "trading.status": PaperExecutionDeskWidget,
  "trading.ashare.status": PaperExecutionDeskWidget,
  "trading.rebalance": PaperExecutionDeskWidget,
  "trading.alerts": RiskConsoleWidget,
  "trading.factor-exposure": RiskConsoleWidget,
  "system.log": LogWidget,
  "system.help": HelpWidget,

  // Common legacy aliases from saved AlphaLab layouts.
  "research.ai-workflow": ResearchAgentPanel,
  "trading.positions": disabled("trading.positions"),
  "trading.order-entry": disabled("trading.order-entry"),
  "trading.order-book": disabled("trading.order-book"),
  "trading.history": disabled("trading.history"),
  "trading.chart": disabled("trading.chart"),
  "trading.ashare.positions": disabled("trading.ashare.positions"),
  "trading.ashare.order-entry": disabled("trading.ashare.order-entry"),
  "trading.ashare.order-book": disabled("trading.ashare.order-book"),
  "trading.ashare.history": disabled("trading.ashare.history"),
  "trading.depth": disabled("trading.order-book"),
  "trading.blotter": disabled("trading.blotter"),
  "trading.greeks": disabled("trading.greeks"),
  "trading.baskets": disabled("trading.baskets"),
  "trading.drift": disabled("trading.drift"),
  "trading.auto-trade": disabled("trading.auto-trade"),
}
