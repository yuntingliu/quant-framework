import {
  BarChart3,
  Boxes,
  CandlestickChart,
  Database,
  FileText,
  Gauge,
  ListFilter,
  PackageCheck,
  ShieldCheck,
  type LucideIcon,
} from "lucide-react"

import type { TranslationKey } from "@/contexts/LanguageContext"
import type { WorkspaceMode } from "@/layouts/presets"

export const MODE_CONFIG: Record<WorkspaceMode, { icon: LucideIcon; labelKey: TranslationKey; detailKey: TranslationKey }> = {
  data: { icon: Database, labelKey: "mode.data", detailKey: "mode.data.long" },
  universe: { icon: Boxes, labelKey: "mode.universe", detailKey: "mode.universe.long" },
  selection: { icon: ListFilter, labelKey: "mode.selection", detailKey: "mode.selection.long" },
  timing: { icon: CandlestickChart, labelKey: "mode.timing", detailKey: "mode.timing.long" },
  portfolio: { icon: PackageCheck, labelKey: "mode.portfolio", detailKey: "mode.portfolio.long" },
  risk: { icon: ShieldCheck, labelKey: "mode.risk", detailKey: "mode.risk.long" },
  execution: { icon: Gauge, labelKey: "mode.execution", detailKey: "mode.execution.long" },
  backtest: { icon: BarChart3, labelKey: "mode.backtest", detailKey: "mode.backtest.long" },
  report: { icon: FileText, labelKey: "mode.report", detailKey: "mode.report.long" },
}

export const WORKSPACE_MODES = Object.keys(MODE_CONFIG) as WorkspaceMode[]

const CORE_WORKBENCHES = [
  "data.workbench",
  "universe.workbench",
  "selection.workbench",
  "timing.workbench",
  "portfolio.workbench",
  "risk.workbench",
  "execution.workbench",
  "backtest.workbench",
  "report.workbench",
]

export const MODE_SHORTCUTS: Record<WorkspaceMode, string[]> = {
  data: [...CORE_WORKBENCHES],
  universe: [...CORE_WORKBENCHES],
  selection: [...CORE_WORKBENCHES],
  timing: [...CORE_WORKBENCHES],
  portfolio: [...CORE_WORKBENCHES],
  risk: [...CORE_WORKBENCHES],
  execution: [...CORE_WORKBENCHES],
  backtest: [...CORE_WORKBENCHES],
  report: [...CORE_WORKBENCHES],
}
