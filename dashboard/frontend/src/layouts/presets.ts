import type { DockviewApi } from "dockview"

export interface LayoutPreset {
  id: string
  name: string
  description: string
  apply: (api: DockviewApi) => void
}

function clearAll(api: DockviewApi) {
  for (const panel of [...api.panels]) panel.api.close()
}

function compactLayout() {
  return typeof window !== "undefined" && window.innerWidth < 760
}

function addPanel(
  api: DockviewApi,
  id: string,
  componentId: string,
  title: string,
  options?: { referencePanel?: string; direction?: "right" | "below" | "within"; inactive?: boolean; initialWidth?: number; initialHeight?: number },
) {
  const reference = options?.referencePanel ? api.panels.find((panel) => panel.id === options.referencePanel) : undefined
  api.addPanel({
    id,
    component: "widget",
    params: { componentId },
    title,
    inactive: options?.inactive,
    initialWidth: options?.initialWidth,
    initialHeight: options?.initialHeight,
    position: reference && options?.direction ? { referencePanel: reference, direction: options.direction } : undefined,
  })
}

const home: LayoutPreset = {
  id: "home", name: "主页", description: "真实历史样本、行情浏览与最近回测",
  apply: (api) => {
    clearAll(api)
    addPanel(api, "summary", "home.index-board", "工作站概览", { initialWidth: 520 })
    addPanel(api, "history", "market.history", "历史行情", { referencePanel: "summary", direction: compactLayout() ? "below" : "right", initialWidth: 620 })
    addPanel(api, "recent-backtests", "backtest.explorer", "最近回测", { referencePanel: compactLayout() ? "history" : "summary", direction: "below", initialHeight: 280 })
  },
}

const data: LayoutPreset = {
  id: "data", name: "数据", description: "市场指标、累计收益与因子统计",
  apply: (api) => {
    clearAll(api)
    addPanel(api, "data-center", "data.center", "数据中心", { initialWidth: 460 })
    addPanel(api, "cumulative-returns", "market.cumulative-returns", "累计收益", { referencePanel: "data-center", direction: compactLayout() ? "below" : "right", initialWidth: 640 })
    addPanel(api, "market-kpi", "market.kpi", "市场 KPI", { referencePanel: "data-center", direction: "below", initialHeight: 300 })
    addPanel(api, "factor-stats", "market.factor-stats", "因子统计", { referencePanel: "market-kpi", direction: "within", inactive: true })
    addPanel(api, "factor-returns", "data.factor-returns", "因子收益", { referencePanel: "cumulative-returns", direction: "within", inactive: true })
    addPanel(api, "logs", "system.log", "系统日志", { referencePanel: "factor-stats", direction: "within", inactive: true })
  },
}

const research: LayoutPreset = {
  id: "research", name: "研究", description: "策略、回测、结果与信号预览",
  apply: (api) => {
    clearAll(api)
    addPanel(api, "strategies", "strategies.hub", "策略模板", { initialWidth: 420 })
    addPanel(api, "editor", "strategy.editor", "策略编辑器", { referencePanel: "strategies", direction: "within", inactive: true })
    addPanel(api, "workbench", "backtest.workbench", "回测工作台", { referencePanel: "strategies", direction: compactLayout() ? "below" : "right", initialWidth: 620 })
    addPanel(api, "signals", "research.signal-preview", "信号预览", { referencePanel: "workbench", direction: "within", inactive: true })
    addPanel(api, "explorer", "backtest.explorer", "回测记录", { referencePanel: "strategies", direction: "below", initialHeight: 260 })
    addPanel(api, "compare", "backtest.compare", "策略对比", { referencePanel: "workbench", direction: "below", initialHeight: 300 })
  },
}

const paper: LayoutPreset = {
  id: "trading_a_share", name: "Paper", description: "离线信号、风控与纸面订单",
  apply: (api) => {
    clearAll(api)
    addPanel(api, "paper-desk", "trading.rebalance", "纸面组合", { initialWidth: 760 })
    addPanel(api, "risk", "trading.alerts", "风险控制", { referencePanel: "paper-desk", direction: compactLayout() ? "below" : "right", initialWidth: 390 })
    addPanel(api, "signal-preview", "research.signal-preview", "信号预览", { referencePanel: "risk", direction: "within", inactive: true })
  },
}

export const layoutPresets: Record<string, LayoutPreset> = {
  home, data, research, monitor: research, trading_a_share: paper, rebalance: paper,
}

export const DEFAULT_MODE = "home"
export type WorkspaceMode = "home" | "data" | "research" | "trading_a_share"

export function normalizeWorkspaceMode(value: unknown): WorkspaceMode {
  if (value === "home" || value === "data" || value === "research" || value === "trading_a_share") return value
  if (value === "monitor") return "research"
  if (value === "rebalance") return "trading_a_share"
  return DEFAULT_MODE
}

export const LAYOUT_VERSION = 68
