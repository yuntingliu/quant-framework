/**
 * Workspace - dockview layout with Overview / Data / Research / Trading modes.
 *
 * Features:
 *   - Mode switching with per-mode layout persistence (localStorage)
 *   - "Save Custom" persists current arrangement for the active mode
 *   - "Reset" reloads the default preset for the active mode
 *   - Widget catalog dropdown for adding individual panels
 */
import { useCallback, useEffect, useRef, useState } from "react"
import { useQueryClient } from "@tanstack/react-query"
import {
  DockviewReact,
  type DockviewReadyEvent,
  type DockviewApi,
  type IDockviewPanelProps,
} from "dockview"
import "dockview/dist/styles/dockview.css"
import { toast } from "sonner"

import { widgetComponents, widgetTitleById } from "@/widgets/registry"
import { StatusBar } from "@/widgets/StatusBar"
import { LaunchSyncBanner } from "@/widgets/home/LaunchSyncBanner"
import { layoutPresets, LAYOUT_VERSION, normalizeWorkspaceMode, type WorkspaceMode } from "@/layouts/presets"
import { WorkspaceProvider, useWorkspace, type LinkGroup } from "@/contexts/WorkspaceContext"
import { PanelContext } from "@/contexts/PanelContext"
import { useAgentPrompt } from "@/contexts/AgentPromptContext"
import { useTradingHotkeys } from "@/hooks/useTradingHotkeys"
import { useAlertNotifications } from "@/lib/notifications"
import { useLanguage } from "@/contexts/LanguageContext"
import { cn } from "@/lib/utils"
import { ModeSidebar } from "@/workspace/ModeSidebar"
import { WorkspaceRightRail } from "@/workspace/RightRail"
import { WorkspaceToolbar } from "@/workspace/Toolbar"
import {
  WORKSPACE_COMMAND_EVENT,
  type AgentWorkspaceCommand,
  type AgentWorkspaceCommandEventDetail,
  type AgentWorkspaceCommandReceipt,
} from "@/workspace/agentCommands"
import { MODE_CONFIG, WORKSPACE_MODES } from "@/workspace/modes"
import type { AgentResearchResult } from "@/workspace/researchResults"
import type { RightRailTab, WorkspaceTask } from "@/workspace/types"

interface ElectronMenuApi {
  setLanguage?: (language: "zh" | "en") => Promise<void>
  onMenuSwitchMode: (cb: (mode: string) => void) => () => void
  onMenuResetLayout: (cb: () => void) => () => void
  onMenuOpenWidget: (cb: (widgetId: string, title: string, targetMode?: string) => void) => () => void
  onMenuClearLayouts: (cb: () => void) => () => void
}

type ElectronWindow = Window & {
  api?: ElectronMenuApi
}

interface PendingWidgetOpen {
  widgetId: string
  title?: string
  mode?: WorkspaceMode
  panelId?: string
  params?: Record<string, unknown>
}

interface WidgetPanelOptions {
  panelId?: string
  params?: Record<string, unknown>
}

interface OpenWidgetEventDetail {
  widgetId: string
  title?: string
  mode?: string
}

// ---------------------------------------------------------------------------
// Panel renderer
// ---------------------------------------------------------------------------

function PanelRenderer(props: IDockviewPanelProps) {
  const { t } = useLanguage()
  const componentId = props.params?.componentId as string
  const panelParams = (props.params ?? {}) as Record<string, unknown>
  const [linkGroup, setLinkGroupState] = useState<LinkGroup | null>(
    (props.params?.linkGroup as LinkGroup | undefined) ?? null,
  )
  const setLinkGroup = useCallback((group: LinkGroup | null) => {
    setLinkGroupState(group)
    // Persist into the panel params so saved layouts keep the assignment.
    try { props.api.updateParameters({ linkGroup: group }) } catch { /* ignore */ }
  }, [props.api])
  const Component = widgetComponents[componentId]
  if (!Component) {
    return (
      <div className="h-full flex items-center justify-center text-muted-foreground text-sm">
        {t("common.unknownWidget")}: {componentId}
      </div>
    )
  }
  return (
    <PanelContext.Provider value={{ panelId: props.api.id, componentId, params: panelParams, linkGroup, setLinkGroup }}>
      <Component />
    </PanelContext.Provider>
  )
}

const components = { widget: PanelRenderer }

// ---------------------------------------------------------------------------
// Per-mode layout persistence
// ---------------------------------------------------------------------------

