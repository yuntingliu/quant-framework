import {
  Languages,
  Moon,
  PanelLeftClose,
  PanelLeftOpen,
  Sun,
} from "lucide-react"

import { useLanguage } from "@/contexts/LanguageContext"
import { useTheme } from "@/contexts/ThemeContext"
import { useWorkspace } from "@/contexts/WorkspaceContext"
import type { WorkspaceMode } from "@/layouts/presets"
import { cn } from "@/lib/utils"

import { MODE_CONFIG, WORKSPACE_MODES } from "./modes"

interface ModeSidebarProps {
  collapsed: boolean
  lockedCollapsed?: boolean
  onToggleCollapsed: () => void
  onSwitchMode: (mode: WorkspaceMode) => void
}

export function ModeSidebar({
  collapsed,
  lockedCollapsed,
  onToggleCollapsed,
  onSwitchMode,
}: ModeSidebarProps) {
  const { activeMode, selectedStrategy, selectedStrategyEditable, selectedStrategyRevision } = useWorkspace()
  const { language, toggleLanguage, t } = useLanguage()
  const { theme, toggleTheme } = useTheme()
  const projectReady = Boolean(selectedStrategy && selectedStrategyEditable && selectedStrategyRevision != null)

  return (
    <aside
      className={cn(
        "flex h-full min-h-0 shrink-0 flex-col border-r border-border bg-card transition-[width] duration-200",
        collapsed ? "w-14" : "w-[232px]",
      )}
    >
      <div className={cn("flex h-12 shrink-0 items-center border-b border-border", collapsed ? "justify-center px-2" : "gap-2 px-3")}>
        <img className="h-8 w-8 shrink-0" src={`${import.meta.env.BASE_URL}alphalab-logo.png`} alt="" aria-hidden="true" />
        {!collapsed && (
          <div className="min-w-0 flex-1">
            <div className="truncate text-sm font-bold text-foreground">AlphaLab</div>
            <div className="truncate text-[10px] text-muted-foreground">{t("sidebar.workstation")}</div>
          </div>
        )}
      </div>

      <nav className="min-h-0 flex-1 space-y-1 overflow-auto p-2">
        {WORKSPACE_MODES.map((mode) => {
          const { icon: Icon, labelKey, detailKey } = MODE_CONFIG[mode]
          const isActive = activeMode === mode
          const disabled = mode !== "project" && !projectReady
          return (
            <button
              key={mode}
              type="button"
              disabled={disabled}
              aria-disabled={disabled}
              className={cn(
                "relative flex min-w-0 items-center rounded text-xs transition-colors",
                collapsed ? "h-10 w-10 justify-center" : "h-11 w-full gap-2 px-2 text-left",
                disabled
                  ? "cursor-not-allowed text-muted-foreground/35 opacity-60"
                  : isActive
                    ? "bg-primary/10 text-foreground ring-1 ring-primary/20"
                    : "text-muted-foreground hover:bg-muted/40 hover:text-foreground",
              )}
              onClick={() => onSwitchMode(mode)}
              title={disabled ? t("sidebar.projectRequired") : t(detailKey)}
            >
              <Icon className={cn("h-4 w-4 shrink-0", isActive && !disabled && "text-primary")} />
              {!collapsed && (
                <span className="min-w-0 flex-1">
                  <span className="block truncate font-medium">{t(labelKey)}</span>
                  <span className="block truncate text-[10px] text-muted-foreground">
                    {disabled ? t("sidebar.projectRequired") : t(detailKey)}
                  </span>
                </span>
              )}
              {isActive && !disabled && <span className="absolute left-0 top-2 bottom-2 w-0.5 rounded-r bg-primary" />}
            </button>
          )
        })}
      </nav>

      <div className="mt-auto space-y-1 border-t border-border p-2">
        <div className="space-y-1">
          <button
            type="button"
            className={cn(
              "flex h-9 w-full items-center gap-2 rounded text-xs text-muted-foreground transition-colors hover:bg-muted/40 hover:text-foreground",
              collapsed ? "justify-center px-0" : "px-2",
            )}
            onClick={toggleTheme}
            title={t("toolbar.theme")}
          >
            {theme === "dark" ? <Sun className="h-4 w-4 shrink-0" /> : <Moon className="h-4 w-4 shrink-0" />}
            {!collapsed && <span className="truncate">{theme === "dark" ? (language === "zh" ? "浅色" : "Light") : (language === "zh" ? "深色" : "Dark")}</span>}
          </button>
          <button
            type="button"
            className={cn(
              "flex h-9 w-full items-center gap-2 rounded text-xs text-muted-foreground transition-colors hover:bg-muted/40 hover:text-foreground",
              collapsed ? "justify-center px-0" : "px-2",
            )}
            onClick={toggleLanguage}
            title={t("toolbar.language")}
          >
            <Languages className="h-4 w-4 shrink-0" />
            {!collapsed && <span className="truncate">{language === "zh" ? "English" : "中文"}</span>}
          </button>
        </div>
        {!lockedCollapsed && (
          <button
            className={cn(
              "flex h-9 w-full items-center gap-2 rounded text-xs text-muted-foreground transition-colors hover:bg-muted/40 hover:text-foreground",
              collapsed ? "justify-center px-0" : "px-2",
            )}
            onClick={onToggleCollapsed}
            title={collapsed ? t("sidebar.expand") : t("sidebar.collapse")}
          >
            {collapsed ? <PanelLeftOpen className="h-4 w-4" /> : <PanelLeftClose className="h-4 w-4" />}
            {!collapsed && <span className="truncate">{t("sidebar.collapse")}</span>}
          </button>
        )}
      </div>
    </aside>
  )
}
