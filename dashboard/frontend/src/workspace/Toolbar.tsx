import { useEffect, useRef, useState } from "react"
import {
  HelpCircle,
  Moon,
  Plus,
  RotateCcw,
  Save,
  Sun,
} from "lucide-react"

import { CommandPalette } from "@/components/CommandPalette"
import { Button } from "@/components/ui/button"
import { useLanguage } from "@/contexts/LanguageContext"
import { useTheme } from "@/contexts/ThemeContext"
import { useWorkspace } from "@/contexts/WorkspaceContext"
import type { WorkspaceMode } from "@/layouts/presets"
import { widgetCategory, widgetCatalog, widgetDescription, widgetTitle, widgetTitleById } from "@/widgets/registry"

import { MODE_CONFIG } from "./modes"
import type { WorkspaceTask } from "./types"

// ---------------------------------------------------------------------------
// Toolbar
// ---------------------------------------------------------------------------

export function WorkspaceToolbar({
  onSwitchMode,
  onAddWidget,
  onOpenTask,
  onSaveLayout,
  onResetLayout,
}: {
  onSwitchMode: (mode: WorkspaceMode) => void
  onAddWidget: (widgetId: string, title?: string) => void
  onOpenTask: (task: WorkspaceTask) => void
  onSaveLayout: () => void
  onResetLayout: () => void
}) {
  const { theme, toggleTheme } = useTheme()
  const { activeMode } = useWorkspace()
  const { language, toggleLanguage, t } = useLanguage()
  const [catalogOpen, setCatalogOpen] = useState(false)
  const toolbarRef = useRef<HTMLDivElement>(null)
  const { icon: ActiveModeIcon, labelKey, detailKey } = MODE_CONFIG[activeMode]

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (toolbarRef.current && !toolbarRef.current.contains(e.target as Node)) {
        setCatalogOpen(false)
      }
    }
    document.addEventListener("mousedown", handler)
    return () => document.removeEventListener("mousedown", handler)
  }, [])

  const addWidget = (widgetId: string, title: string) => {
    onAddWidget(widgetId, title)
    setCatalogOpen(false)
  }

  const categories = widgetCatalog.reduce<Record<string, typeof widgetCatalog>>((acc, w) => {
    const category = widgetCategory(w, language)
    ;(acc[category] ??= []).push(w)
    return acc
  }, {})
  for (const widgets of Object.values(categories)) {
    widgets.sort((left, right) => Number(right.status === "active") - Number(left.status === "active"))
  }

  return (
    <div ref={toolbarRef} className="relative z-30 h-10 flex min-w-0 shrink-0 items-center overflow-visible border-b border-border bg-card">
      {/* Command Palette (Ctrl+K) */}
      <CommandPalette
        onAddWidget={addWidget}
        onSwitchMode={onSwitchMode}
        onResetLayout={onResetLayout}
        onOpenTask={onOpenTask}
      />

      {/* Left: current mode context */}
      <div className="flex min-w-0 items-center gap-2 px-3">
        <ActiveModeIcon className="h-4 w-4 shrink-0 text-primary" />
        <div className="min-w-0">
          <div className="truncate text-sm font-semibold text-foreground">{t(labelKey)}</div>
          <div className="hidden truncate text-[10px] text-muted-foreground sm:block">{t(detailKey)}</div>
        </div>
      </div>

      <div className="flex-1" />

      {/* Right: add widget + utilities */}
      <div className="flex shrink-0 items-center gap-0.5 pr-2">
        {/* Add widget catalog */}
        <div className="relative">
          <Button variant="ghost" size="sm" onClick={() => setCatalogOpen(!catalogOpen)} title={t("toolbar.addWidget")}>
            <Plus className="w-4 h-4" />
          </Button>
          {catalogOpen && (
            <div className="absolute top-full right-0 mt-1 w-56 bg-popover border border-border rounded-md shadow-lg z-50 max-h-80 overflow-auto">
              {Object.entries(categories).map(([cat, widgets]) => (
                <div key={cat}>
                  <div className="px-3 py-1.5 text-[10px] font-semibold text-muted-foreground uppercase tracking-wider bg-muted/50">
                    {cat}
                  </div>
                  {widgets.map(w => (
                    <button
                      key={w.id}
                      className="w-full text-left px-3 py-1.5 text-xs hover:bg-muted"
                      onClick={() => addWidget(w.id, widgetTitle(w, language))}
                    >
                      <span>{widgetTitle(w, language)}</span>
                      {w.status === "not_configured" && (
                        <span className="ml-1 text-[10px] text-muted-foreground">
                          {language === "zh" ? "未配置" : "Not configured"}
                        </span>
                      )}
                      {widgetDescription(w, language) && (
                        <span className="text-muted-foreground ml-1">-- {widgetDescription(w, language)}</span>
                      )}
                    </button>
                  ))}
                </div>
              ))}
            </div>
          )}
        </div>

        <Button variant="ghost" size="sm" onClick={onSaveLayout} title={t("toolbar.saveLayout")}>
          <Save className="w-4 h-4" />
        </Button>
        <Button variant="ghost" size="sm" onClick={onResetLayout} title={t("toolbar.resetLayout")}>
          <RotateCcw className="w-4 h-4" />
        </Button>
        <Button variant="ghost" size="sm" onClick={() => addWidget("system.help", widgetTitleById("system.help", language))} title={t("toolbar.help")}>
          <HelpCircle className="w-4 h-4" />
        </Button>
        <Button variant="ghost" size="sm" onClick={toggleTheme} title={t("toolbar.theme")}>
          {theme === "dark" ? <Sun className="w-4 h-4" /> : <Moon className="w-4 h-4" />}
        </Button>
        <Button variant="ghost" size="sm" onClick={toggleLanguage} title={t("toolbar.language")} className="px-2 text-xs">
          {language === "zh" ? "EN" : "中文"}
        </Button>
      </div>
    </div>
  )
}
