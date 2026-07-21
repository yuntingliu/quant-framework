/**
 * Command Palette — Ctrl+K spotlight search for stocks, strategies, widgets, actions.
 *
 * Uses cmdk for fuzzy search + keyboard navigation.
 * Integrates with WorkspaceContext for cross-widget symbol linking.
 */
import { useEffect, useState, useCallback } from "react"
import { Command } from "cmdk"
import {
  Search, BarChart3, Database, FlaskConical, TrendingUp, Home,
  Plus, RotateCcw, Sun, Moon, Workflow, PlayCircle,
} from "lucide-react"
import { useWorkspace } from "@/contexts/WorkspaceContext"
import { useTheme } from "@/contexts/ThemeContext"
import { useLanguage, type TranslationKey } from "@/contexts/LanguageContext"
import { widgetCatalog, widgetDescription, widgetTitle } from "@/widgets/registry"
import type { WorkspaceMode } from "@/layouts/presets"

interface CommandPaletteProps {
  onAddWidget?: (widgetId: string, title: string) => void
  onSwitchMode?: (mode: WorkspaceMode) => void
  onResetLayout?: () => void
  onOpenTask?: (task: CommandTask) => void
}

type CommandTask = "startResearch" | "runBacktest" | "openEvidence" | "resetResearchWorkspace"

const MODE_ITEMS: { mode: WorkspaceMode; labelKey: TranslationKey; icon: typeof BarChart3 }[] = [
  { mode: "home", labelKey: "mode.home.long", icon: Home },
  { mode: "data", labelKey: "mode.data.long", icon: Database },
  { mode: "research", labelKey: "mode.research.long", icon: FlaskConical },
  { mode: "trading_a_share", labelKey: "mode.tradingAshare.long", icon: TrendingUp },
]

// Common A-share stocks for quick search (loaded once)
const QUICK_STOCKS = [
  { code: "000300.SH", name: "沪深300" },
  { code: "000905.SH", name: "中证500" },
  { code: "000852.SH", name: "中证1000" },
  { code: "510300.SH", name: "沪深300ETF" },
  { code: "600519.SH", name: "贵州茅台" },
  { code: "000858.SZ", name: "五粮液" },
  { code: "601318.SH", name: "中国平安" },
  { code: "000001.SZ", name: "平安银行" },
  { code: "600036.SH", name: "招商银行" },
  { code: "601166.SH", name: "兴业银行" },
  { code: "000651.SZ", name: "格力电器" },
  { code: "600276.SH", name: "恒瑞医药" },
  { code: "002415.SZ", name: "海康威视" },
  { code: "300750.SZ", name: "宁德时代" },
  { code: "601012.SH", name: "隆基绿能" },
]

