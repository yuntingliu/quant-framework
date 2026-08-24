import type { DockviewApi } from "dockview"

export interface LayoutPreset {
  id: string
  name: string
  description: string
  apply: (api: DockviewApi) => void
}

export type WorkspaceMode = "data" | "project" | "selection" | "portfolio" | "execution" | "backtest" | "report"

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
  id: Extract<WorkspaceMode, "selection" | "portfolio" | "execution">,
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
  project: createWorkbenchPreset("project", "研究项目", "项目、数据环境与数据截至日", "project.workbench", "研究项目"),
  selection: createStagePreset("selection", "选股", "横截面信号与选股规则", [
    { componentId: "selection.workbench", title: "策略", width: 680 },
    { componentId: "selection.ranking", title: "选股排名", direction: "right", reference: 0, width: 520 },
    { componentId: "selection.chart", title: "入选证券行情", direction: "below", reference: 1, height: 440 },
  ]),
  portfolio: createStagePreset("portfolio", "组合", "把选股结果构造成受约束的目标组合", [
    { componentId: "portfolio.workbench", title: "策略", width: 650 },
    { componentId: "portfolio.weights", title: "目标权重", direction: "right", reference: 0, width: 600 },
    { componentId: "portfolio.summary", title: "组合摘要", direction: "below", reference: 1, height: 330 },
  ]),
  execution: createStagePreset("execution", "执行", "调仓频率、成本与成交假设", [
    { componentId: "execution.workbench", title: "策略", width: 640 },
    { componentId: "execution.settings", title: "执行设置", direction: "right", reference: 0, width: 560 },
    { componentId: "execution.targets", title: "执行目标", direction: "below", reference: 1, height: 380 },
  ]),
  backtest: createWorkbenchPreset("backtest", "回测", "运行、归因、稳健性与对比", "backtest.workbench", "回测工作台"),
  report: createWorkbenchPreset("report", "报告", "Agent 文档、表格、图表与导出", "report.workbench", "报告工作台"),
}

export const DEFAULT_MODE: WorkspaceMode = "data"

export function normalizeWorkspaceMode(value: unknown): WorkspaceMode {
  if (value === "data" || value === "project" || value === "selection" || value === "portfolio" || value === "execution" || value === "backtest" || value === "report") {
    return value
  }
  return DEFAULT_MODE
}

export const LAYOUT_VERSION = 85
