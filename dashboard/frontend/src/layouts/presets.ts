/**
 * Layout presets - pre-configured widget arrangements for common workflows.
 *
 * Each preset is a function that takes a DockviewApi and adds panels.
 */
import type { DockviewApi } from "dockview"

export interface LayoutPreset {
  id: string
  name: string
  description: string
  apply: (api: DockviewApi) => void
}

function clearAll(api: DockviewApi) {
  // Remove all existing panels
  const panels = [...api.panels]
  for (const p of panels) {
    p.api.close()
  }
}

function addPanel(
  api: DockviewApi,
  id: string,
  componentId: string,
  title: string,
  options?: {
    referencePanel?: string
    direction?: "right" | "below" | "left" | "above" | "within"
    inactive?: boolean
    initialWidth?: number
    initialHeight?: number
    minimumWidth?: number
    minimumHeight?: number
    maximumWidth?: number
    maximumHeight?: number
  },
) {
  const ref = options?.referencePanel
    ? api.panels.find(p => p.id === options.referencePanel) ?? undefined
    : undefined

  return api.addPanel({
    id,
    component: "widget",
    params: { componentId },
    title,
    inactive: options?.inactive,
    initialWidth: options?.initialWidth,
    initialHeight: options?.initialHeight,
    minimumWidth: options?.minimumWidth,
    minimumHeight: options?.minimumHeight,
    maximumWidth: options?.maximumWidth,
    maximumHeight: options?.maximumHeight,
    position: ref && options?.direction ? { referencePanel: ref, direction: options.direction } : undefined,
  })
}

// ---------------------------------------------------------------------------
// Preset: Home (live market overview — Electron first-launch default)
// ---------------------------------------------------------------------------

const home: LayoutPreset = {
  id: "home",
  name: "主页",
  description: "实时指数行情 + 点击K线 + 涨跌广度 + 成交活跃 + 行业热力图",
  apply: (api) => {
    clearAll(api)

    addPanel(api, "index-board", "home.index-board", "指数行情", {
      initialHeight: 220,
      minimumWidth: 360,
      minimumHeight: 170,
      maximumHeight: 380,
    })
    addPanel(api, "market-detail", "home.market-detail", "行情详情", {
      referencePanel: "index-board",
      direction: "right",
      initialWidth: 560,
      minimumWidth: 360,
      minimumHeight: 260,
    })
    addPanel(api, "breadth", "home.breadth", "涨跌广度", {
      referencePanel: "index-board",
      direction: "below",
      initialHeight: 340,
      minimumWidth: 320,
      minimumHeight: 220,
    })
    addPanel(api, "sectors", "home.sectors", "行业热力图", {
      referencePanel: "breadth",
      direction: "right",
      initialWidth: 520,
      minimumWidth: 320,
      minimumHeight: 260,
    })
    addPanel(api, "money-flow", "home.money-flow", "成交活跃 / 资金流", {
      referencePanel: "breadth",
      direction: "below",
      initialHeight: 280,
      minimumWidth: 320,
      minimumHeight: 200,
    })
    // Watchlist (live QMT quotes) tabbed beside the market detail — its natural home
    // alongside the live-market overview (moved out of the research preset).
    addPanel(api, "watchlist", "market.watchlist", "自选股", {
      referencePanel: "market-detail",
      direction: "within",
      inactive: true,
    })
    addPanel(api, "ashare-signal-workbench", "research.ashare-signal-workbench", "主力题材", {
      referencePanel: "market-detail",
      direction: "within",
      inactive: true,
      minimumWidth: 760,
      minimumHeight: 520,
    })
  },
}

// ---------------------------------------------------------------------------
// Preset: Data (QMT/RQ workstation)
// ---------------------------------------------------------------------------

const data: LayoutPreset = {
  id: "data",
  name: "数据",
  description: "QMT/RQ 数据中心 + 可切换同步日志",
  apply: (api) => {
    clearAll(api)

    addPanel(api, "data-center", "data.center", "数据中心", {
      minimumWidth: 820,
      minimumHeight: 520,
    })
    addPanel(api, "dolphindb", "data.dolphindb", "DolphinDB", {
      referencePanel: "data-center",
      direction: "within",
      inactive: true,
      minimumWidth: 760,
      minimumHeight: 500,
    })
    addPanel(api, "sync-log", "system.log", "同步日志", {
      referencePanel: "data-center",
      direction: "within",
      inactive: true,
      minimumWidth: 520,
      minimumHeight: 360,
    })
  },
}

// ---------------------------------------------------------------------------
// Preset: Research (factor analysis + signals)
// ---------------------------------------------------------------------------

