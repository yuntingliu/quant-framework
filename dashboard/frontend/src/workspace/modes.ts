import {
  BarChart3,
  Database,
  FileText,
  FlaskConical,
  Sigma,
  type LucideIcon,
} from "lucide-react"

import type { TranslationKey } from "@/contexts/LanguageContext"
import type { WorkspaceMode } from "@/layouts/presets"

export const MODE_CONFIG: Record<WorkspaceMode, { icon: LucideIcon; labelKey: TranslationKey; detailKey: TranslationKey }> = {
  data: { icon: Database, labelKey: "mode.data", detailKey: "mode.data.long" },
  factor: { icon: Sigma, labelKey: "mode.factor", detailKey: "mode.factor.long" },
  strategy: { icon: FlaskConical, labelKey: "mode.strategy", detailKey: "mode.strategy.long" },
  backtest: { icon: BarChart3, labelKey: "mode.backtest", detailKey: "mode.backtest.long" },
  report: { icon: FileText, labelKey: "mode.report", detailKey: "mode.report.long" },
}

export const WORKSPACE_MODES = Object.keys(MODE_CONFIG) as WorkspaceMode[]

const CORE_WORKBENCHES = [
  "data.workbench",
  "factor.workbench",
  "strategy.workbench",
  "backtest.workbench",
  "report.workbench",
]

export const MODE_SHORTCUTS: Record<WorkspaceMode, string[]> = {
  data: [...CORE_WORKBENCHES],
  factor: [...CORE_WORKBENCHES],
  strategy: [...CORE_WORKBENCHES],
  backtest: [...CORE_WORKBENCHES],
  report: [...CORE_WORKBENCHES],
}
