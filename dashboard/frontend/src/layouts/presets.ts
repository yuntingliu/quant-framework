import type { DockviewApi } from "dockview"

export interface LayoutPreset {
  id: string
  name: string
  description: string
  apply: (api: DockviewApi) => void
}

export type WorkspaceMode = "project" | "data" | "factor" | "selection" | "backtest" | "python" | "report"

function clearAll(api: DockviewApi) {
  for (const panel of [...api.panels]) panel.api.close()
}

function addPanel(
  api: DockviewApi,
  id: string,
  componentId: string,
  title: string,
) {
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
  project: createWorkbenchPreset("project", "研究项目", "项目生命周期与数据环境", "project.workbench", "研究项目"),
  data: createWorkbenchPreset("data", "数据", "研究范围、数据预览与质量校验", "data.workbench", "数据工作台"),
  factor: createWorkbenchPreset("factor", "因子", "因子定义、验证截面与历史证据", "factor.workbench", "因子研究"),
  selection: createWorkbenchPreset("selection", "信号模型", "组合多因子生成横截面排名，并转换成目标仓位", "selection.workbench", "信号模型"),
  backtest: createWorkbenchPreset("backtest", "回测", "运行、归因、稳健性与对比", "backtest.workbench", "回测工作台"),
  python: createWorkbenchPreset("python", "Python Lab", "受控的自定义 Python 实验与候选提升", "python.workbench", "Python Lab"),
  report: createWorkbenchPreset("report", "报告", "Agent 文档、表格、图表与导出", "report.workbench", "报告工作台"),
}

export const DEFAULT_MODE: WorkspaceMode = "project"

export function normalizeWorkspaceMode(value: unknown): WorkspaceMode {
  if (value === "portfolio") return "selection"
  if (value === "execution") return "backtest"
  if (value === "data" || value === "factor" || value === "project" || value === "selection" || value === "backtest" || value === "python" || value === "report") {
    return value
  }
  return DEFAULT_MODE
}

export const LAYOUT_VERSION = 95
