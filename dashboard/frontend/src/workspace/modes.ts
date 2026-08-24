import {
  BarChart3,
  Database,
  FileText,
  Gauge,
  ListFilter,
  PackageCheck,
  FolderKanban,
  type LucideIcon,
} from "lucide-react"

import type { TranslationKey } from "@/contexts/LanguageContext"
import type { WorkspaceMode } from "@/layouts/presets"

export const MODE_CONFIG: Record<WorkspaceMode, { icon: LucideIcon; labelKey: TranslationKey; detailKey: TranslationKey }> = {
  data: { icon: Database, labelKey: "mode.data", detailKey: "mode.data.long" },
  project: { icon: FolderKanban, labelKey: "mode.project", detailKey: "mode.project.long" },
  selection: { icon: ListFilter, labelKey: "mode.selection", detailKey: "mode.selection.long" },
  portfolio: { icon: PackageCheck, labelKey: "mode.portfolio", detailKey: "mode.portfolio.long" },
  execution: { icon: Gauge, labelKey: "mode.execution", detailKey: "mode.execution.long" },
  backtest: { icon: BarChart3, labelKey: "mode.backtest", detailKey: "mode.backtest.long" },
  report: { icon: FileText, labelKey: "mode.report", detailKey: "mode.report.long" },
}

export const WORKSPACE_MODES = Object.keys(MODE_CONFIG) as WorkspaceMode[]

export const MODE_GROUPS: Array<{ labelKey: TranslationKey; modes: WorkspaceMode[] }> = [
  { labelKey: "sidebar.groupData", modes: ["data", "project"] },
  { labelKey: "sidebar.groupPipeline", modes: ["selection", "portfolio", "execution"] },
  { labelKey: "sidebar.groupReview", modes: ["backtest", "report"] },
]

export const MODE_SHORTCUTS: Record<WorkspaceMode, string[]> = {
  data: ["data.workbench"],
  project: ["project.workbench"],
  selection: ["selection.workbench", "selection.ranking", "selection.chart"],
  portfolio: ["portfolio.workbench", "portfolio.weights", "portfolio.summary"],
  execution: ["execution.workbench", "execution.settings", "execution.targets"],
  backtest: ["backtest.workbench"],
  report: ["report.workbench"],
}
