import type { DockviewApi } from "dockview"

export interface LayoutPreset {
  id: string
  name: string
  description: string
  apply: (api: DockviewApi) => void
}

export type WorkspaceMode = "project" | "data" | "factor" | "strategy" | "validation" | "report"

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
  project: createWorkbenchPreset("project", "研究项目", "创建、选择和管理研究项目", "project.workbench", "研究项目"),
  data: createWorkbenchPreset("data", "数据", "研究范围、数据预览与质量校验", "data.workbench", "数据工作台"),
  factor: createWorkbenchPreset("factor", "因子", "因子定义、验证截面与历史证据", "factor.workbench", "因子研究"),
  strategy: createWorkbenchPreset("strategy", "策略", "信号、组合、状态事件与执行共用一份 Python", "strategy.workbench", "策略工作台"),
  validation: createWorkbenchPreset("validation", "验证", "冻结修订、因子检验、预览、回测与归因", "validation.workbench", "验证与回测"),
  report: createWorkbenchPreset("report", "报告", "Agent 文档、表格、图表与导出", "report.workbench", "报告工作台"),
}

export const DEFAULT_MODE: WorkspaceMode = "project"

export function normalizeWorkspaceMode(value: unknown): WorkspaceMode {
  if (value === "data" || value === "factor" || value === "project" || value === "strategy" || value === "validation" || value === "report") {
    return value
  }
  return DEFAULT_MODE
}

export const LAYOUT_VERSION = 100
