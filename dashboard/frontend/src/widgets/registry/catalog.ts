/**
 * Widget catalog metadata and localization helpers.
 */
import type { Language } from "@/contexts/LanguageContext"

export interface WidgetMeta {
  id: string
  title: string
  titleEn?: string
  category: string
  categoryEn?: string
  description?: string
  descriptionEn?: string
  status: WidgetStatus
}

export type WidgetStatus = "active" | "not_configured"

const widgetDefinitions: Omit<WidgetMeta, "status">[] = [
  // One parallel workstation per function boundary; data flows left to right.
  { id: "data.workbench", title: "数据工作台", titleEn: "Data Workbench", category: "研究流程", categoryEn: "Research Flow", description: "数据获取、筛选预览、同步和质量校验", descriptionEn: "Acquire, filter, preview, sync, and validate datasets" },
  { id: "universe.workbench", title: "标的池工作台", titleEn: "Universe Workbench", category: "研究流程", categoryEn: "Research Flow", description: "定义基础池和当期可交易标的", descriptionEn: "Define the base pool and point-in-time tradable universe" },
  { id: "selection.workbench", title: "选股工作台", titleEn: "Selection Workbench", category: "研究流程", categoryEn: "Research Flow", description: "研究横截面信号并选择标的", descriptionEn: "Research cross-sectional signals and select assets" },
  { id: "timing.workbench", title: "择时工作台", titleEn: "Timing Workbench", category: "研究流程", categoryEn: "Research Flow", description: "研究时间序列信号并输出市场仓位", descriptionEn: "Research time-series signals and emit market exposure" },
  { id: "portfolio.workbench", title: "组合工作台", titleEn: "Portfolio Workbench", category: "研究流程", categoryEn: "Research Flow", description: "把选股和择时结果构造成权重", descriptionEn: "Construct weights from selection and timing outputs" },
  { id: "risk.workbench", title: "风控工作台", titleEn: "Risk Workbench", category: "研究流程", categoryEn: "Research Flow", description: "应用仓位、集中度和风险限制", descriptionEn: "Apply exposure, concentration, and risk limits" },
  { id: "execution.workbench", title: "执行工作台", titleEn: "Execution Workbench", category: "研究流程", categoryEn: "Research Flow", description: "定义调仓、成本与成交假设", descriptionEn: "Define rebalancing, cost, and fill assumptions" },
  { id: "universe.members", title: "标的池", titleEn: "Universe Members", category: "标的池", categoryEn: "Universe", description: "查看当前阶段保留的证券", descriptionEn: "Inspect securities retained by the current universe stage" },
  { id: "universe.chart", title: "证券行情", titleEn: "Security Chart", category: "标的池", categoryEn: "Universe", description: "联动查看池内证券历史行情", descriptionEn: "Inspect linked price history for universe members" },
  { id: "selection.ranking", title: "选股排名", titleEn: "Selection Ranking", category: "选股", categoryEn: "Selection", description: "查看真实得分、排名与入选结果", descriptionEn: "Inspect actual scores, ranks, and selections" },
  { id: "selection.chart", title: "入选证券行情", titleEn: "Selected Security Chart", category: "选股", categoryEn: "Selection", description: "联动查看入选证券行情", descriptionEn: "Inspect linked price history for selected securities" },
  { id: "timing.chart", title: "择时行情与仓位点", titleEn: "Timing Chart", category: "择时", categoryEn: "Timing", description: "在行情上标注真实仓位变化", descriptionEn: "Annotate actual exposure changes on price history" },
  { id: "timing.events", title: "仓位轨迹", titleEn: "Exposure Trace", category: "择时", categoryEn: "Timing", description: "查看历史市场仓位与信号", descriptionEn: "Inspect historical market exposure and signals" },
  { id: "portfolio.weights", title: "目标权重", titleEn: "Target Weights", category: "组合", categoryEn: "Portfolio", description: "查看组合阶段生成的目标权重", descriptionEn: "Inspect target weights emitted by portfolio construction" },
  { id: "portfolio.history", title: "组合轨迹", titleEn: "Portfolio Trace", category: "组合", categoryEn: "Portfolio", description: "查看历史标的数、仓位与集中度", descriptionEn: "Inspect historical names, exposure, and concentration" },
  { id: "risk.limits", title: "约束前后", titleEn: "Before and After Risk", category: "风控", categoryEn: "Risk", description: "比较风险约束前后的权重", descriptionEn: "Compare weights before and after risk limits" },
  { id: "risk.history", title: "风险后仓位", titleEn: "Post-risk Exposure", category: "风控", categoryEn: "Risk", description: "查看风险后仓位与现金", descriptionEn: "Inspect post-risk exposure and cash" },
  { id: "execution.settings", title: "执行设置", titleEn: "Execution Settings", category: "执行", categoryEn: "Execution", description: "查看策略实际输出的成交假设", descriptionEn: "Inspect actual fill assumptions emitted by the strategy" },
  { id: "execution.history", title: "调仓与成本", titleEn: "Rebalances and Costs", category: "执行", categoryEn: "Execution", description: "查看历史调仓、换手与模拟成本", descriptionEn: "Inspect historical rebalances, turnover, and modeled costs" },
  { id: "backtest.workbench", title: "回测工作台", titleEn: "Backtest Workbench", category: "研究流程", categoryEn: "Research Flow", description: "运行、历史结果、稳健性、持仓和策略对比", descriptionEn: "Run, inspect history, robustness, holdings, and comparisons" },
  { id: "report.workbench", title: "报告工作台", titleEn: "Report Workbench", category: "研究流程", categoryEn: "Research Flow", description: "查看 Agent 生成的文档、表格、图表和来源", descriptionEn: "Inspect Agent documents, tables, charts, and sources" },

  // Optional extension slots retained outside the core workflow.
  { id: "home.market-detail", title: "行情详情", titleEn: "Market Detail", category: "主页", categoryEn: "Home", description: "点击指数/个股查看真实K线", descriptionEn: "Click index/stock rows to inspect real K-lines" },
  { id: "home.breadth", title: "涨跌广度", titleEn: "Market Breadth", category: "主页", categoryEn: "Home", description: "涨跌家数 + 领涨领跌", descriptionEn: "Advance/decline counts and top movers" },
  { id: "home.sectors", title: "行业热力图", titleEn: "Sector Heatmap", category: "主页", categoryEn: "Home", description: "行业平均涨跌幅", descriptionEn: "Per-industry mean change %" },
  { id: "home.money-flow", title: "成交活跃 / 资金流", titleEn: "Activity & Flow", category: "主页", categoryEn: "Home", description: "成交额代理 + 真实资金流提供方状态", descriptionEn: "Turnover proxy plus true net-flow provider state" },

  // Market
  { id: "market.watchlist", title: "自选股", titleEn: "Watchlist", category: "市场概览", categoryEn: "Market", description: "股票列表 + 行情 + 走势", descriptionEn: "Stock list, quotes, and trend" },

  // Timing

  // Optional strategy and research extensions.
  { id: "ensemble.composer", title: "策略协同", titleEn: "Strategy Ensemble", category: "策略回测", categoryEn: "Backtest", description: "多策略协同：成员 + 元配置法，选中后在「回测工作台」运行", descriptionEn: "Coordinate strategies into one book: members + meta-method; run via Backtest Workbench" },
  { id: "research.workflow-console", title: "工作流控制台", titleEn: "Workflow Console", category: "策略回测", categoryEn: "Backtest", description: "每日核心研究工作流：手动运行、实时状态和历史记录", descriptionEn: "Daily core research workflows with manual runs, live status, and history" },
  { id: "research.valuation-flow", title: "Valuation/Flow Stock Picks", titleEn: "Valuation/Flow Stock Picks", category: "策略回测", categoryEn: "Backtest", description: "PEG/PE markup、资金流和技术信号研究选股器", descriptionEn: "Research screener using valuation, flow, and technical signals" },
  { id: "research.ashare-signal-workbench", title: "A股主力题材工作台", titleEn: "A-share Signal Workbench", category: "策略回测", categoryEn: "Backtest", description: "主力资金、龙虎榜、题材情绪、筹码成本代理和K线标注", descriptionEn: "Main flow, LHB, theme sentiment, cost proxy, and annotated K-lines" },
  { id: "research.daily-brief", title: "每日决策简报", titleEn: "Daily Decision Brief", category: "策略回测", categoryEn: "Backtest", description: "LLM 合成的个股决策卡：信号/信心/参考点位 + 新闻（仅研究）", descriptionEn: "LLM-synthesized per-stock decision cards: signal, confidence, reference levels, news (research only)" },

  // System
  { id: "data.freshness", title: "数据状态", titleEn: "Data Status", category: "系统", categoryEn: "System", description: "QMT/RQ/AkShare 最新日期和更新提醒", descriptionEn: "QMT/RQ/AkShare latest dates and freshness badges" },
  { id: "data.dolphindb", title: "DolphinDB 工作台", titleEn: "DolphinDB Workbench", category: "系统", categoryEn: "System", description: "Level-2 连接、缓存、schema 和资金流样本", descriptionEn: "Level-2 connection, cache, schema, and money-flow samples" },

  // Trading
  { id: "trading.status", title: "交易连接", titleEn: "Trading Setup", category: "实盘交易", categoryEn: "Trading", description: "纸面/实盘连接 + 模式 + 风控", descriptionEn: "Paper/live setup, mode, and risk" },
  { id: "trading.positions", title: "持仓", titleEn: "Positions", category: "实盘交易", categoryEn: "Trading", description: "实时持仓列表", descriptionEn: "Live positions" },
  { id: "trading.order-entry", title: "下单", titleEn: "Order Entry", category: "实盘交易", categoryEn: "Trading", description: "预览 + 执行订单", descriptionEn: "Preview and execute orders" },
  { id: "trading.order-book", title: "L2行情", titleEn: "Order Book", category: "实盘交易", categoryEn: "Trading", description: "五档买卖盘", descriptionEn: "Level 2 bid/ask book" },
  { id: "trading.history", title: "委托/成交", titleEn: "Orders/Fills", category: "实盘交易", categoryEn: "Trading", description: "今日委托记录", descriptionEn: "Today's orders and fills" },
  { id: "trading.chart", title: "K线图", titleEn: "Price Chart", category: "实盘交易", categoryEn: "Trading", description: "TradingView 专业K线", descriptionEn: "TradingView candlestick chart" },
  { id: "trading.rebalance", title: "纸面执行", titleEn: "Paper Execution", category: "纸面交易", categoryEn: "Paper", description: "信号 -> 订单计划 -> 模拟成交", descriptionEn: "Signal, order plan, simulated fills" },
  { id: "trading.ashare.status", title: "QMT交易连接", titleEn: "QMT Trading Setup", category: "QMT交易", categoryEn: "QMT Trading", description: "纸面/QMT 连接 + 风控", descriptionEn: "Paper/QMT setup and risk" },
  { id: "trading.ashare.positions", title: "QMT持仓", titleEn: "QMT Positions", category: "QMT交易", categoryEn: "QMT Trading", description: "QMT/A股持仓列表", descriptionEn: "QMT/A-share positions" },
  { id: "trading.ashare.order-entry", title: "QMT下单", titleEn: "QMT Order Entry", category: "QMT交易", categoryEn: "QMT Trading", description: "A股预览 + 纸面/QMT执行", descriptionEn: "A-share preview and paper/QMT execution" },
  { id: "trading.ashare.order-book", title: "QMT L2行情", titleEn: "QMT Level 2", category: "QMT交易", categoryEn: "QMT Trading", description: "五档买卖盘", descriptionEn: "Level 2 bid/ask book" },
  { id: "trading.ashare.history", title: "QMT委托/成交", titleEn: "QMT Orders/Fills", category: "QMT交易", categoryEn: "QMT Trading", description: "QMT/A股委托记录", descriptionEn: "QMT/A-share orders and fills" },
  // Shared trading widgets (both markets)
  { id: "trading.blotter", title: "订单簿", titleEn: "Order Blotter", category: "实盘交易", categoryEn: "Trading", description: "未结订单 + 撤单/改单（实盘）", descriptionEn: "Open orders with cancel/modify (live)" },
  { id: "trading.greeks", title: "组合希腊值", titleEn: "Portfolio Greeks", category: "实盘交易", categoryEn: "Trading", description: "期权希腊值 + 组合汇总", descriptionEn: "Option Greeks and portfolio aggregate" },
  // trading.depth (券商盘口深度) consolidated into trading.order-book (L2行情): both render a bid/ask
  // ladder, and the order book works live from /trading/quote without an L2 entitlement. The id stays
  // mapped to the order book in components.ts as an alias for saved layouts.
  { id: "trading.alerts", title: "预警中心", titleEn: "Alert Center", category: "实盘交易", categoryEn: "Trading", description: "价格/涨跌幅/成交量预警规则 + 触发记录 + 通知", descriptionEn: "Price/change/volume alert rules with trigger feed and notifications" },
  { id: "trading.factor-exposure", title: "组合因子暴露", titleEn: "Factor Exposure", category: "实盘交易", categoryEn: "Trading", description: "实时组合风格暴露（ETF代理回归）+ 滚动历史", descriptionEn: "Live portfolio style exposures via ETF-proxy regression with rolling history" },
  { id: "trading.baskets", title: "篮子与算法单", titleEn: "Basket Desk", category: "实盘交易", categoryEn: "Trading", description: "暂存篮子批量下单 + TWAP 分片 + 纸面 OCO 止盈止损", descriptionEn: "Staged baskets, TWAP slicing, and paper OCO take-profit/stop-loss" },
  { id: "trading.drift", title: "调仓漂移监控", titleEn: "Drift Monitor", category: "实盘交易", categoryEn: "Trading", description: "实时持仓 vs 信号目标权重，L1 漂移 + 预警联动", descriptionEn: "Live weights vs signal targets with L1 drift and alert integration" },
  { id: "trading.auto-trade", title: "自动交易盯盘", titleEn: "Auto-Trade Desk", category: "实盘交易", categoryEn: "Trading", description: "日内盯盘 + 自动下单：条件触发 / 定时执行信号，模拟→纸面→实盘，武装+死手开关", descriptionEn: "Intraday monitor + auto order placement: conditional triggers / scheduled signal execution, simulate→paper→live, with arm + dead-man's switch" },
]

