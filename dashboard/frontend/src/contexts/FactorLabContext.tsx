import { createContext, useCallback, useContext, useMemo, useState } from "react"
import { useQuery, useQueryClient } from "@tanstack/react-query"

import { useWorkspace } from "@/contexts/WorkspaceContext"
import {
  api,
  type FactorResearchLibrary,
  type FactorResearchResult,
  type PipelineProjectDetail,
} from "@/lib/api"
import { useDataProfile } from "@/lib/data-profile"

export type FactorSource = "technical" | "fundamental" | "expression"

export interface ProjectFactorSpec {
  name: string
  source: FactorSource
  expression?: string | null
  direction: "long" | "short"
  weight: number
  winsorize: number
  neutralize: string[]
}

export interface FactorDraft extends ProjectFactorSpec {
  expression: string
  startDate: string
  endDate: string
  frequency: "monthly" | "weekly"
  quantiles: number
}

interface ExpressionInsertRequest {
  id: number
  token: string
}

interface FactorLabContextValue {
  library: FactorResearchLibrary | null
  libraryLoading: boolean
  project: PipelineProjectDetail | null
  projectLoading: boolean
  projectFactors: ProjectFactorSpec[]
  draft: FactorDraft
  editingOriginalName: string | null
  result: FactorResearchResult | null
  resultStale: boolean
  expressionInsertRequest: ExpressionInsertRequest | null
  running: boolean
  saving: boolean
  error: string
  workspaceView: "build" | "results"
  setWorkspaceView: (view: "build" | "results") => void
  selectLibraryFactor: (factor: FactorResearchLibrary["factors"][number]) => void
  addLibraryFactorToProject: (factor: FactorResearchLibrary["factors"][number]) => Promise<void>
  selectProjectFactor: (factor: ProjectFactorSpec) => void
  createExpressionFactor: () => void
  requestExpressionInsert: (token: string) => void
  updateDraft: (values: Partial<FactorDraft>) => void
  evaluate: () => Promise<void>
  saveCustomFactor: () => Promise<void>
  saveToProject: () => Promise<void>
  removeFromProject: (name: string) => Promise<void>
}

const FactorLabContext = createContext<FactorLabContextValue | null>(null)

function isoDate(value: Date): string {
  return value.toISOString().slice(0, 10)
}

function defaultDates() {
  const end = new Date()
  const start = new Date(end)
  start.setFullYear(start.getFullYear() - 3)
  return { startDate: isoDate(start), endDate: isoDate(end) }
}

function defaultDraft(): FactorDraft {
  return {
    name: "momentum_20d",
    source: "technical",
    expression: "",
    direction: "long",
    weight: 1,
    winsorize: 0.01,
    neutralize: [],
    frequency: "monthly",
    quantiles: 5,
    ...defaultDates(),
  }
}

function nextExpressionName(projectFactors: ProjectFactorSpec[]): string {
  const used = new Set(projectFactors.map((factor) => factor.name))
  let index = 1
  let name = "custom_factor"
  while (used.has(name)) {
    index += 1
    name = `custom_factor_${index}`
  }
  return name
}

function defaultDirection(name: string): FactorDraft["direction"] {
  return name.includes("volatility") || name.includes("leverage") ? "short" : "long"
}

function asProjectFactors(value: unknown): ProjectFactorSpec[] {
  if (!Array.isArray(value)) return []
  return value.flatMap((raw) => {
    if (!raw || typeof raw !== "object" || Array.isArray(raw)) return []
    const item = raw as Record<string, unknown>
    const source = String(item.source || "technical")
    const direction = String(item.direction || "long")
    const name = String(item.name || "").trim()
    if (!name || !["technical", "fundamental", "expression"].includes(source)) return []
    return [{
      name,
      source: source as FactorSource,
      expression: item.expression == null ? null : String(item.expression),
      direction: direction === "short" ? "short" as const : "long" as const,
      weight: Number.isFinite(Number(item.weight)) ? Number(item.weight) : 1,
      winsorize: Number.isFinite(Number(item.winsorize)) ? Number(item.winsorize) : 0.01,
      neutralize: Array.isArray(item.neutralize) ? item.neutralize.map(String) : [],
    }]
  })
}