const research: LayoutPreset = {
  id: "research",
  name: "研究",
  description: "策略研究主从台：左=策略中枢/代理/工作流/选股，右=回测工作台详情",
  apply: (api) => {
    clearAll(api)

    // Master–detail, two panes:
    //  - Left: the strategy-research flow — Strategy Hub (master list, active) + AI
    //    agent, workflows, research screeners, saved-run records.
    //  - Right: the Backtest Workbench as the DETAIL pane. Clicking a Hub row sets
    //    selectedStrategy/selectedBacktest (WorkspaceContext), which the Workbench
    //    reads — so the right pane shows the picked strategy's backtest live.
    // Factor analytics (kpi/cum-returns/factor-stats/drawdowns/annual/correlation)
    // were removed from this preset — they are empty until FF3 factor returns are
    // built and remain addable from the widget picker (市场概览). Watchlist lives in
    // the 主页 preset; data status lives on the Data page.
    addPanel(api, "strategy-hub", "strategies.hub", "策略中枢", {
      minimumWidth: 520,
      minimumHeight: 360,
    })
    addPanel(api, "agent", "research.agent", "AI 研究代理", { referencePanel: "strategy-hub", direction: "within", inactive: true })
    addPanel(api, "workflow-console", "research.workflow-console", "工作流控制台", { referencePanel: "strategy-hub", direction: "within", inactive: true })
    addPanel(api, "valuation-flow", "research.valuation-flow", "Valuation/Flow Picks", { referencePanel: "strategy-hub", direction: "within", inactive: true })
    addPanel(api, "ashare-signal-workbench", "research.ashare-signal-workbench", "A股主力题材", { referencePanel: "strategy-hub", direction: "within", inactive: true, minimumWidth: 760, minimumHeight: 520 })
    addPanel(api, "daily-brief", "research.daily-brief", "每日决策简报", { referencePanel: "strategy-hub", direction: "within", inactive: true })
    addPanel(api, "explorer", "backtest.explorer", "回测记录", { referencePanel: "strategy-hub", direction: "within", inactive: true })

    // Right pane — Workbench shows the selected strategy/backtest detail.
    addPanel(api, "workbench", "backtest.workbench", "回测工作台", {
      referencePanel: "strategy-hub",
      direction: "right",
      initialWidth: 560,
      minimumWidth: 420,
    })
  },
}

// ---------------------------------------------------------------------------
// Preset: QMT Trading (A-share QMT / paper execution)
// ---------------------------------------------------------------------------

const tradingAshare: LayoutPreset = {
  id: "trading_a_share",
  name: "QMT Trading",
  description: "QMT A-share paper/live connection, rebalance cockpit, positions, market data, and orders",
  apply: (api) => {
    clearAll(api)

    // Primary surface: rebalance left, live market context right.
    addPanel(api, "rebalance", "trading.rebalance", "Rebalance Cockpit")
    addPanel(api, "chart", "trading.chart", "Price Chart", { referencePanel: "rebalance", direction: "right" })
    addPanel(api, "ashare-signal-workbench", "research.ashare-signal-workbench", "主力题材", { referencePanel: "chart", direction: "within", inactive: true, minimumWidth: 760, minimumHeight: 520 })
    addPanel(api, "status", "trading.ashare.status", "QMT Trading Setup", { referencePanel: "chart", direction: "above" })

    // Account state and audit history are secondary tabs under the cockpit.
    addPanel(api, "positions", "trading.ashare.positions", "QMT Positions", { referencePanel: "rebalance", direction: "below" })
    addPanel(api, "history", "trading.ashare.history", "QMT Orders/Fills", { referencePanel: "positions", direction: "within" })

    // Manual trading is tabbed below the chart, with market depth active first.
    addPanel(api, "order-book", "trading.ashare.order-book", "QMT Level 2", { referencePanel: "chart", direction: "below" })
    addPanel(api, "order-entry", "trading.ashare.order-entry", "QMT Manual Order", { referencePanel: "order-book", direction: "within", inactive: true })

    // Shared blotter is tabbed beside positions/history for a unified order view.
    addPanel(api, "blotter", "trading.blotter", "Order Blotter", { referencePanel: "positions", direction: "within", inactive: true })
    // Intraday monitor + auto-trade (盯盘自动下单) — its own visible panel, not a hidden tab.
    addPanel(api, "auto-trade", "trading.auto-trade", "盯盘自动下单", {
      referencePanel: "positions",
      direction: "right",
      minimumWidth: 360,
      minimumHeight: 220,
    })
  },
}

// ---------------------------------------------------------------------------
// Export as named map — tabs look up by ID, overview is default
// ---------------------------------------------------------------------------

export const layoutPresets: Record<string, LayoutPreset> = {
  home,
  monitor: research,
  data,
  research,
  trading_a_share: tradingAshare,
  // Compatibility for older localStorage/menu payloads. Runtime mode selection
  // normalizes "rebalance" to "trading_a_share".
  rebalance: tradingAshare,
}

export const DEFAULT_MODE = "home"
export type WorkspaceMode = "home" | "data" | "research" | "trading_a_share"

export function normalizeWorkspaceMode(value: unknown): WorkspaceMode {
  if (value === "home") return "home"
  if (value === "monitor") return "research"
  if (value === "rebalance" || value === "trading_a_share") return "trading_a_share"
  if (value === "data") return "data"
  if (value === "research") return "research"
  return DEFAULT_MODE
}

// Increment this to force cache invalidation when presets/widget defaults change
export const LAYOUT_VERSION = 63