function layoutKey(mode: WorkspaceMode) {
  return `alphalab-layout-${mode}`
}

const VERSION_KEY = "alphalab-layout-version"
const HIDDEN_MODES_KEY = "alphalab-hidden-modes"
const SIDEBAR_COLLAPSED_KEY = "alphalab-sidebar-collapsed"
const RIGHT_RAIL_COLLAPSED_KEY = "alphalab-right-sidebar-collapsed"
const RIGHT_RAIL_TAB_KEY = "alphalab-right-sidebar-tab"
interface OpenTaskEventDetail {
  task: WorkspaceTask
}

function loadHiddenModes(): Set<WorkspaceMode> {
  try {
    const raw = localStorage.getItem(HIDDEN_MODES_KEY)
    if (!raw) return new Set()
    const parsed = JSON.parse(raw)
    if (!Array.isArray(parsed)) return new Set()
    const valid = parsed.filter((m): m is WorkspaceMode => WORKSPACE_MODES.includes(m as WorkspaceMode))
    // Never start with every tab hidden — keep at least one visible.
    if (valid.length >= WORKSPACE_MODES.length) return new Set()
    return new Set(valid)
  } catch {
    return new Set()
  }
}

function loadSidebarCollapsed(): boolean {
  try {
    const saved = localStorage.getItem(SIDEBAR_COLLAPSED_KEY)
    if (saved !== null) return saved === "true"
    return typeof window !== "undefined" ? window.innerWidth < 1024 : false
  } catch {
    return false
  }
}

function loadRightRailCollapsed(): boolean {
  try {
    const saved = localStorage.getItem(RIGHT_RAIL_COLLAPSED_KEY)
    if (saved !== null) return saved === "true"
    return true
  } catch {
    return true
  }
}

function loadRightRailTab(): RightRailTab {
  try {
    const saved = localStorage.getItem(RIGHT_RAIL_TAB_KEY)
    if (saved === "agent" || saved === "activity") return saved
  } catch {
    // ignore
  }
  return "context"
}

function isViewportBelow(breakpoint: number): boolean {
  return typeof window !== "undefined" ? window.innerWidth < breakpoint : false
}

function useViewportBelow(breakpoint: number) {
  const [matches, setMatches] = useState(() => isViewportBelow(breakpoint))

  useEffect(() => {
    const handler = () => setMatches(isViewportBelow(breakpoint))
    const media = window.matchMedia(`(max-width: ${breakpoint - 1}px)`)
    handler()
    window.addEventListener("resize", handler)
    window.visualViewport?.addEventListener("resize", handler)
    media.addEventListener("change", handler)
    return () => {
      window.removeEventListener("resize", handler)
      window.visualViewport?.removeEventListener("resize", handler)
      media.removeEventListener("change", handler)
    }
  }, [breakpoint])

  return matches
}

function saveLayout(api: DockviewApi, mode: WorkspaceMode) {
  try {
    localStorage.setItem(layoutKey(mode), JSON.stringify(api.toJSON()))
    localStorage.setItem(VERSION_KEY, String(LAYOUT_VERSION))
  } catch { /* ignore */ }
}

function loadLayout(api: DockviewApi, mode: WorkspaceMode): boolean {
  try {
    // Invalidate cache if preset version changed
    const savedVer = parseInt(localStorage.getItem(VERSION_KEY) || "0")
    if (savedVer < LAYOUT_VERSION) {
      // Clear all saved layouts — presets changed
      for (const key of Object.keys(localStorage)) {
        if (key.startsWith("alphalab-layout-")) localStorage.removeItem(key)
      }
      localStorage.setItem(VERSION_KEY, String(LAYOUT_VERSION))
      return false
    }

    const saved = localStorage.getItem(layoutKey(mode))
    if (!saved) return false
    api.fromJSON(JSON.parse(saved))
    return true
  } catch {
    localStorage.removeItem(layoutKey(mode))
    return false
  }
}

function applyMode(api: DockviewApi, mode: WorkspaceMode) {
  if (!loadLayout(api, mode)) {
    const preset = layoutPresets[mode]
    if (preset) preset.apply(api)
  }
}

function relabelPanels(api: DockviewApi, language: "zh" | "en") {
  for (const panel of api.panels) {
    const params = panel.params as { componentId?: string } | undefined
    const componentId = params?.componentId ?? panel.id
    panel.api.setTitle(widgetTitleById(componentId, language, panel.title))
  }
}

