import {
  CircleDot,
  Eye,
  EyeOff,
  PanelLeftClose,
  PanelLeftOpen,
  Search,
  X,
} from "lucide-react"

import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover"
import { useLanguage } from "@/contexts/LanguageContext"
import { useWorkspace } from "@/contexts/WorkspaceContext"
import type { WorkspaceMode } from "@/layouts/presets"
import { cn } from "@/lib/utils"

import { MODE_CONFIG, WORKSPACE_MODES } from "./modes"

function dispatchCommandPalette() {
  window.dispatchEvent(new KeyboardEvent("keydown", { key: "k", ctrlKey: true }))
}

interface ModeSidebarProps {
  collapsed: boolean
  lockedCollapsed?: boolean
  onToggleCollapsed: () => void
  onSwitchMode: (mode: WorkspaceMode) => void
  hiddenModes: Set<WorkspaceMode>
  onToggleModeHidden: (mode: WorkspaceMode) => void
}

export function ModeSidebar({
  collapsed,
  lockedCollapsed,
  onToggleCollapsed,
  onSwitchMode,
  hiddenModes,
  onToggleModeHidden,
}: ModeSidebarProps) {
  const { activeMode } = useWorkspace()
  const { t } = useLanguage()
  const visibleModes = WORKSPACE_MODES.filter((mode) => !hiddenModes.has(mode))

  return (
    <aside
      className={cn(
        "flex h-full min-h-0 shrink-0 flex-col border-r border-border bg-card transition-[width] duration-200",
        collapsed ? "w-14" : "w-[232px]",
      )}
    >
      <div className={cn("flex h-12 shrink-0 items-center border-b border-border", collapsed ? "justify-center px-2" : "gap-2 px-3")}>
        <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded border border-primary/25 bg-primary/10 text-xs font-bold text-primary">
          AL
        </div>
        {!collapsed && (
          <div className="min-w-0 flex-1">
            <div className="truncate text-sm font-bold text-foreground">AlphaLab</div>
            <div className="truncate text-[10px] text-muted-foreground">{t("sidebar.workstation")}</div>
          </div>
        )}
      </div>

      <div className="space-y-2 p-2">
        <button
          className={cn(
            "flex h-9 w-full items-center gap-2 rounded border border-border bg-background text-xs text-muted-foreground transition-colors hover:border-primary/40 hover:text-foreground",
            collapsed ? "justify-center px-0" : "px-2",
          )}
          onClick={dispatchCommandPalette}
          title={`${t("toolbar.search")} (Ctrl+K)`}
        >
          <Search className="h-4 w-4 shrink-0" />
          {!collapsed && (
            <>
              <span className="min-w-0 flex-1 truncate text-left">{t("toolbar.search")}</span>
              <kbd className="rounded border border-border bg-muted px-1 py-0.5 font-mono text-[9px]">Ctrl K</kbd>
            </>
          )}
        </button>
      </div>

      <nav className="space-y-1 px-2">
        {visibleModes.map((mode) => {
          const { icon: Icon, labelKey, detailKey } = MODE_CONFIG[mode]
          const isActive = activeMode === mode
          const canHide = !collapsed && visibleModes.length > 1
          return (
            <div key={mode} className="group relative">
              <button
                className={cn(
                  "relative flex min-w-0 items-center rounded text-xs transition-colors",
                  collapsed ? "h-10 w-10 justify-center" : "h-11 w-full gap-2 px-2 text-left",
                  isActive
                    ? "bg-primary/10 text-foreground ring-1 ring-primary/20"
                    : "text-muted-foreground hover:bg-muted/40 hover:text-foreground",
                )}
                onClick={() => onSwitchMode(mode)}
                title={t(detailKey)}
              >
                <Icon className={cn("h-4 w-4 shrink-0", isActive && "text-primary")} />
                {!collapsed && (
                  <span className="min-w-0 flex-1">
                    <span className="block truncate font-medium">{t(labelKey)}</span>
                    <span className="block truncate text-[10px] text-muted-foreground">{t(detailKey)}</span>
                  </span>
                )}
                {isActive && <span className="absolute left-0 top-2 bottom-2 w-0.5 rounded-r bg-primary" />}
              </button>
              {canHide && (
                <button
                  type="button"
                  className="pointer-events-none absolute right-1.5 top-1/2 flex h-6 w-6 -translate-y-1/2 items-center justify-center rounded text-muted-foreground opacity-0 transition-opacity hover:bg-muted hover:text-foreground focus-visible:pointer-events-auto focus-visible:opacity-100 group-hover:pointer-events-auto group-hover:opacity-100"
                  onClick={(e) => {
                    e.stopPropagation()
                    onToggleModeHidden(mode)
                  }}
                  title={t("sidebar.hideTab")}
                  aria-label={t("sidebar.hideTab")}
                >
                  <X className="h-3.5 w-3.5" />
                </button>
              )}
            </div>
          )
        })}
      </nav>

      <div className="min-h-0 flex-1" />

      <div className="mt-auto space-y-1 border-t border-border p-2">
        {!collapsed && (
          <div className="flex items-center gap-2 rounded border border-border bg-background px-2 py-1.5 text-[10px] text-muted-foreground">
            <CircleDot className="h-3.5 w-3.5 text-red-500" />
            <span className="truncate">{t("sidebar.statusHint")}</span>
          </div>
        )}
        <Popover>
          <PopoverTrigger asChild>
            <button
              className={cn(
                "relative flex h-9 w-full items-center gap-2 rounded text-xs text-muted-foreground transition-colors hover:bg-muted/40 hover:text-foreground",
                collapsed ? "justify-center px-0" : "px-2",
              )}
              title={t("sidebar.manageTabs")}
            >
              {hiddenModes.size > 0 ? <EyeOff className="h-4 w-4 shrink-0" /> : <Eye className="h-4 w-4 shrink-0" />}
              {!collapsed && <span className="min-w-0 flex-1 truncate text-left">{t("sidebar.manageTabs")}</span>}
              {hiddenModes.size > 0 && (
                <span
                  className={cn(
                    "rounded-full bg-primary/15 text-[9px] font-semibold text-primary",
                    collapsed ? "absolute right-0.5 top-0.5 flex h-3.5 w-3.5 items-center justify-center" : "px-1.5 py-0.5",
                  )}
                >
                  {hiddenModes.size}
                </span>
              )}
            </button>
          </PopoverTrigger>
          <PopoverContent side="right" align="end" sideOffset={8} className="w-60">
            <div className="mb-1.5 px-1 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
              {t("sidebar.manageTabs")}
            </div>
            <div className="space-y-0.5">
              {WORKSPACE_MODES.map((mode) => {
                const { icon: Icon, labelKey } = MODE_CONFIG[mode]
                const visible = !hiddenModes.has(mode)
                const isLastVisible = visible && visibleModes.length <= 1
                return (
                  <label
                    key={mode}
                    className={cn(
                      "flex items-center gap-2 rounded px-2 py-1.5 text-xs",
                      isLastVisible ? "cursor-not-allowed opacity-50" : "cursor-pointer hover:bg-muted/40",
                    )}
                    title={isLastVisible ? t("sidebar.manageTabsLast") : t(labelKey)}
                  >
                    <input
                      type="checkbox"
                      className="h-3.5 w-3.5 shrink-0 accent-primary"
                      checked={visible}
                      disabled={isLastVisible}
                      onChange={() => onToggleModeHidden(mode)}
                    />
                    <Icon className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                    <span className="min-w-0 flex-1 truncate text-foreground">{t(labelKey)}</span>
                  </label>
                )
              })}
            </div>
            <div className="mt-1.5 border-t border-border px-1 pt-1.5 text-[10px] leading-4 text-muted-foreground">
              {t("sidebar.manageTabsHint")}
            </div>
          </PopoverContent>
        </Popover>
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
