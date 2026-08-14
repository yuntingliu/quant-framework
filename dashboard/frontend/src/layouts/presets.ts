import type { DockviewApi } from "dockview"

export interface LayoutPreset {
  id: string
  name: string
  description: string
  apply: (api: DockviewApi) => void
}

export type WorkspaceMode = "data" | "factor" | "strategy" | "backtest" | "report"

function clearAll(api: DockviewApi) {
  for (const panel of [...api.panels]) panel.api.close()
}

function addPanel(api: DockviewApi, id: string, componentId: string, title: string) {
  api.addPanel({
    id,
    component: "widget",
    params: { componentId },
    title,
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

export const layoutPresets: Record<WorkspaceMode, LayoutPreset> = {
  data: createWorkbenchPreset("data", "数据", "数据获取、预览与质量校验", "data.workbench", "数据工作台"),
  factor: createWorkbenchPreset("factor", "因子", "因子选择、定义与表现分析", "factor.workbench", "因子工作台"),
  strategy: createWorkbenchPreset("strategy", "策略", "策略编辑、校验与版本管理", "strategy.workbench", "策略工作台"),
  backtest: createWorkbenchPreset("backtest", "回测", "运行、历史、稳健性与对比", "backtest.workbench", "回测工作台"),
  report: createWorkbenchPreset("report", "报告", "Agent 文档、表格、图表与导出", "report.workbench", "报告工作台"),
}

export const DEFAULT_MODE: WorkspaceMode = "data"

export function normalizeWorkspaceMode(value: unknown): WorkspaceMode {
  if (value === "data" || value === "factor" || value === "strategy" || value === "backtest" || value === "report") {
    return value
  }
  return DEFAULT_MODE
}

export const LAYOUT_VERSION = 70
