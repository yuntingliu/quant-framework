import type { DockviewApi } from "dockview"

export interface LayoutPreset {
  id: string
  name: string
  description: string
  apply: (api: DockviewApi) => void
}

export type WorkspaceMode = "data" | "factor" | "project" | "selection" | "portfolio" | "execution" | "backtest" | "report"

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
  id: Extract<WorkspaceMode, "selection" | "portfolio" | "execution">,
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
  selection: createMultiPanelPreset("selection", "选股", "横截面信号与选股规则", [
    { componentId: "selection.workbench", title: "策略组件", width: 480 },
    { componentId: "selection.funnel", title: "选股漏斗", direction: "within", reference: 0 },
    { componentId: "selection.ranking", title: "选股排名", direction: "right", reference: 0, width: 560 },
    { componentId: "selection.factor-evidence", title: "因子结构", direction: "below", reference: 0, height: 340 },
    { componentId: "selection.distribution", title: "分数分布", direction: "within", reference: 3 },
    { componentId: "selection.chart", title: "入选证券行情", direction: "below", reference: 2, height: 340 },
  ]),
  portfolio: createMultiPanelPreset("portfolio", "组合", "把选股结果构造成受约束的目标组合", [
    { componentId: "portfolio.workbench", title: "策略组件", width: 500 },
    { componentId: "portfolio.input", title: "选股输入", direction: "within", reference: 0 },
    { componentId: "portfolio.weights", title: "目标权重", direction: "right", reference: 0, width: 600 },
    { componentId: "portfolio.summary", title: "组合摘要", direction: "below", reference: 0, height: 350 },
    { componentId: "portfolio.constraints", title: "约束检查", direction: "within", reference: 3 },
  ]),
  execution: createMultiPanelPreset("execution", "执行", "调仓频率、成本与成交假设", [
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
  if (value === "data" || value === "factor" || value === "project" || value === "selection" || value === "portfolio" || value === "execution" || value === "backtest" || value === "report") {
    return value
  }
  return DEFAULT_MODE
}

export const LAYOUT_VERSION = 89
