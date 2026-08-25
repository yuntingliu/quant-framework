/**
 * Widget registry — original AlphaLab GUI IDs mapped onto barebone-safe panels.
 */
import type { ComponentType } from "react"

import { ValidationWorkbenchWidget } from "../backtest/BacktestWorkbench"
import { DataWorkbenchWidget } from "../data/DataWorkbench"
import { FactorWorkbenchWidget } from "../factors/FactorWorkbench"
import { PaperExecutionDeskWidget } from "../execution/PaperExecutionDesk"
import { RiskConsoleWidget } from "../execution/RiskConsole"
import { ResearchAgentPanel } from "../research/ResearchAgent"
import { ReportWorkbenchWidget } from "../research/ReportWorkbench"
import { ProjectWorkbenchWidget } from "../project/ProjectWorkbench"
import { StrategyWorkbenchWidget } from "../strategy/StrategyWorkbench"
import { AdapterDisabledWidget } from "../system/AdapterDisabled"

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

  // Parallel views over one canonical Strategy SDK v1 Python source package.
  "data.workbench": DataWorkbenchWidget,
  "project.workbench": ProjectWorkbenchWidget,
  "factor.workbench": FactorWorkbenchWidget,
  "strategy.workbench": StrategyWorkbenchWidget,
  "validation.workbench": ValidationWorkbenchWidget,
  "report.workbench": ReportWorkbenchWidget,

  // The Agent is hosted in the shell rail, not in the Dockview catalog.
  "research.agent": ResearchAgentPanel,

  // Paper execution remains a separate, explicit post-research flow.
  "trading.rebalance": PaperExecutionDeskWidget,
  "trading.alerts": RiskConsoleWidget,
}