function factorSignature(draft: FactorDraft, profile: string, projectId: string | null): string {
  return JSON.stringify({
    projectId,
    profile,
    name: draft.name,
    source: draft.source,
    expression: draft.expression,
    direction: draft.direction,
    winsorize: draft.winsorize,
    neutralize: draft.neutralize,
    startDate: draft.startDate,
    endDate: draft.endDate,
    frequency: draft.frequency,
    quantiles: draft.quantiles,
  })
}

export function FactorLabProvider({ children }: { children: React.ReactNode }) {
  const queryClient = useQueryClient()
  const [profile] = useDataProfile()
  const {
    activeMode,
    selectedStrategy,
    selectedStrategyRevision,
    setSelectedStrategyRevision,
  } = useWorkspace()
  const [draft, setDraft] = useState<FactorDraft>(defaultDraft)
  const [expressionInsertRequest, setExpressionInsertRequest] = useState<ExpressionInsertRequest | null>(null)
  const [editingOriginalName, setEditingOriginalName] = useState<string | null>(null)
  const [result, setResult] = useState<FactorResearchResult | null>(null)
  const [evaluatedSignature, setEvaluatedSignature] = useState("")
  const [running, setRunning] = useState(false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState("")
  const [workspaceView, setWorkspaceView] = useState<"build" | "results">("build")

  const libraryQuery = useQuery({
    queryKey: ["factor-research", "library", profile],
    queryFn: () => api.get<FactorResearchLibrary>(`/factor-research/library?profile=${profile}`),
    enabled: activeMode === "factor",
    staleTime: 30_000,
  })
  const projectQuery = useQuery({
    queryKey: ["pipeline", "project-detail", selectedStrategy, selectedStrategyRevision],
    queryFn: () => api.get<PipelineProjectDetail>(`/pipeline/projects/${selectedStrategy}`),
    enabled: activeMode === "factor" && Boolean(selectedStrategy),
    staleTime: 30_000,
  })
  const project = projectQuery.data ?? null
  const projectFactors = useMemo(
    () => asProjectFactors(project?.settings.factors),
    [project?.settings.factors],
  )
  const projectUniverse = useMemo(
    () => project?.settings.universe && typeof project.settings.universe === "object"
      ? project.settings.universe as Record<string, unknown>
      : {},
    [project?.settings.universe],
  )
  const signature = factorSignature(draft, profile, selectedStrategy)

  const selectLibraryFactor = useCallback((factor: FactorResearchLibrary["factors"][number]) => {
    setWorkspaceView("build")
    setEditingOriginalName(null)
    setError("")
    setDraft((current) => ({
      ...current,
      name: factor.name,
      source: factor.source,
      expression: factor.expression ?? "",
      direction: factor.direction ?? defaultDirection(factor.name),
      weight: 1,
      winsorize: factor.winsorize ?? 0.01,
      neutralize: factor.neutralize ?? [],
    }))
  }, [])

  const selectProjectFactor = useCallback((factor: ProjectFactorSpec) => {
    setWorkspaceView("build")
    setEditingOriginalName(factor.name)
    setError("")
    setDraft((current) => ({
      ...current,
      ...factor,
      expression: factor.expression ?? "",
    }))
  }, [])

  const createExpressionFactor = useCallback(() => {
    setWorkspaceView("build")
    const name = nextExpressionName(projectFactors)
    setEditingOriginalName(null)
    setError("")
    setDraft((current) => ({
      ...current,
      name,
      source: "expression",
      expression: "zscore(momentum_20d) - 0.5 * zscore(volatility_20d)",
      direction: "long",
      weight: 1,
      winsorize: 0.01,
      neutralize: [],
    }))
  }, [projectFactors])

  const requestExpressionInsert = useCallback((token: string) => {
    setWorkspaceView("build")
    setEditingOriginalName(null)
    setError("")
    setDraft((current) => current.source === "expression"
      ? current
      : {
          ...current,
          name: nextExpressionName(projectFactors),
          source: "expression",
          expression: "",
          direction: "long",
          weight: 1,
          winsorize: 0.01,
          neutralize: [],
        })
    setExpressionInsertRequest((current) => ({ id: (current?.id ?? 0) + 1, token }))
  }, [projectFactors])

  const updateDraft = useCallback((values: Partial<FactorDraft>) => {
    setDraft((current) => ({ ...current, ...values }))
    setError("")
  }, [])

  const evaluate = useCallback(async () => {
    if (!draft.name.trim()) {
      setError("请输入因子名称")
      return
    }
    if (draft.source === "expression" && !draft.expression.trim()) {
      setError("请输入因子表达式")
      return
    }
    setRunning(true)
    setError("")
    try {
      const next = await api.post<FactorResearchResult>("/factor-research/evaluate", {
        profile,
        name: draft.name.trim(),
        source: draft.source,
        expression: draft.source === "expression" ? draft.expression.trim() : null,
        direction: draft.direction,
        winsorize: draft.winsorize,
        neutralize: draft.neutralize,
        start_date: draft.startDate,
        end_date: draft.endDate,
        frequency: draft.frequency,
        quantiles: draft.quantiles,
        symbols: Array.isArray(projectUniverse.symbols) && projectUniverse.symbols.length ? projectUniverse.symbols : null,
        min_price: Number(projectUniverse.min_price || 0),
        min_history_days: Number(projectUniverse.min_history_days || 60),
        min_average_amount: Number(projectUniverse.min_average_amount || 0),
      })
      setResult(next)
      setEvaluatedSignature(signature)
      setWorkspaceView("results")
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason))
    } finally {
      setRunning(false)
    }
  }, [draft, profile, projectUniverse, signature])

  const persistFactors = useCallback(async (factors: ProjectFactorSpec[]) => {
    if (!project) throw new Error("请先选择研究项目")
    if (!project.editable) throw new Error("当前项目只读，请先在“研究项目”工作区复制项目")
    const saved = await api.put<PipelineProjectDetail>(`/pipeline/projects/${project.id}`, {
      name: project.name,
      description: project.description,
      components: project.components,
      settings: { ...project.settings, factors },
    })
    queryClient.setQueryData(
      ["pipeline", "project-detail", saved.id, saved.revision],
      saved,
    )
    setSelectedStrategyRevision(saved.revision)
    await queryClient.invalidateQueries({ queryKey: ["pipeline", "projects"] })
    window.dispatchEvent(new CustomEvent("alphalab:projectUpdated", { detail: saved }))
  }, [project, queryClient, setSelectedStrategyRevision])

  const saveToProject = useCallback(async () => {
    setSaving(true)
    setError("")
    try {
      const name = draft.name.trim()
      if (!name) throw new Error("请输入因子名称")
      if (draft.source === "expression" && !draft.expression.trim()) {
        throw new Error("请输入因子表达式")
      }
      const next: ProjectFactorSpec = {
        name,
        source: draft.source,
        ...(draft.source === "expression" ? { expression: draft.expression.trim() } : {}),
        direction: draft.direction,
        // Effective combination weights belong to the project's signal model.
        // Keep this compatibility field neutral for the guarded core.
        weight: 1,
        winsorize: draft.winsorize,
        neutralize: draft.neutralize,
      }
      const matchName = editingOriginalName ?? name
      const remaining = projectFactors.filter((factor) => factor.name !== matchName && factor.name !== name)
      await persistFactors([...remaining, next])
      setEditingOriginalName(name)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason))
    } finally {
      setSaving(false)
    }
  }, [draft, editingOriginalName, persistFactors, projectFactors])

  const saveCustomFactor = useCallback(async () => {
    setSaving(true)
    setError("")
    try {
      const name = draft.name.trim()
      if (!name) throw new Error("请输入因子名称")
      if (draft.source !== "expression" || !draft.expression.trim()) {
        throw new Error("只有完整的表达式因子可以保存到自定义因子库")
      }
      const saved = await api.put<FactorResearchLibrary["factors"][number]>(
        `/factor-research/factors/${encodeURIComponent(name)}`,
        {
          description: "自定义表达式因子",
          expression: draft.expression.trim(),
          direction: draft.direction,
          winsorize: draft.winsorize,
          neutralize: draft.neutralize,
        },
      )
      queryClient.setQueryData<FactorResearchLibrary>(
        ["factor-research", "library", profile],
        (current) => current ? {
          ...current,
          factors: [...current.factors.filter((factor) => factor.name !== saved.name), saved],
        } : current,
      )
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason))
    } finally {
      setSaving(false)
    }
  }, [draft, profile, queryClient])

  const addLibraryFactorToProject = useCallback(async (factor: FactorResearchLibrary["factors"][number]) => {
    if (projectFactors.some((item) => item.name === factor.name)) return
    setSaving(true)
    setError("")
    try {
      await persistFactors([...projectFactors, {
        name: factor.name,
        source: factor.source,
        ...(factor.source === "expression" ? { expression: factor.expression ?? "" } : {}),
        direction: factor.direction ?? defaultDirection(factor.name),
        weight: 1,
        winsorize: factor.winsorize ?? 0.01,
        neutralize: factor.neutralize ?? [],
      }])
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason))
    } finally {
      setSaving(false)
    }
  }, [persistFactors, projectFactors])

  const removeFromProject = useCallback(async (name: string) => {
    setSaving(true)
    setError("")
    try {
      await persistFactors(projectFactors.filter((factor) => factor.name !== name))
      if (editingOriginalName === name) setEditingOriginalName(null)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason))
    } finally {
      setSaving(false)
    }
  }, [editingOriginalName, persistFactors, projectFactors])

  const value = useMemo<FactorLabContextValue>(() => ({
    library: libraryQuery.data ?? null,
    libraryLoading: libraryQuery.isLoading,
    project,
    projectLoading: projectQuery.isLoading,
    projectFactors,
    draft,
    editingOriginalName,
    result,
    resultStale: Boolean(result && evaluatedSignature !== signature),
    expressionInsertRequest,
    running,
    saving,
    error: error || (libraryQuery.error instanceof Error ? libraryQuery.error.message : "") || (projectQuery.error instanceof Error ? projectQuery.error.message : ""),
    workspaceView,
    setWorkspaceView,
    selectLibraryFactor,
    addLibraryFactorToProject,
    selectProjectFactor,
    createExpressionFactor,
    requestExpressionInsert,
    updateDraft,
    evaluate,
    saveCustomFactor,
    saveToProject,
    removeFromProject,
  }), [
    createExpressionFactor,
    addLibraryFactorToProject,
    draft,
    editingOriginalName,
    error,
    evaluate,
    evaluatedSignature,
    libraryQuery.data,
    libraryQuery.error,
    libraryQuery.isLoading,
    project,
    projectFactors,
    projectQuery.error,
    projectQuery.isLoading,
    removeFromProject,
    requestExpressionInsert,
    result,
    running,
    saveCustomFactor,
    saveToProject,
    saving,
    selectLibraryFactor,
    selectProjectFactor,
    signature,
    expressionInsertRequest,
    updateDraft,
    workspaceView,
  ])

  return <FactorLabContext.Provider value={value}>{children}</FactorLabContext.Provider>
}

export function useFactorLab(): FactorLabContextValue {
  const value = useContext(FactorLabContext)
  if (!value) throw new Error("useFactorLab must be used inside FactorLabProvider")
  return value
}
