import {
  Database,
  FlaskConical,
  Home,
  TrendingUp,
  type LucideIcon,
} from "lucide-react"

import type { TranslationKey } from "@/contexts/LanguageContext"
import type { WorkspaceMode } from "@/layouts/presets"

export const MODE_CONFIG: Record<WorkspaceMode, { icon: LucideIcon; labelKey: TranslationKey; detailKey: TranslationKey }> = {
  home: { icon: Home, labelKey: "mode.home", detailKey: "mode.home.long" },
  data: { icon: Database, labelKey: "mode.data", detailKey: "mode.data.long" },
  research: { icon: FlaskConical, labelKey: "mode.research", detailKey: "mode.research.long" },
  trading_a_share: { icon: TrendingUp, labelKey: "mode.tradingAshare", detailKey: "mode.tradingAshare.long" },
}

export const WORKSPACE_MODES = Object.keys(MODE_CONFIG) as WorkspaceMode[]

export const MODE_SHORTCUTS: Record<WorkspaceMode, string[]> = {
  home: [
    "home.index-board",
    "home.market-detail",
    "home.breadth",
    "home.money-flow",
    "home.sectors",
  ],
  data: [
    "data.center",
    "data.dolphindb",
    "data.freshness",
    "system.log",
  ],
  research: [
    "research.agent",
    "backtest.workbench",
    "market.kpi",
    "market.cumulative-returns",
    "market.factor-stats",
    "backtest.runner",
    "backtest.strategies",
  ],
  trading_a_share: [
    "trading.rebalance",
    "trading.ashare.status",
    "trading.ashare.positions",
    "trading.ashare.order-book",
    "trading.blotter",
    "trading.alerts",
    "trading.baskets",
    "trading.drift",
  ],
}
