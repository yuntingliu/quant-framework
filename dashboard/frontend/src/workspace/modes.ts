import {
  BarChart3,
  Database,
  FileText,
  FlaskConical,
  ListFilter,
  FolderKanban,
  SquareTerminal,
  type LucideIcon,
} from "lucide-react"

import type { TranslationKey } from "@/contexts/LanguageContext"
import type { WorkspaceMode } from "@/layouts/presets"

export const MODE_CONFIG: Record<WorkspaceMode, { icon: LucideIcon; labelKey: TranslationKey; detailKey: TranslationKey }> = {
  data: { icon: Database, labelKey: "mode.data", detailKey: "mode.data.long" },
  factor: { icon: FlaskConical, labelKey: "mode.factor", detailKey: "mode.factor.long" },
  project: { icon: FolderKanban, labelKey: "mode.project", detailKey: "mode.project.long" },
  selection: { icon: ListFilter, labelKey: "mode.selection", detailKey: "mode.selection.long" },
  backtest: { icon: BarChart3, labelKey: "mode.backtest", detailKey: "mode.backtest.long" },
  python: { icon: SquareTerminal, labelKey: "mode.python", detailKey: "mode.python.long" },
  report: { icon: FileText, labelKey: "mode.report", detailKey: "mode.report.long" },
}

export const WORKSPACE_MODES: WorkspaceMode[] = [
  "project", "data", "factor", "selection", "backtest", "python", "report",
]

export const MODE_SHORTCUTS: Record<WorkspaceMode, string[]> = {
  data: ["data.workbench"],
  factor: ["factor.workbench", "factor.library", "factor.editor", "factor.snapshot", "factor.evidence"],
  project: ["project.workbench"],
  selection: ["selection.workbench"],
  backtest: ["backtest.workbench"],
  python: ["python.workbench"],
  report: ["report.workbench"],
}
