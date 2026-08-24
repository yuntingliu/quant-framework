import {
  BarChart3,
  Database,
  FileText,
  FlaskConical,
  Gauge,
  ListFilter,
  FolderKanban,
  type LucideIcon,
} from "lucide-react"

import type { TranslationKey } from "@/contexts/LanguageContext"
import type { WorkspaceMode } from "@/layouts/presets"

export const MODE_CONFIG: Record<WorkspaceMode, { icon: LucideIcon; labelKey: TranslationKey; detailKey: TranslationKey }> = {
  data: { icon: Database, labelKey: "mode.data", detailKey: "mode.data.long" },
  factor: { icon: FlaskConical, labelKey: "mode.factor", detailKey: "mode.factor.long" },
  project: { icon: FolderKanban, labelKey: "mode.project", detailKey: "mode.project.long" },
  selection: { icon: ListFilter, labelKey: "mode.selection", detailKey: "mode.selection.long" },
  execution: { icon: Gauge, labelKey: "mode.execution", detailKey: "mode.execution.long" },
  backtest: { icon: BarChart3, labelKey: "mode.backtest", detailKey: "mode.backtest.long" },
  report: { icon: FileText, labelKey: "mode.report", detailKey: "mode.report.long" },
}

export const WORKSPACE_MODES: WorkspaceMode[] = [
  "data", "factor", "project", "selection", "execution", "backtest", "report",
]

export const MODE_GROUPS: Array<{ labelKey: TranslationKey; modes: WorkspaceMode[] }> = [
  { labelKey: "sidebar.groupData", modes: ["data", "factor", "project"] },
  { labelKey: "sidebar.groupPipeline", modes: ["selection", "execution"] },
  { labelKey: "sidebar.groupReview", modes: ["backtest", "report"] },
]

export const MODE_SHORTCUTS: Record<WorkspaceMode, string[]> = {
  data: ["data.workbench"],
  factor: ["factor.workbench", "factor.library", "factor.editor", "factor.snapshot", "factor.evidence"],
  project: ["project.workbench"],
  selection: ["selection.workbench"],
  execution: ["execution.workbench", "execution.settings", "execution.targets", "execution.costs", "execution.guardrails"],
  backtest: ["backtest.workbench"],
  report: ["report.workbench"],
}
