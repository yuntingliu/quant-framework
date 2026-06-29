/**
 * Widget registry — original AlphaLab GUI IDs mapped onto barebone-safe panels.
 */
import type { ComponentType } from "react"

import { BacktestExplorerWidget } from "../backtest/BacktestExplorer"
import { BacktestRunnerWidget } from "../backtest/BacktestRunner"
import { StrategyListWidget } from "../backtest/StrategyList"
import { AdapterSlotsWidget } from "../data/AdapterSlots"
import { DataCenterWidget } from "../data/DataCenter"
import { PaperExecutionDeskWidget } from "../execution/PaperExecutionDesk"
import { RiskConsoleWidget } from "../execution/RiskConsole"
import { WorkstationHomeWidget } from "../home/WorkstationHome"
import { FactorLibraryWidget } from "../research/FactorLibrary"
import { StrategyEditorWidget } from "../research/StrategyEditor"
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
  "data.center": DataCenterWidget,
  "data.freshness": AdapterSlotsWidget,
  "data.dolphindb": AdapterSlotsWidget,
  "strategies.hub": StrategyListWidget,
  "backtest.explorer": BacktestExplorerWidget,
  "backtest.runner": BacktestRunnerWidget,
  "backtest.strategies": StrategyListWidget,
  "strategy.editor": StrategyEditorWidget,
  "backtest.workbench": BacktestRunnerWidget,
  "market.factor-stats": FactorLibraryWidget,
  "trading.status": PaperExecutionDeskWidget,
  "trading.ashare.status": PaperExecutionDeskWidget,
  "trading.rebalance": PaperExecutionDeskWidget,
  "trading.alerts": RiskConsoleWidget,
  "trading.factor-exposure": RiskConsoleWidget,
  "system.log": LogWidget,
  "system.help": HelpWidget,

  // Common legacy aliases from saved AlphaLab layouts.
  "research.ai-workflow": disabled("research.agent"),
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
