import type { DockviewApi } from "dockview"

export interface LayoutPreset {
  id: string
  name: string
  description: string
  apply: (api: DockviewApi) => void
}

export type WorkspaceMode = "data" | "universe" | "selection" | "timing" | "portfolio" | "risk" | "execution" | "backtest" | "report"

function clearAll(api: DockviewApi) {
  for (const panel of [...api.panels]) panel.api.close()
}

function addPanel(
  api: DockviewApi,
  id: string,
  componentId: string,
  title: string,
  position?: { referencePanel: string; direction: "right" | "below" | "within" },
  size?: { initialWidth?: number; initialHeight?: number },
) {
  api.addPanel({
    id,
    component: "widget",
    params: { componentId },
    title,
    position,
    ...size,
  })
}

function createWorkbenchPreset(
  id: WorkspaceMode,
  name: string,
  description: string,
  componentId: string,
  title: string,
): LayoutPreset {
  return {
    id,
    name,
    description,
    apply: (api) => {
      clearAll(api)
      addPanel(api, `${id}-workbench`, componentId, title)
    },
  }
}

function createStagePreset(
  id: Extract<WorkspaceMode, "universe" | "selection" | "timing" | "portfolio" | "risk" | "execution">,
  name: string,
  description: string,
  panels: Array<{ componentId: string; title: string; direction?: "right" | "below"; reference?: number; width?: number; height?: number }>,
): LayoutPreset {
  return {
    id,
    name,
    description,
    apply: (api) => {
      clearAll(api)
      panels.forEach((panel, index) => {
        const panelId = `${id}-panel-${index}`
        const referencePanel = panel.reference == null ? undefined : `${id}-panel-${panel.reference}`
        addPanel(
          api,
          panelId,
          panel.componentId,
          panel.title,
          referencePanel && panel.direction
            ? { referencePanel, direction: panel.direction }
            : undefined,
          { initialWidth: panel.width, initialHeight: panel.height },
        )
      })
    },
  }
}

export const layoutPresets: Record<WorkspaceMode, LayoutPreset> = {
  data: createWorkbenchPreset("data", "数据", "数据获取、预览与质量校验", "data.workbench", "数据工作台"),
  universe: createStagePreset("universe", "标的池", "定义可交易的研究标的", [
    { componentId: "universe.workbench", title: "策略", width: 720 },
    { componentId: "universe.members", title: "标的池", direction: "right", reference: 0, width: 480 },
    { componentId: "universe.chart", title: "证券行情", direction: "below", reference: 1, height: 430 },
  ]),
  selection: createStagePreset("selection", "选股", "横截面信号与选股规则", [
    { componentId: "selection.workbench", title: "策略", width: 680 },
    { componentId: "selection.ranking", title: "选股排名", direction: "right", reference: 0, width: 520 },
    { componentId: "selection.chart", title: "入选证券行情", direction: "below", reference: 1, height: 440 },
  ]),
  timing: createStagePreset("timing", "择时", "时间序列市场仓位覆盖", [
    { componentId: "timing.workbench", title: "策略", width: 620 },
    { componentId: "timing.chart", title: "择时行情与仓位点", direction: "right", reference: 0, width: 660 },
    { componentId: "timing.events", title: "仓位轨迹", direction: "below", reference: 0, height: 340 },
  ]),
  portfolio: createStagePreset("portfolio", "组合", "把选股和择时输出构造成组合", [
    { componentId: "portfolio.workbench", title: "策略", width: 650 },
    { componentId: "portfolio.weights", title: "目标权重", direction: "right", reference: 0, width: 600 },
    { componentId: "portfolio.history", title: "组合轨迹", direction: "below", reference: 1, height: 330 },
  ]),
  risk: createStagePreset("risk", "风控", "仓位、集中度和回撤约束", [
    { componentId: "risk.workbench", title: "策略", width: 640 },
    { componentId: "risk.limits", title: "约束前后", direction: "right", reference: 0, width: 620 },
    { componentId: "risk.history", title: "风险后仓位", direction: "below", reference: 0, height: 330 },
  ]),
  execution: createStagePreset("execution", "执行", "调仓频率、成本与成交假设", [
    { componentId: "execution.workbench", title: "策略", width: 640 },
    { componentId: "execution.settings", title: "执行设置", direction: "right", reference: 0, width: 560 },
    { componentId: "execution.history", title: "调仓与成本", direction: "below", reference: 1, height: 380 },
  ]),
  backtest: createWorkbenchPreset("backtest", "回测", "运行、历史、稳健性与对比", "backtest.workbench", "回测工作台"),
  report: createWorkbenchPreset("report", "报告", "Agent 文档、表格、图表与导出", "report.workbench", "报告工作台"),
}

export const DEFAULT_MODE: WorkspaceMode = "data"

export function normalizeWorkspaceMode(value: unknown): WorkspaceMode {
  if (value === "data" || value === "universe" || value === "selection" || value === "timing" || value === "portfolio" || value === "risk" || value === "execution" || value === "backtest" || value === "report") {
    return value
  }
  return DEFAULT_MODE
}

export const LAYOUT_VERSION = 81