export const coreWorkflowWidgetIds = [
  "data.workbench",
  "universe.workbench",
  "selection.workbench",
  "timing.workbench",
  "portfolio.workbench",
  "risk.workbench",
  "execution.workbench",
  "backtest.workbench",
  "report.workbench",
] as const

export const stageResultWidgetIds = [
  "universe.members", "universe.chart",
  "selection.ranking", "selection.chart",
  "timing.chart", "timing.events",
  "portfolio.weights", "portfolio.history",
  "risk.limits", "risk.history",
  "execution.settings", "execution.history",
] as const

const activeWidgetIds = new Set<string>([
  ...coreWorkflowWidgetIds,
  ...stageResultWidgetIds,
  "trading.rebalance",
  "trading.alerts",
])

export const widgetCatalog: WidgetMeta[] = widgetDefinitions.map((widget) => ({
  ...widget,
  status: activeWidgetIds.has(widget.id) ? "active" : "not_configured",
}))

export const agentWorkspaceWidgetIds: string[] = [...coreWorkflowWidgetIds, ...stageResultWidgetIds]

export function isActiveWidgetId(widgetId: string): boolean {
  return activeWidgetIds.has(widgetId)
}

export function widgetTitle(widget: WidgetMeta, language: Language): string {
  return language === "en" ? widget.titleEn ?? widget.title : widget.title
}

export function widgetDescription(widget: WidgetMeta, language: Language): string | undefined {
  return language === "en" ? widget.descriptionEn ?? widget.description : widget.description
}

export function widgetCategory(widget: WidgetMeta, language: Language): string {
  return language === "en" ? widget.categoryEn ?? widget.category : widget.category
}

export function widgetTitleById(widgetId: string, language: Language, fallback?: string): string {
  const widget = widgetCatalog.find((item) => item.id === widgetId)
  return widget ? widgetTitle(widget, language) : fallback ?? widgetId
}