export function CommandPalette({ onAddWidget, onSwitchMode, onResetLayout, onOpenTask }: CommandPaletteProps) {
  const [open, setOpen] = useState(false)
  const { setSelectedSymbol } = useWorkspace()
  const { theme, toggleTheme } = useTheme()
  const { language, t } = useLanguage()

  // Global Ctrl+K shortcut
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") {
        e.preventDefault()
        setOpen(prev => !prev)
      }
      if (e.key === "Escape") {
        setOpen(false)
      }
    }
    window.addEventListener("keydown", handler)
    return () => window.removeEventListener("keydown", handler)
  }, [])

  const selectStock = useCallback((code: string) => {
    setSelectedSymbol(code)
    setOpen(false)
  }, [setSelectedSymbol])

  const selectWidget = useCallback((id: string, title: string) => {
    onAddWidget?.(id, title)
    setOpen(false)
  }, [onAddWidget])

  const selectMode = useCallback((mode: WorkspaceMode) => {
    onSwitchMode?.(mode)
    setOpen(false)
  }, [onSwitchMode])

  const selectTask = useCallback((task: CommandTask) => {
    onOpenTask?.(task)
    setOpen(false)
  }, [onOpenTask])

  if (!open) return null

  return (
    <div className="fixed inset-0 z-[100]">
      {/* Backdrop */}
      <div
        className="absolute inset-0 bg-black/50 backdrop-blur-sm"
        onClick={() => setOpen(false)}
      />

      {/* Command dialog */}
      <div className="absolute top-[20%] left-1/2 -translate-x-1/2 w-[560px] max-w-[90vw]">
        <Command
          className="bg-popover border border-border rounded-lg shadow-2xl overflow-hidden"
          loop
        >
          {/* Search input */}
          <div className="flex items-center gap-2 px-3 border-b border-border">
            <Search className="w-4 h-4 text-muted-foreground shrink-0" />
            <Command.Input
              autoFocus
              placeholder={t("command.placeholder")}
              className="w-full py-3 text-sm bg-transparent outline-none placeholder:text-muted-foreground"
            />
            <kbd className="hidden sm:inline-flex h-5 px-1.5 items-center gap-1 rounded border border-border bg-muted text-[10px] font-mono text-muted-foreground">
              ESC
            </kbd>
          </div>

          <Command.List className="max-h-[360px] overflow-auto p-1.5">
            <Command.Empty className="py-6 text-center text-sm text-muted-foreground">
              {t("command.empty")}
            </Command.Empty>

            {/* Actions */}
            <Command.Group heading={t("command.group.actions")} className="[&_[cmdk-group-heading]]:px-2 [&_[cmdk-group-heading]]:py-1.5 [&_[cmdk-group-heading]]:text-[10px] [&_[cmdk-group-heading]]:font-semibold [&_[cmdk-group-heading]]:text-muted-foreground [&_[cmdk-group-heading]]:uppercase [&_[cmdk-group-heading]]:tracking-wider">
              <Command.Item
                value="开始研究 start research workflow ai quant workflow"
                onSelect={() => selectTask("startResearch")}
                className="flex items-center gap-3 px-2 py-1.5 text-sm rounded cursor-pointer data-[selected=true]:bg-accent/10 data-[selected=true]:text-accent-foreground"
              >
                <Workflow className="w-4 h-4 text-muted-foreground" />
                <span>{t("command.action.startResearch")}</span>
              </Command.Item>
              <Command.Item
                value="运行回测 run selected strategy backtest"
                onSelect={() => selectTask("runBacktest")}
                className="flex items-center gap-3 px-2 py-1.5 text-sm rounded cursor-pointer data-[selected=true]:bg-accent/10 data-[selected=true]:text-accent-foreground"
              >
                <PlayCircle className="w-4 h-4 text-muted-foreground" />
                <span>{t("command.action.runBacktest")}</span>
              </Command.Item>
              <Command.Item
                value="查看证据 open latest evidence backtest workbench records"
                onSelect={() => selectTask("openEvidence")}
                className="flex items-center gap-3 px-2 py-1.5 text-sm rounded cursor-pointer data-[selected=true]:bg-accent/10 data-[selected=true]:text-accent-foreground"
              >
                <BarChart3 className="w-4 h-4 text-muted-foreground" />
                <span>{t("command.action.openEvidence")}</span>
              </Command.Item>
              <Command.Item
                value="重置研究工作台 reset research workspace"
                onSelect={() => selectTask("resetResearchWorkspace")}
                className="flex items-center gap-3 px-2 py-1.5 text-sm rounded cursor-pointer data-[selected=true]:bg-accent/10 data-[selected=true]:text-accent-foreground"
              >
                <RotateCcw className="w-4 h-4 text-muted-foreground" />
                <span>{t("command.action.resetResearch")}</span>
              </Command.Item>
              <Command.Item
                value="重置布局 reset layout"
                onSelect={() => { onResetLayout?.(); setOpen(false) }}
                className="flex items-center gap-3 px-2 py-1.5 text-sm rounded cursor-pointer data-[selected=true]:bg-accent/10 data-[selected=true]:text-accent-foreground"
              >
                <RotateCcw className="w-4 h-4 text-muted-foreground" />
                <span>{t("command.action.resetLayout")}</span>
              </Command.Item>
              <Command.Item
                value="切换主题 toggle theme dark light"
                onSelect={() => { toggleTheme(); setOpen(false) }}
                className="flex items-center gap-3 px-2 py-1.5 text-sm rounded cursor-pointer data-[selected=true]:bg-accent/10 data-[selected=true]:text-accent-foreground"
              >
                {theme === "dark" ? <Sun className="w-4 h-4 text-muted-foreground" /> : <Moon className="w-4 h-4 text-muted-foreground" />}
                <span>{t("command.action.toggleTheme")}</span>
              </Command.Item>
            </Command.Group>

            {/* Stocks */}
            <Command.Group heading={t("command.group.stocks")} className="[&_[cmdk-group-heading]]:px-2 [&_[cmdk-group-heading]]:py-1.5 [&_[cmdk-group-heading]]:text-[10px] [&_[cmdk-group-heading]]:font-semibold [&_[cmdk-group-heading]]:text-muted-foreground [&_[cmdk-group-heading]]:uppercase [&_[cmdk-group-heading]]:tracking-wider">
              {QUICK_STOCKS.map(s => (
                <Command.Item
                  key={s.code}
                  value={`${s.code} ${s.name}`}
                  onSelect={() => selectStock(s.code)}
                  className="flex items-center gap-3 px-2 py-1.5 text-sm rounded cursor-pointer data-[selected=true]:bg-accent/10 data-[selected=true]:text-accent-foreground"
                >
                  <span className="font-mono text-xs text-muted-foreground w-20">{s.code}</span>
                  <span>{s.name}</span>
                </Command.Item>
              ))}
            </Command.Group>

            {/* Modes */}
            <Command.Group heading={t("command.group.modes")} className="[&_[cmdk-group-heading]]:px-2 [&_[cmdk-group-heading]]:py-1.5 [&_[cmdk-group-heading]]:text-[10px] [&_[cmdk-group-heading]]:font-semibold [&_[cmdk-group-heading]]:text-muted-foreground [&_[cmdk-group-heading]]:uppercase [&_[cmdk-group-heading]]:tracking-wider">
              {MODE_ITEMS.map(m => (
                <Command.Item
                  key={m.mode}
                  value={`${t(m.labelKey)} ${m.mode}`}
                  onSelect={() => selectMode(m.mode)}
                  className="flex items-center gap-3 px-2 py-1.5 text-sm rounded cursor-pointer data-[selected=true]:bg-accent/10 data-[selected=true]:text-accent-foreground"
                >
                  <m.icon className="w-4 h-4 text-muted-foreground" />
                  <span>{t(m.labelKey)}</span>
                </Command.Item>
              ))}
            </Command.Group>

            {/* Widgets */}
            <Command.Group heading={t("command.group.widgets")} className="[&_[cmdk-group-heading]]:px-2 [&_[cmdk-group-heading]]:py-1.5 [&_[cmdk-group-heading]]:text-[10px] [&_[cmdk-group-heading]]:font-semibold [&_[cmdk-group-heading]]:text-muted-foreground [&_[cmdk-group-heading]]:uppercase [&_[cmdk-group-heading]]:tracking-wider">
              {[...widgetCatalog]
                .sort((left, right) => Number(right.status === "active") - Number(left.status === "active"))
                .map(w => (
                <Command.Item
                  key={w.id}
                  value={`${w.title} ${w.titleEn ?? ""} ${w.description || ""} ${w.descriptionEn ?? ""} ${w.category} ${w.categoryEn ?? ""}`}
                  onSelect={() => selectWidget(w.id, widgetTitle(w, language))}
                  className="flex items-center gap-3 px-2 py-1.5 text-sm rounded cursor-pointer data-[selected=true]:bg-accent/10 data-[selected=true]:text-accent-foreground"
                >
                  <Plus className="w-3.5 h-3.5 text-muted-foreground" />
                  <span>{widgetTitle(w, language)}</span>
                  {widgetDescription(w, language) && (
                    <span className="text-xs text-muted-foreground ml-auto truncate max-w-[200px]">
                      {widgetDescription(w, language)}
                    </span>
                  )}
                  {w.status === "not_configured" && (
                    <span className="shrink-0 text-[10px] text-muted-foreground">
                      {language === "zh" ? "未配置" : "Not configured"}
                    </span>
                  )}
                </Command.Item>
              ))}
            </Command.Group>

          </Command.List>

          {/* Footer hint */}
          <div className="flex items-center gap-4 px-3 py-2 border-t border-border text-[10px] text-muted-foreground">
            <span className="flex items-center gap-1">
              <kbd className="px-1 py-0.5 rounded border border-border bg-muted font-mono">↑↓</kbd>
              {t("command.nav")}
            </span>
            <span className="flex items-center gap-1">
              <kbd className="px-1 py-0.5 rounded border border-border bg-muted font-mono">↵</kbd>
              {t("command.select")}
            </span>
            <span className="flex items-center gap-1">
              <kbd className="px-1 py-0.5 rounded border border-border bg-muted font-mono">Esc</kbd>
              {t("command.close")}
            </span>
          </div>
        </Command>
      </div>
    </div>
  )
}