function findWidgetPanel(api: DockviewApi, componentId: string) {
  return api.panels.find((panel) => {
    const params = panel.params as { componentId?: string } | undefined
    return panel.id === componentId || params?.componentId === componentId
  })
}

// ---------------------------------------------------------------------------
// Workspace
// ---------------------------------------------------------------------------

function WorkspaceInner() {
  const queryClient = useQueryClient()
  const { registerResearchResult } = useAgentPrompt()
  const apiRef = useRef<DockviewApi | null>(null)
  const apiRefsRef = useRef<Partial<Record<WorkspaceMode, DockviewApi>>>({})
  const pendingWidgetOpenRef = useRef<PendingWidgetOpen | null>(null)
  const workspace = useWorkspace()
  const { activeMode, setActiveMode } = workspace
  const { language } = useLanguage()
  const activeModeRef = useRef(activeMode)
  const [mountedModes, setMountedModes] = useState<Set<WorkspaceMode>>(() => new Set([activeMode]))
  const [sidebarCollapsed, setSidebarCollapsed] = useState(loadSidebarCollapsed)
  const [rightRailCollapsed, setRightRailCollapsed] = useState(loadRightRailCollapsed)
  const [rightRailTab, setRightRailTab] = useState<RightRailTab>(loadRightRailTab)
  const [hiddenModes, setHiddenModes] = useState<Set<WorkspaceMode>>(loadHiddenModes)
  const compactViewport = useViewportBelow(1024)
  const narrowViewport = useViewportBelow(768)
  useEffect(() => { activeModeRef.current = activeMode }, [activeMode])

  useEffect(() => {
    for (const [mode, api] of Object.entries(apiRefsRef.current) as [WorkspaceMode, DockviewApi][]) {
      if (!api) continue
      relabelPanels(api, language)
      saveLayout(api, mode)
    }
  }, [language])

  const switchMode = useCallback((rawMode: unknown) => {
    const mode = normalizeWorkspaceMode(rawMode)
    if (!(mode in MODE_CONFIG)) return
    // Navigating to a hidden tab (command palette, menu, programmatic) restores it.
    setHiddenModes((prev) => {
      if (!prev.has(mode)) return prev
      const next = new Set(prev)
      next.delete(mode)
      return next
    })
    if (mode === activeModeRef.current) return
    const previousMode = activeModeRef.current
    const previousApi = apiRefsRef.current[previousMode]
    if (previousApi) saveLayout(previousApi, previousMode)
    setMountedModes(prev => {
      if (prev.has(mode)) return prev
      const next = new Set(prev)
      next.add(mode)
      return next
    })
    setActiveMode(mode)
    activeModeRef.current = mode
    const nextApi = apiRefsRef.current[mode] ?? null
    apiRef.current = nextApi
    if (nextApi) {
      relabelPanels(nextApi, language)
      saveLayout(nextApi, mode)
    }
  }, [language, setActiveMode])

  const addWidgetToApi = useCallback((
    api: DockviewApi,
    rawWidgetId: string,
    title?: string,
    options?: WidgetPanelOptions,
  ) => {
    // Legacy alias: the "AI 量化工作流" mock merged into the unified research agent.
    // Collapse any open/saved request for the old id onto the agent panel so a
    // stale layout never spawns a duplicate or blank tab.
    const widgetId = rawWidgetId === "research.ai-workflow" ? "research.agent" : rawWidgetId
    const panelId = options?.panelId ?? widgetId
    const existing = options?.panelId
      ? api.panels.find((panel) => panel.id === panelId)
      : findWidgetPanel(api, widgetId)
    if (existing) {
      existing.api.setActive()
      existing.focus()
      return
    }
    const referencePanel = api.activePanel ?? api.panels[0]
    api.addPanel({
      id: panelId,
      component: "widget",
      params: { componentId: widgetId, ...(options?.params ?? {}) },
      title: widgetTitleById(widgetId, language, title),
      position: referencePanel
        ? { referencePanel, direction: "within" }
        : undefined,
    })
  }, [language])

  const addWidget = useCallback((widgetId: string, title?: string) => {
    if (!apiRef.current) return
    addWidgetToApi(apiRef.current, widgetId, title)
  }, [addWidgetToApi])

  const openWidget = useCallback((
    widgetId: string,
    title?: string,
    rawMode?: unknown,
    options?: WidgetPanelOptions,
  ) => {
    const targetMode = rawMode ? normalizeWorkspaceMode(rawMode) : activeModeRef.current
    const targetApi = apiRefsRef.current[targetMode] ?? null

    if (targetMode !== activeModeRef.current) {
      if (targetApi) {
        switchMode(targetMode)
        requestAnimationFrame(() => {
          apiRef.current = targetApi
          addWidgetToApi(targetApi, widgetId, title, options)
        })
      } else {
        pendingWidgetOpenRef.current = { widgetId, title, mode: targetMode, ...options }
        switchMode(targetMode)
      }
      return
    }

    if (targetApi) {
      apiRef.current = targetApi
      addWidgetToApi(targetApi, widgetId, title, options)
      return
    }

    pendingWidgetOpenRef.current = { widgetId, title, mode: targetMode, ...options }
  }, [addWidgetToApi, switchMode])

  const toggleSidebarCollapsed = useCallback(() => {
    setSidebarCollapsed((prev) => {
      const next = !prev
      try { localStorage.setItem(SIDEBAR_COLLAPSED_KEY, String(next)) } catch { /* ignore */ }
      return next
    })
  }, [])

  const toggleRightRailCollapsed = useCallback(() => {
    setRightRailCollapsed((prev) => {
      const next = !prev
      try { localStorage.setItem(RIGHT_RAIL_COLLAPSED_KEY, String(next)) } catch { /* ignore */ }
      return next
    })
  }, [])

  const toggleModeHidden = useCallback((mode: WorkspaceMode) => {
    if (!hiddenModes.has(mode)) {
      // Hiding: keep at least one tab visible, and if we're hiding the tab the
      // user is currently on, redirect in the same commit so its panel never
      // renders without a matching nav button (avoids a 1-frame flash).
      const visibleAfter = WORKSPACE_MODES.filter((m) => m !== mode && !hiddenModes.has(m))
      if (visibleAfter.length === 0) return
      if (mode === activeMode) switchMode(visibleAfter[0])
    }
    setHiddenModes((prev) => {
      const next = new Set(prev)
      if (next.has(mode)) next.delete(mode)
      else next.add(mode)
      return next
    })
  }, [hiddenModes, activeMode, switchMode])

  // Single source of truth for tab-visibility persistence.
  useEffect(() => {
    try { localStorage.setItem(HIDDEN_MODES_KEY, JSON.stringify([...hiddenModes])) } catch { /* ignore */ }
  }, [hiddenModes])

  // Safety net: if the active tab ends up hidden by a path other than the
  // synchronous redirect above (e.g. forced startup mode, corrupted storage),
  // fall back to the first still-visible tab.
  useEffect(() => {
    if (!hiddenModes.has(activeMode)) return
    const firstVisible = WORKSPACE_MODES.find((mode) => !hiddenModes.has(mode))
    if (firstVisible) switchMode(firstVisible)
  }, [hiddenModes, activeMode, switchMode])

  const selectRightRailTab = useCallback((tab: RightRailTab) => {
    setRightRailTab(tab)
    try { localStorage.setItem(RIGHT_RAIL_TAB_KEY, tab) } catch { /* ignore */ }
  }, [])

  const openTask = useCallback((task: WorkspaceTask) => {
    selectRightRailTab("context")

    if (task === "resetResearchWorkspace") {
      localStorage.removeItem(layoutKey("research"))
      const preset = layoutPresets.research
      const researchApi = apiRefsRef.current.research
      if (preset && researchApi) {
        preset.apply(researchApi)
        relabelPanels(researchApi, language)
        saveLayout(researchApi, "research")
      }
      switchMode("research")
      return
    }

    if (task === "startResearch") {
      openWidget("research.agent", undefined, "research")
      return
    }

    if (task === "runBacktest") {
      openWidget("backtest.workbench", undefined, "research")
      return
    }

    const evidenceWidget = workspace.selectedBacktest ? "backtest.workbench" : "backtest.explorer"
    openWidget(evidenceWidget, undefined, "research")
  }, [language, openWidget, selectRightRailTab, switchMode, workspace.selectedBacktest])

  const saveCurrentLayout = useCallback(() => {
    const currentMode = activeModeRef.current
    const currentApi = apiRefsRef.current[currentMode] ?? apiRef.current
    if (currentApi) saveLayout(currentApi, currentMode)
  }, [])

  const resetCurrentLayout = useCallback(() => {
    const currentMode = activeModeRef.current
    localStorage.removeItem(layoutKey(currentMode))
    const preset = layoutPresets[currentMode]
    const currentApi = apiRefsRef.current[currentMode] ?? apiRef.current
    if (preset && currentApi) {
      preset.apply(currentApi)
      relabelPanels(currentApi, language)
      saveLayout(currentApi, currentMode)
    }
  }, [language])

  const executeWorkspaceCommand = useCallback((
    command: AgentWorkspaceCommand,
    researchResult?: AgentResearchResult,
  ): Omit<AgentWorkspaceCommandReceipt, "index"> => {
    switch (command.type) {
      case "switch_mode":
        switchMode(command.mode)
        return { type: command.type, success: true, message: `已切换到 ${command.mode}` }
      case "open_widget":
        if (!widgetComponents[command.widgetId]) {
          return { type: command.type, success: false, message: `未知组件：${command.widgetId}` }
        }
        openWidget(command.widgetId, command.title, command.mode)
        return {
          type: command.type,
          success: true,
          message: `已打开 ${widgetTitleById(command.widgetId, language)}`,
        }
      case "open_result": {
        if (!researchResult || researchResult.id !== command.resultId) {
          return { type: command.type, success: false, message: `未找到研究结果：${command.resultId}` }
        }
        registerResearchResult(researchResult)
        const panelId = `research-result-${command.resultId.replace(/[^a-zA-Z0-9_-]/g, "-")}`
        openWidget(
          "research.result-viewer",
          command.title ?? researchResult.title,
          command.mode ?? "research",
          { panelId, params: { resultId: researchResult.id } },
        )
        return { type: command.type, success: true, message: `已打开研究结果：${researchResult.title}` }
      }
      case "close_widget": {
        const targetMode = command.mode ?? activeModeRef.current
        const api = apiRefsRef.current[targetMode]
        const panel = api ? findWidgetPanel(api, command.widgetId) : undefined
        if (!panel) {
          return { type: command.type, success: false, message: `未找到组件：${command.widgetId}` }
        }
        panel.api.close()
        return {
          type: command.type,
          success: true,
          message: `已关闭 ${widgetTitleById(command.widgetId, language)}`,
        }
      }
      case "set_focus": {
        const changed: string[] = []
        if (Object.prototype.hasOwnProperty.call(command, "symbol")) {
          workspace.setSelectedSymbol(command.symbol ?? null)
          changed.push(`标的 ${command.symbol ?? "已清除"}`)
        }
        if (Object.prototype.hasOwnProperty.call(command, "strategyId")) {
          workspace.setSelectedStrategy(command.strategyId ?? null)
          changed.push(`策略 ${command.strategyId ?? "已清除"}`)
        }
        if (Object.prototype.hasOwnProperty.call(command, "backtestId")) {
          workspace.setSelectedBacktest(command.backtestId ?? null)
          changed.push(`回测 ${command.backtestId ?? "已清除"}`)
        }
        if (Object.prototype.hasOwnProperty.call(command, "date")) {
          workspace.setSelectedDate(command.date ?? null)
          changed.push(`日期 ${command.date ?? "已清除"}`)
        }
        return { type: command.type, success: true, message: `已更新焦点：${changed.join("，")}` }
      }
      case "set_link_symbol":
        workspace.setLinkSymbol(command.group, command.symbol)
        return {
          type: command.type,
          success: true,
          message: `联动组 ${command.group.toUpperCase()} 已更新`,
        }
      case "show_right_rail": {
        if (command.tab) selectRightRailTab(command.tab)
        const open = command.open ?? true
        setRightRailCollapsed(!open)
        try { localStorage.setItem(RIGHT_RAIL_COLLAPSED_KEY, String(!open)) } catch { /* ignore */ }
        return {
          type: command.type,
          success: true,
          message: open ? "已展开右侧栏" : "已收起右侧栏",
        }
      }
      case "refresh_data":
        void queryClient.invalidateQueries()
        window.dispatchEvent(new CustomEvent("alphalab:refreshData"))
        return { type: command.type, success: true, message: "已刷新工作台数据" }
      case "save_layout": {
        const targetMode = command.mode ?? activeModeRef.current
        const api = apiRefsRef.current[targetMode]
        if (!api) {
          return { type: command.type, success: false, message: `${targetMode} 布局尚未挂载` }
        }
        saveLayout(api, targetMode)
        return { type: command.type, success: true, message: `已保存 ${targetMode} 布局` }
      }
      case "reset_layout": {
        const targetMode = command.mode ?? activeModeRef.current
        localStorage.removeItem(layoutKey(targetMode))
        const preset = layoutPresets[targetMode]
        const api = apiRefsRef.current[targetMode]
        if (preset && api) {
          preset.apply(api)
          relabelPanels(api, language)
          saveLayout(api, targetMode)
        }
        return { type: command.type, success: true, message: `已重置 ${targetMode} 布局` }
      }
    }
  }, [
    language,
    openWidget,
    queryClient,
    registerResearchResult,
    selectRightRailTab,
    switchMode,
    workspace,
  ])

  useEffect(() => {
    const api = apiRefsRef.current[activeMode]
    apiRef.current = api ?? null
    if (!api) return
    requestAnimationFrame(() => {
      api.layout(api.width, api.height, true)
      api.focus()
    })
  }, [activeMode, compactViewport, narrowViewport, sidebarCollapsed, rightRailCollapsed])

  const onReady = useCallback((mode: WorkspaceMode, event: DockviewReadyEvent) => {
    apiRefsRef.current[mode] = event.api
    if (mode === activeModeRef.current) apiRef.current = event.api
    applyMode(event.api, mode)
    relabelPanels(event.api, language)
    const pending = pendingWidgetOpenRef.current
    if (pending && (!pending.mode || pending.mode === mode)) {
      pendingWidgetOpenRef.current = null
      requestAnimationFrame(() => {
        if (mode === activeModeRef.current) apiRef.current = event.api
        addWidgetToApi(event.api, pending.widgetId, pending.title, {
          panelId: pending.panelId,
          params: pending.params,
        })
      })
    }
    event.api.onDidLayoutChange(() => {
      saveLayout(event.api, mode)
    })
  }, [addWidgetToApi, language])

  // Electron menu IPC events
  useEffect(() => {
    const w = window as ElectronWindow
    if (!w.api?.onMenuSwitchMode) return  // not in Electron

    const cleanups = [
      w.api.onMenuSwitchMode((mode: string) => {
        switchMode(mode)
      }),
      w.api.onMenuResetLayout(() => {
        resetCurrentLayout()
      }),
      w.api.onMenuOpenWidget((widgetId: string, title: string, targetMode?: string) => {
        openWidget(widgetId, title, targetMode)
      }),
      w.api.onMenuClearLayouts(() => {
        for (const mode of Object.keys(layoutPresets)) {
          localStorage.removeItem(layoutKey(mode as WorkspaceMode))
        }
        const currentMode = activeModeRef.current
        const preset = layoutPresets[currentMode]
        const currentApi = apiRefsRef.current[currentMode]
        if (preset && currentApi) {
          preset.apply(currentApi)
          relabelPanels(currentApi, language)
          saveLayout(currentApi, currentMode)
        }
      }),
    ]

    return () => cleanups.forEach(fn => fn?.())
  }, [language, openWidget, resetCurrentLayout, switchMode])

  useEffect(() => {
    const handler = (event: Event) => {
      const mode = (event as CustomEvent<string>).detail
      switchMode(mode)
    }
    window.addEventListener("alphalab:switchMode", handler)
    return () => window.removeEventListener("alphalab:switchMode", handler)
  }, [switchMode])

  useEffect(() => {
    const handler = (event: Event) => {
      const detail = (event as CustomEvent<OpenWidgetEventDetail>).detail
      if (!detail?.widgetId) return
      openWidget(detail.widgetId, detail.title, detail.mode)
    }
    window.addEventListener("alphalab:openWidget", handler)
    return () => window.removeEventListener("alphalab:openWidget", handler)
  }, [openWidget])

  useEffect(() => {
    const handler = (event: Event) => {
      const detail = (event as CustomEvent<OpenTaskEventDetail>).detail
      if (!detail?.task) return
      openTask(detail.task)
    }
    window.addEventListener("alphalab:openTask", handler)
    return () => window.removeEventListener("alphalab:openTask", handler)
  }, [openTask])

  useEffect(() => {
    const handler = (event: Event) => {
      const detail = (event as CustomEvent<AgentWorkspaceCommandEventDetail>).detail
      if (!detail?.batch?.commands) return
      detail.receipts = detail.batch.commands.map((command, index) => {
        try {
          return { index, ...executeWorkspaceCommand(command, detail.researchResult) }
        } catch (error) {
          return {
            index,
            type: command.type,
            success: false,
            message: error instanceof Error ? error.message : String(error),
          }
        }
      })
      const succeeded = detail.receipts.filter((receipt) => receipt.success).length
      const failed = detail.receipts.length - succeeded
      if (succeeded > 0) {
        toast.success(`Agent 已执行 ${succeeded} 项工作台操作`, {
          description: failed > 0
            ? `${failed} 项操作未执行`
            : detail.receipts.map((receipt) => receipt.message).join("；"),
        })
      } else if (failed > 0) {
        toast.error("Agent 工作台操作未执行", {
          description: detail.receipts.map((receipt) => receipt.message).join("；"),
        })
      }
    }
    window.addEventListener(WORKSPACE_COMMAND_EVENT, handler)
    return () => window.removeEventListener(WORKSPACE_COMMAND_EVENT, handler)
  }, [executeWorkspaceCommand])

  // Keyboard shortcuts: Ctrl+1/2/3/4 for mode switching (visible tabs only, so
  // the numbers line up with what the sidebar shows).
  useEffect(() => {
    const modes = WORKSPACE_MODES.filter((mode) => !hiddenModes.has(mode))
    const handler = (e: KeyboardEvent) => {
      if (e.ctrlKey && !e.shiftKey && !e.altKey) {
        const num = parseInt(e.key)
        if (num >= 1 && num <= modes.length) {
          e.preventDefault()
          switchMode(modes[num - 1])
        }
      }
    }
    window.addEventListener("keydown", handler)
    return () => window.removeEventListener("keydown", handler)
  }, [switchMode, hiddenModes])

  // Trading-mode hotkeys (B/S/W//, Shift+X) and app-level alert toasts.
  useTradingHotkeys()
  useAlertNotifications()

  const effectiveSidebarCollapsed = compactViewport || sidebarCollapsed

  return (
    <div className="flex h-full w-full min-w-0 flex-col">
      <div className="flex min-h-0 min-w-0 flex-1">
        <ModeSidebar
          collapsed={effectiveSidebarCollapsed}
          lockedCollapsed={compactViewport}
          onToggleCollapsed={toggleSidebarCollapsed}
          onSwitchMode={switchMode}
          onAddWidget={addWidget}
          hiddenModes={hiddenModes}
          onToggleModeHidden={toggleModeHidden}
        />
        <div className="flex min-h-0 min-w-0 flex-1 flex-col">
          <WorkspaceToolbar
            onSwitchMode={switchMode}
            onAddWidget={addWidget}
            onOpenTask={openTask}
            onSaveLayout={saveCurrentLayout}
            onResetLayout={resetCurrentLayout}
            rightRailCollapsed={rightRailCollapsed}
            onToggleRightRail={toggleRightRailCollapsed}
          />
          {/* Reserved for explicit, user-started data-operation notices. */}
          <LaunchSyncBanner />
          {/* Trading modes: amber top border as safety indicator */}
          <div className={cn("relative min-w-0 flex-1", activeMode.startsWith("trading_") && "border-t-2 border-amber-500/60")}>
            {WORKSPACE_MODES.filter(mode => mountedModes.has(mode)).map(mode => (
              <div
                key={mode}
                aria-hidden={activeMode !== mode}
                className={cn(
                  "absolute inset-0 min-w-0",
                  activeMode === mode
                    ? "z-10 opacity-100"
                    : "pointer-events-none z-0 opacity-0"
                )}
              >
                <DockviewReact
                  className="dockview-theme-abyss"
                  onReady={(event) => onReady(mode, event)}
                  components={components}
                />
              </div>
            ))}
          </div>
        </div>
        <WorkspaceRightRail
          collapsed={rightRailCollapsed}
          narrow={narrowViewport}
          activeTab={rightRailTab}
          onSelectTab={selectRightRailTab}
          onToggleCollapsed={toggleRightRailCollapsed}
          onAddWidget={addWidget}
          onOpenWidget={openWidget}
          onOpenTask={openTask}
        />
      </div>
      {/* Status bar: always visible at bottom */}
      <StatusBar />
    </div>
  )
}

export default function Workspace() {
  return (
    <WorkspaceProvider>
      <WorkspaceInner />
    </WorkspaceProvider>
  )
}
