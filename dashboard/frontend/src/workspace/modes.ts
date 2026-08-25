import {
  BadgeCheck,
  Database,
  FileText,
  FlaskConical,
  Code2,
  FolderKanban,
  type LucideIcon,
} from "lucide-react"

import type { TranslationKey } from "@/contexts/LanguageContext"
import type { WorkspaceMode } from "@/layouts/presets"

export const MODE_CONFIG: Record<WorkspaceMode, { icon: LucideIcon; labelKey: TranslationKey; detailKey: TranslationKey }> = {
  data: { icon: Database, labelKey: "mode.data", detailKey: "mode.data.long" },
  factor: { icon: FlaskConical, labelKey: "mode.factor", detailKey: "mode.factor.long" },
  project: { icon: FolderKanban, labelKey: "mode.project", detailKey: "mode.project.long" },
  strategy: { icon: Code2, labelKey: "mode.strategy", detailKey: "mode.strategy.long" },
  validation: { icon: BadgeCheck, labelKey: "mode.validation", detailKey: "mode.validation.long" },
  report: { icon: FileText, labelKey: "mode.report", detailKey: "mode.report.long" },
}

export const WORKSPACE_MODES: WorkspaceMode[] = [
  "project", "data", "factor", "strategy", "validation", "report",
]

export const MODE_SHORTCUTS: Record<WorkspaceMode, string[]> = {
  data: ["data.workbench"],
  factor: ["factor.workbench"],
  project: ["project.workbench"],
  strategy: ["strategy.workbench"],
  validation: ["validation.workbench"],
  report: ["report.workbench"],
}
