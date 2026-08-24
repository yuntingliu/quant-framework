/**
 * Workspace - dockview layout with five workflow workstations.
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
import { toast } from "sonner"

import { isActiveWidgetId, widgetComponents, widgetTitleById } from "@/widgets/registry"
import { StatusBar } from "@/widgets/StatusBar"
import { LaunchSyncBanner } from "@/widgets/home/LaunchSyncBanner"
import { layoutPresets, LAYOUT_VERSION, normalizeWorkspaceMode, type WorkspaceMode } from "@/layouts/presets"
import { WorkspaceProvider, useWorkspace, type LinkGroup } from "@/contexts/WorkspaceContext"
import { FactorLabProvider } from "@/contexts/FactorLabContext"
import { PanelContext } from "@/contexts/PanelContext"
import { useAgentPrompt } from "@/contexts/AgentPromptContext"
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
import type { WorkspaceTask } from "@/workspace/types"

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

type WorkspacePanel = ReturnType<DockviewApi["addPanel"]>

interface PendingWidgetOpen {
  widgetId: string
  title?: string
  mode?: WorkspaceMode
  panelId?: string
  params?: Record<string, unknown>
  resolve: (panel: WorkspacePanel | undefined) => void
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
const SIDEBAR_COLLAPSED_KEY = "alphalab-sidebar-collapsed"
const RIGHT_RAIL_COLLAPSED_KEY = "alphalab-right-sidebar-collapsed"
const RIGHT_RAIL_WIDTH_KEY = "alphalab-right-sidebar-width"
const DEFAULT_RIGHT_RAIL_WIDTH = 420
const MIN_RIGHT_RAIL_WIDTH = 320
const MAX_RIGHT_RAIL_WIDTH = 720
interface OpenTaskEventDetail {
  task: WorkspaceTask
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

function loadRightRailWidth(): number {
  try {
    const saved = Number(localStorage.getItem(RIGHT_RAIL_WIDTH_KEY))
    if (Number.isFinite(saved) && saved > 0) {
      return Math.min(MAX_RIGHT_RAIL_WIDTH, Math.max(MIN_RIGHT_RAIL_WIDTH, Math.round(saved)))
    }
  } catch {
    // ignore
  }
  return DEFAULT_RIGHT_RAIL_WIDTH
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

const LEGACY_AGENT_WIDGET_IDS = new Set(["research.agent", "research.ai-workflow"])

function removeLegacyAgentPanels(api: DockviewApi): boolean {
  const panels = api.panels.filter((panel) => {
    const params = panel.params as { componentId?: string } | undefined
    return LEGACY_AGENT_WIDGET_IDS.has(params?.componentId ?? panel.id)
  })
  for (const panel of panels) panel.api.close()
  return panels.length > 0
}

// ---------------------------------------------------------------------------
// Workspace
// ---------------------------------------------------------------------------

function WorkspaceInner() {
  const layoutVersion = LAYOUT_VERSION
  const queryClient = useQueryClient()
  const { registerResearchResult } = useAgentPrompt()
  const apiRef = useRef<DockviewApi | null>(null)
  const apiRefsRef = useRef<Partial<Record<WorkspaceMode, DockviewApi>>>({})
  const pendingWidgetOpenRef = useRef<PendingWidgetOpen[]>([])
  const workspace = useWorkspace()
  const { activeMode, setActiveMode } = workspace
  const { language } = useLanguage()
  const activeModeRef = useRef(activeMode)
  const [mountedModes, setMountedModes] = useState<Set<WorkspaceMode>>(() => new Set([activeMode]))
  const [sidebarCollapsed, setSidebarCollapsed] = useState(loadSidebarCollapsed)
  const [rightRailCollapsed, setRightRailCollapsed] = useState(loadRightRailCollapsed)
  const [rightRailWidth, setRightRailWidth] = useState(loadRightRailWidth)
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

  const openAgentRail = useCallback(() => {
    setRightRailCollapsed(false)
    try {
      localStorage.setItem(RIGHT_RAIL_COLLAPSED_KEY, "false")
    } catch { /* ignore */ }
  }, [])

  const addWidgetToApi = useCallback((api: DockviewApi, rawWidgetId: string, title?: string, options?: WidgetPanelOptions) => {
    if (LEGACY_AGENT_WIDGET_IDS.has(rawWidgetId)) return
    const widgetId = rawWidgetId
    const panelId = options?.panelId ?? widgetId
    const existing = options?.panelId
      ? api.panels.find((panel) => panel.id === panelId)
      : findWidgetPanel(api, widgetId)
    if (existing) {
      existing.api.setActive()
      existing.focus()
      return existing
    }
    const panel = api.addPanel({
      id: panelId,
      component: "widget",
      params: { componentId: widgetId, ...(options?.params ?? {}) },
      title: widgetTitleById(widgetId, language, title),
    })
    panel.api.setActive()
    panel.focus()
    return panel
  }, [language])

  const addWidget = useCallback((widgetId: string, title?: string) => {
    if (LEGACY_AGENT_WIDGET_IDS.has(widgetId)) {
      openAgentRail()
      return
    }
    if (!apiRef.current) return
    addWidgetToApi(apiRef.current, widgetId, title)
  }, [addWidgetToApi, openAgentRail])

  const openWidget = useCallback((widgetId: string, title?: string, rawMode?: unknown, options?: WidgetPanelOptions): Promise<WorkspacePanel | undefined> => {
    if (LEGACY_AGENT_WIDGET_IDS.has(widgetId)) {
      openAgentRail()
      return Promise.resolve(undefined)
    }
    const targetMode = rawMode ? normalizeWorkspaceMode(rawMode) : activeModeRef.current
    const targetApi = apiRefsRef.current[targetMode] ?? null

    return new Promise((resolve) => {
      if (targetMode !== activeModeRef.current) {
        if (targetApi) {
          switchMode(targetMode)
          requestAnimationFrame(() => {
            apiRef.current = targetApi
            resolve(addWidgetToApi(targetApi, widgetId, title, options))
          })
        } else {
          pendingWidgetOpenRef.current.push({ widgetId, title, mode: targetMode, ...options, resolve })
          switchMode(targetMode)
        }
        return
      }

      if (targetApi) {
        apiRef.current = targetApi
        resolve(addWidgetToApi(targetApi, widgetId, title, options))
        return
      }

      pendingWidgetOpenRef.current.push({ widgetId, title, mode: targetMode, ...options, resolve })
    })
  }, [addWidgetToApi, openAgentRail, switchMode])

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

  const resizeRightRail = useCallback((width: number) => {
    const next = Math.min(MAX_RIGHT_RAIL_WIDTH, Math.max(MIN_RIGHT_RAIL_WIDTH, Math.round(width)))
    setRightRailWidth(next)
    try { localStorage.setItem(RIGHT_RAIL_WIDTH_KEY, String(next)) } catch { /* ignore */ }
  }, [])

  const openTask = useCallback((task: WorkspaceTask) => {
    if (task === "resetWorkstations") {
      for (const mode of WORKSPACE_MODES) {
        localStorage.removeItem(layoutKey(mode))
        const modeApi = apiRefsRef.current[mode]
        if (modeApi) {
          layoutPresets[mode].apply(modeApi)
          relabelPanels(modeApi, language)
          saveLayout(modeApi, mode)
        }
      }
      switchMode("data")
      return
    }

    if (task === "startResearch") {
      openAgentRail()
      return
    }

    if (task === "runBacktest") {
      openWidget("backtest.workbench", undefined, "backtest")
      return
    }

    openWidget("backtest.workbench", undefined, "backtest")
  }, [language, openAgentRail, openWidget, switchMode])

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

  const executeWorkspaceCommand = useCallback(async (
    command: AgentWorkspaceCommand,
    researchResult?: AgentResearchResult,
  ): Promise<Omit<AgentWorkspaceCommandReceipt, "index">> => {
    switch (command.type) {
      case "switch_mode":
        switchMode(command.mode)
        return { type: command.type, success: true, message: `已切换到 ${command.mode} 板块` }
      case "open_widget": {
        if (!widgetComponents[command.widgetId] || !isActiveWidgetId(command.widgetId)) {
          return { type: command.type, success: false, message: `不可用的看板组件：${command.widgetId}` }
        }
        const openedPanel = await openWidget(command.widgetId, command.title, command.mode)
        const targetMode = command.mode ?? activeModeRef.current
        await new Promise<void>((resolve) => requestAnimationFrame(() => resolve()))
        const targetApi = apiRefsRef.current[targetMode]
        if (targetApi && openedPanel && activeModeRef.current === targetMode && targetApi.activePanel?.id === openedPanel.id) {
          return { type: command.type, success: true, message: `已打开 ${widgetTitleById(command.widgetId, language)}` }
        }
        return { type: command.type, success: false, message: `组件未能挂载：${widgetTitleById(command.widgetId, language)}` }
      }
      case "open_result": {
        if (!researchResult || researchResult.id !== command.resultId) {
          return { type: command.type, success: false, message: `未找到本轮研究结果：${command.resultId}` }
        }
        registerResearchResult(researchResult)
        const panelId = `research-result-${command.resultId.replace(/[^a-zA-Z0-9_-]/g, "-")}`
        const openedPanel = await openWidget(
          "report.workbench",
          command.title ?? researchResult.title,
          command.mode ?? "report",
          { panelId, params: { resultId: researchResult.id } },
        )
        const targetMode = command.mode ?? "report"
        await new Promise<void>((resolve) => requestAnimationFrame(() => resolve()))
        const targetApi = apiRefsRef.current[targetMode]
        if (targetApi && openedPanel?.id === panelId && activeModeRef.current === targetMode && targetApi.activePanel?.id === panelId) {
          return { type: command.type, success: true, message: `已在 ${targetMode} 工作区激活研究结果：${researchResult.title}` }
        }
        return { type: command.type, success: false, message: `研究结果面板未能挂载：${researchResult.title}` }
      }
      case "close_widget": {
        const targetMode = command.mode ?? activeModeRef.current
        const api = apiRefsRef.current[targetMode]
        const panel = api ? findWidgetPanel(api, command.widgetId) : undefined
        if (!panel) return { type: command.type, success: false, message: `未找到已打开的组件：${command.widgetId}` }
        panel.api.close()
        return { type: command.type, success: true, message: `已关闭 ${widgetTitleById(command.widgetId, language)}` }
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
        return { type: command.type, success: true, message: `联动组 ${command.group.toUpperCase()} 已设为 ${command.symbol ?? "空"}` }
      case "show_right_rail": {
        const open = command.open ?? true
        setRightRailCollapsed(!open)
        try { localStorage.setItem(RIGHT_RAIL_COLLAPSED_KEY, String(!open)) } catch { /* ignore */ }
        return { type: command.type, success: true, message: open ? "已展开右侧栏" : "已收起右侧栏" }
      }
      case "refresh_data":
        void queryClient.invalidateQueries()
        window.dispatchEvent(new CustomEvent("alphalab:refreshData"))
        return { type: command.type, success: true, message: "已刷新工作台数据" }
      case "save_layout": {
        const targetMode = command.mode ?? activeModeRef.current
        const api = apiRefsRef.current[targetMode]
        if (!api) return { type: command.type, success: false, message: `${targetMode} 布局尚未挂载` }
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
  }, [language, openWidget, queryClient, registerResearchResult, switchMode, workspace])

  useEffect(() => {
    const api = apiRefsRef.current[activeMode]
    apiRef.current = api ?? null
    if (!api) return
    requestAnimationFrame(() => {
      api.layout(api.width, api.height, true)
      api.focus()
    })
  }, [activeMode, compactViewport, narrowViewport, rightRailWidth, sidebarCollapsed, rightRailCollapsed])

  const onReady = useCallback((mode: WorkspaceMode, event: DockviewReadyEvent) => {
    apiRefsRef.current[mode] = event.api
    if (mode === activeModeRef.current) apiRef.current = event.api
    applyMode(event.api, mode)
    relabelPanels(event.api, language)
    if (removeLegacyAgentPanels(event.api)) {
      saveLayout(event.api, mode)
      openAgentRail()
    }
    const pending = pendingWidgetOpenRef.current.filter((request) => !request.mode || request.mode === mode)
    pendingWidgetOpenRef.current = pendingWidgetOpenRef.current.filter((request) => request.mode && request.mode !== mode)
    for (const request of pending) {
      requestAnimationFrame(() => {
        if (mode === activeModeRef.current) apiRef.current = event.api
        request.resolve(addWidgetToApi(event.api, request.widgetId, request.title, { panelId: request.panelId, params: request.params }))
      })
    }
    event.api.onDidLayoutChange(() => {
      saveLayout(event.api, mode)
    })
  }, [addWidgetToApi, language, openAgentRail])

  // Keep the live Dockview tree in sync during Vite Fast Refresh as well as
  // after a full reload. A layout-version bump intentionally replaces saved
  // presets because the registered component IDs have changed.
  useEffect(() => {
    const savedVersion = Number(localStorage.getItem(VERSION_KEY) || "0")
    if (savedVersion >= layoutVersion) return
    for (const key of Object.keys(localStorage)) {
      if (key.startsWith("alphalab-layout-")) localStorage.removeItem(key)
    }
    localStorage.setItem(VERSION_KEY, String(layoutVersion))
    for (const [rawMode, api] of Object.entries(apiRefsRef.current)) {
      const mode = rawMode as WorkspaceMode
      const preset = layoutPresets[mode]
      if (!api || !preset) continue
      preset.apply(api)
      relabelPanels(api, language)
      saveLayout(api, mode)
    }
  }, [language, layoutVersion])

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
      detail.receiptPromise = (async () => {
        const receipts: AgentWorkspaceCommandReceipt[] = []
        for (let index = 0; index < detail.batch.commands.length; index += 1) {
          const command = detail.batch.commands[index]
          if (!command) continue
          try {
            receipts.push({ index, ...await executeWorkspaceCommand(command, detail.researchResult) })
          } catch (error) {
            receipts.push({
              index,
              type: command.type,
              success: false,
              message: error instanceof Error ? error.message : String(error),
            })
          }
        }
        detail.receipts = receipts
        const succeeded = receipts.filter((receipt) => receipt.success).length
        const failed = receipts.length - succeeded
        if (succeeded > 0) {
          toast.success(`Agent 已执行 ${succeeded} 项工作台操作`, {
            description: failed > 0 ? `${failed} 项操作未执行` : receipts.map((receipt) => receipt.message).join("；"),
          })
        } else if (failed > 0) {
          toast.error("Agent 工作台操作未执行", { description: receipts.map((receipt) => receipt.message).join("；") })
        }
        return receipts
      })()
    }
    window.addEventListener(WORKSPACE_COMMAND_EVENT, handler)
    return () => window.removeEventListener(WORKSPACE_COMMAND_EVENT, handler)
  }, [executeWorkspaceCommand])

  // Keyboard shortcuts follow the fixed sidebar order.
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.ctrlKey && !e.shiftKey && !e.altKey) {
        const num = parseInt(e.key)
        if (num >= 1 && num <= WORKSPACE_MODES.length) {
          e.preventDefault()
          switchMode(WORKSPACE_MODES[num - 1])
        }
      }
    }
    window.addEventListener("keydown", handler)
    return () => window.removeEventListener("keydown", handler)
  }, [switchMode])

  // App-level alert toasts remain available for background system notices.
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
        />
        <div className="flex min-h-0 min-w-0 flex-1 flex-col">
          <WorkspaceToolbar
            onAddWidget={addWidget}
            onSaveLayout={saveCurrentLayout}
            onResetLayout={resetCurrentLayout}
          />
          {/* Reserved for explicit, user-started data-operation notices. */}
          <LaunchSyncBanner />
          <div className="relative min-w-0 flex-1">
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
                  className="dockview-theme-light alphalab-dockview"
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
          width={rightRailWidth}
          onToggleCollapsed={toggleRightRailCollapsed}
          onWidthChange={resizeRightRail}
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
      <FactorLabProvider>
        <WorkspaceInner />
      </FactorLabProvider>
    </WorkspaceProvider>
  )
}
