/**
 * Widget registry — original AlphaLab GUI IDs mapped onto barebone-safe panels.
 */
import type { ComponentType } from "react"

import { BacktestWorkbenchWidget } from "../backtest/BacktestWorkbench"
import { DataWorkbenchWidget } from "../data/DataWorkbench"
import {
  FactorEditorWidget,
  FactorEvidenceWidget,
  FactorLibraryWidget,
  FactorSnapshotWidget,
} from "../factors/FactorLabPanels"
import { FactorWorkbenchWidget } from "../factors/FactorWorkbench"
import { PaperExecutionDeskWidget } from "../execution/PaperExecutionDesk"
import { RiskConsoleWidget } from "../execution/RiskConsole"
import { ResearchAgentPanel } from "../research/ResearchAgent"
import { ReportWorkbenchWidget } from "../research/ReportWorkbench"
import { ProjectWorkbenchWidget } from "../project/ProjectWorkbench"
import { PythonLabWorkbenchWidget } from "../python/PythonLabWorkbench"
import {
  ExecutionWorkbenchWidget,
  PortfolioWorkbenchWidget,
  SelectionWorkbenchWidget,
} from "../pipeline/StageWorkbench"
import {
  ExecutionCostsWidget,
  ExecutionGuardrailsWidget,
  ExecutionSettingsWidget,
  ExecutionTargetsWidget,
  PortfolioConstraintsWidget,
  PortfolioInputWidget,
  PortfolioSummaryWidget,
  PortfolioWeightsWidget,
  SelectionChartWidget,
  SelectionDistributionWidget,
  SelectionFactorEvidenceWidget,
  SelectionFunnelWidget,
  SelectionRankingWidget,
} from "../pipeline/StageResultPanels"
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

  // Parallel workstations backed by one sequential three-stage Python pipeline.
  "data.workbench": DataWorkbenchWidget,
  "project.workbench": ProjectWorkbenchWidget,
  "factor.workbench": FactorWorkbenchWidget,
  "factor.library": FactorLibraryWidget,
  "factor.editor": FactorEditorWidget,
  "factor.snapshot": FactorSnapshotWidget,
  "factor.evidence": FactorEvidenceWidget,
  "selection.workbench": SelectionWorkbenchWidget,
  "portfolio.workbench": PortfolioWorkbenchWidget,
  "execution.workbench": ExecutionWorkbenchWidget,
  "selection.funnel": SelectionFunnelWidget,
  "selection.ranking": SelectionRankingWidget,
  "selection.distribution": SelectionDistributionWidget,
  "selection.factor-evidence": SelectionFactorEvidenceWidget,
  "selection.chart": SelectionChartWidget,
  "portfolio.input": PortfolioInputWidget,
  "portfolio.weights": PortfolioWeightsWidget,
  "portfolio.summary": PortfolioSummaryWidget,
  "portfolio.constraints": PortfolioConstraintsWidget,
  "execution.settings": ExecutionSettingsWidget,
  "execution.targets": ExecutionTargetsWidget,
  "execution.costs": ExecutionCostsWidget,
  "execution.guardrails": ExecutionGuardrailsWidget,
  "backtest.workbench": BacktestWorkbenchWidget,
  "python.workbench": PythonLabWorkbenchWidget,
  "report.workbench": ReportWorkbenchWidget,

  // The Agent is hosted in the shell rail, not in the Dockview catalog.
  "research.agent": ResearchAgentPanel,

  // Paper execution remains a separate, explicit post-research flow.
  "trading.rebalance": PaperExecutionDeskWidget,
  "trading.alerts": RiskConsoleWidget,
}
