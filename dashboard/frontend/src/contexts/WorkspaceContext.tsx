/**
 * WorkspaceContext — shared state for cross-widget communication + active mode.
 */
import { createContext, useContext, useState, useCallback } from "react"
import { DEFAULT_MODE, normalizeWorkspaceMode, type WorkspaceMode } from "@/layouts/presets"
import type { PythonPipelineStage } from "@/lib/api"

const MODE_KEY = "alphalab-active-mode"
const PROJECT_KEY = "alphalab-selected-project"
// Startup always lands on the Data workbench. Users can switch workstations
// during the session or request another one explicitly through the URL.
const DEFAULT_MODE_VERSION_KEY = "alphalab-default-mode-version"
const DEFAULT_MODE_VERSION = "8"

/** Symbol link channels. Panels assigned to the same group follow
 * the same symbol; `null` group falls back to the global selectedSymbol. */
export type LinkGroup = "a" | "b" | "c" | "d"

export const LINK_GROUPS: LinkGroup[] = ["a", "b", "c", "d"]

export const LINK_GROUP_COLORS: Record<LinkGroup, string> = {
  a: "#fbbf24", // amber
  b: "#38bdf8", // blue
  c: "#34d399", // green
  d: "#f87171", // red
}

export interface StageRunContext {
  projectId: string
  revision: number
  profile: "demo" | "runtime"
  asOfDate: string | null
}

interface WorkspaceContextValue {
  activeMode: WorkspaceMode
  setActiveMode: (mode: WorkspaceMode) => void
  selectedStrategy: string | null
  setSelectedStrategy: (id: string | null) => void
  selectedStrategyRevision: number | null
  setSelectedStrategyRevision: (revision: number | null) => void
  stageRunContexts: Partial<Record<PythonPipelineStage, StageRunContext>>
  setStageRunContext: (stage: PythonPipelineStage, value: StageRunContext) => void
  selectedDataset: string | null
  setSelectedDataset: (id: string | null) => void
  selectedSymbol: string | null
  setSelectedSymbol: (code: string | null) => void
  linkSymbols: Partial<Record<LinkGroup, string | null>>
  setLinkSymbol: (group: LinkGroup | null, symbol: string | null) => void
  selectedOrderPrice: number | null
  setSelectedOrderPrice: (price: number | null) => void
  selectedOrderSide: "buy" | "sell" | null
  setSelectedOrderSide: (side: "buy" | "sell" | null) => void
  selectedBacktest: string | null
  setSelectedBacktest: (id: string | null) => void
  selectedDate: string | null
  setSelectedDate: (date: string | null) => void
}

const WorkspaceContext = createContext<WorkspaceContextValue | null>(null)

function loadMode(): WorkspaceMode {
  const requestedMode = typeof window !== "undefined"
    ? new URLSearchParams(window.location.search).get("mode")
    : null
  if (!requestedMode) {
    try {
      localStorage.setItem(DEFAULT_MODE_VERSION_KEY, DEFAULT_MODE_VERSION)
      localStorage.setItem(MODE_KEY, DEFAULT_MODE)
    } catch { /* ignore */ }
    return DEFAULT_MODE
  }
  const mode = requestedMode ? normalizeWorkspaceMode(requestedMode) : DEFAULT_MODE
  try {
    localStorage.setItem(DEFAULT_MODE_VERSION_KEY, DEFAULT_MODE_VERSION)
    localStorage.setItem(MODE_KEY, mode)
  } catch { /* ignore */ }
  return mode
}

function loadProject(): string | null {
  try { return localStorage.getItem(PROJECT_KEY) }
  catch { return null }
}

export function WorkspaceProvider({ children }: { children: React.ReactNode }) {
  const [activeMode, _setActiveMode] = useState<WorkspaceMode>(loadMode)
  const [selectedStrategy, setSelectedStrategy] = useState<string | null>(loadProject)
  const [selectedStrategyRevision, setSelectedStrategyRevision] = useState<number | null>(null)
  const [stageRunContexts, setStageRunContexts] = useState<Partial<Record<PythonPipelineStage, StageRunContext>>>({})
  const [selectedDataset, setSelectedDataset] = useState<string | null>(null)
  const [selectedSymbol, setSelectedSymbol] = useState<string | null>(null)
  const [linkSymbols, setLinkSymbols] = useState<Partial<Record<LinkGroup, string | null>>>({})
  const [selectedOrderPrice, setSelectedOrderPrice] = useState<number | null>(null)
  const [selectedOrderSide, setSelectedOrderSide] = useState<"buy" | "sell" | null>(null)
  const [selectedBacktest, setSelectedBacktest] = useState<string | null>(null)
  const [selectedDate, setSelectedDate] = useState<string | null>(null)

  const setActiveMode = useCallback((mode: WorkspaceMode) => {
    _setActiveMode(mode)
    try { localStorage.setItem(MODE_KEY, mode) } catch { /* ignore */ }
  }, [])

  const selectStrategy = useCallback((value: string | null) => {
    setSelectedStrategy(value)
    try {
      if (value) localStorage.setItem(PROJECT_KEY, value)
      else localStorage.removeItem(PROJECT_KEY)
    } catch { /* ignore */ }
  }, [])

  return (
    <WorkspaceContext.Provider
      value={{
        activeMode,
        setActiveMode,
        selectedStrategy,
        selectedStrategyRevision,
        stageRunContexts,
        selectedDataset,
        selectedSymbol,
        linkSymbols,
        setLinkSymbol: useCallback((group: LinkGroup | null, symbol: string | null) => {
          if (group === null) {
            setSelectedSymbol(symbol)
            return
          }
          setLinkSymbols((prev) => ({ ...prev, [group]: symbol }))
        }, []),
        selectedOrderPrice,
        selectedOrderSide,
        selectedBacktest,
        selectedDate,
        setSelectedStrategy: selectStrategy,
        setSelectedStrategyRevision: useCallback((v) => setSelectedStrategyRevision(v), []),
        setStageRunContext: useCallback((stage, value) => {
          setStageRunContexts((current) => ({ ...current, [stage]: value }))
        }, []),
        setSelectedDataset: useCallback((v) => setSelectedDataset(v), []),
        setSelectedSymbol: useCallback((v) => setSelectedSymbol(v), []),
        setSelectedOrderPrice: useCallback((v) => setSelectedOrderPrice(v), []),
        setSelectedOrderSide: useCallback((v) => setSelectedOrderSide(v), []),
        setSelectedBacktest: useCallback((v) => setSelectedBacktest(v), []),
        setSelectedDate: useCallback((v) => setSelectedDate(v), []),
      }}
    >
      {children}
    </WorkspaceContext.Provider>
  )
}

export function useWorkspace(): WorkspaceContextValue {
  const ctx = useContext(WorkspaceContext)
  if (!ctx) {
    return {
      activeMode: DEFAULT_MODE,
      setActiveMode: () => {},
      selectedStrategy: null,
      selectedStrategyRevision: null,
      stageRunContexts: {},
      selectedDataset: null,
      selectedSymbol: null,
      linkSymbols: {},
      setLinkSymbol: () => {},
      selectedOrderPrice: null,
      selectedOrderSide: null,
      selectedBacktest: null,
      selectedDate: null,
      setSelectedStrategy: () => {},
      setSelectedStrategyRevision: () => {},
      setStageRunContext: () => {},
      setSelectedDataset: () => {},
      setSelectedSymbol: () => {},
      setSelectedOrderPrice: () => {},
      setSelectedOrderSide: () => {},
      setSelectedBacktest: () => {},
      setSelectedDate: () => {},
    }
  }
  return ctx
}
