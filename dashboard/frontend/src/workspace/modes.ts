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
    "market.history",
    "backtest.explorer",
  ],
  data: [
    "data.center",
    "data.factor-returns",
    "system.log",
  ],
  research: [
    "backtest.workbench",
    "strategies.hub",
    "strategy.editor",
    "research.signal-preview",
  ],
  trading_a_share: [
    "trading.rebalance",
    "research.signal-preview",
    "trading.alerts",
  ],
}
