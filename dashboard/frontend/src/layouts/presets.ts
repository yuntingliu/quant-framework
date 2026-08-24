import type { DockviewApi } from "dockview"

export interface LayoutPreset {
  id: string
  name: string
  description: string
  apply: (api: DockviewApi) => void
}

export type WorkspaceMode = "data" | "factor" | "project" | "selection" | "execution" | "backtest" | "report"

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

function createMultiPanelPreset(
  id: Extract<WorkspaceMode, "execution">,
  name: string,
  description: string,
  panels: Array<{ componentId: string; title: string; direction?: "right" | "below" | "within"; reference?: number; width?: number; height?: number }>,
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
  factor: createWorkbenchPreset("factor", "因子", "因子定义、验证截面与历史证据", "factor.workbench", "因子研究"),
  project: createWorkbenchPreset("project", "研究项目", "项目生命周期、数据环境与股票池", "project.workbench", "研究项目"),
  selection: createWorkbenchPreset("selection", "信号模型", "组合多因子生成横截面排名，并转换成目标仓位", "selection.workbench", "信号模型"),
  execution: createMultiPanelPreset("execution", "执行", "成交时点、流动性与成本假设", [
    { componentId: "execution.workbench", title: "策略组件", width: 520 },
    { componentId: "execution.settings", title: "执行计划", direction: "within", reference: 0 },
    { componentId: "execution.targets", title: "执行目标", direction: "right", reference: 0, width: 580 },
    { componentId: "execution.guardrails", title: "成交门禁", direction: "below", reference: 0, height: 350 },
    { componentId: "execution.costs", title: "成本模型", direction: "within", reference: 3 },
  ]),
  backtest: createWorkbenchPreset("backtest", "回测", "运行、归因、稳健性与对比", "backtest.workbench", "回测工作台"),
  report: createWorkbenchPreset("report", "报告", "Agent 文档、表格、图表与导出", "report.workbench", "报告工作台"),
}

export const DEFAULT_MODE: WorkspaceMode = "data"

export function normalizeWorkspaceMode(value: unknown): WorkspaceMode {
  if (value === "portfolio") return "selection"
  if (value === "data" || value === "factor" || value === "project" || value === "selection" || value === "execution" || value === "backtest" || value === "report") {
    return value
  }
  return DEFAULT_MODE
}

export const LAYOUT_VERSION = 93
