import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react"

import { api } from "@/lib/api"
import { useWorkspace } from "@/contexts/WorkspaceContext"

export interface SdkParameter {
  name: string
  annotation: string | null
  default: unknown
  editable: boolean
  custom_source: string | null
  label: string | null
  description: string | null
  minimum: number | null
  maximum: number | null
  step: number | null
}

export interface SdkEntrypoint {
  kind: "universe" | "factor" | "schedule" | "signal" | "portfolio" | "event" | "execution"
  id: string
  function: string
  label: string | null
  event: string | null
  metadata: Record<string, unknown>
  parameters: SdkParameter[]
  line: number
}

export interface SourceInspection {
  valid: boolean
  sdk_version: number
  source_sha256: string
  source_bytes: number
  validator_version: string
  entrypoints: SdkEntrypoint[]
  data_requirements: Record<string, unknown>
  runtime_requirements: Record<string, unknown>
  warnings: Array<{ line: number; code: string; message: string }>
}

export interface SourcePackage {
  project_id: string
  revision: number
  parent_revision: number | null
  source_sha256: string
  sdk_version: number
  validator_version: string
  manifest: Array<Record<string, unknown>>
  parameters: Record<string, Record<string, unknown>>
  created_at: string
  source?: string
}

export interface StrategyProject {
  id: string
  name: string
  description: string
  profile: "runtime"
  current_revision: number
  draft_parent_revision: number | null
  draft_source_sha256: string
  draft_source: string
  dirty: boolean
  settings: Record<string, unknown>
  built_in: boolean
  editable: boolean
  current_package: SourcePackage
  inspection: SourceInspection
  created_at: string
  updated_at: string
}

type ProjectSummary = Omit<StrategyProject, "draft_source" | "inspection">

interface StrategySdkValue {
  projects: ProjectSummary[]
  project: StrategyProject | null
  loading: boolean
  error: string
  refresh: (preferredId?: string) => Promise<void>
  openProject: (projectId: string) => Promise<void>
  createProject: (targetId: string, name: string) => Promise<StrategyProject>
  updateDraft: (source: string) => Promise<StrategyProject>
  updateMetadata: (values: Pick<StrategyProject, "name" | "description" | "profile" | "settings">) => Promise<StrategyProject>
  structuredEdit: (payload: Record<string, unknown>) => Promise<StrategyProject>
  installFactorTemplate: (templateId: string) => Promise<StrategyProject>
  removeProject: () => Promise<void>
}

const StrategySdkContext = createContext<StrategySdkValue | null>(null)

export function StrategySdkProvider({ children }: { children: ReactNode }) {
  const {
    selectedStrategy,
    setSelectedStrategy,
    setSelectedStrategyEditable,
    setSelectedStrategyRevision,
  } = useWorkspace()
  const [projects, setProjects] = useState<ProjectSummary[]>([])
  const [project, setProject] = useState<StrategyProject | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState("")

  const adopt = useCallback((value: StrategyProject) => {
    setProject(value)
    const { draft_source: _source, inspection: _inspection, ...summary } = value
    setProjects((current) => current.map((item) => item.id === value.id ? summary : item))
    setSelectedStrategy(value.id)
    setSelectedStrategyEditable(value.editable)
    setSelectedStrategyRevision(value.current_revision)
    window.dispatchEvent(new CustomEvent("alphalab:strategyUpdated", { detail: value }))
    return value
  }, [setSelectedStrategy, setSelectedStrategyEditable, setSelectedStrategyRevision])

  const openProject = useCallback(async (projectId: string) => {
    setLoading(true)
    setError("")
    try {
      adopt(await api.get<StrategyProject>(`/strategy/projects/${projectId}`))
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason))
      throw reason
    } finally {
      setLoading(false)
    }
  }, [adopt])

  const refresh = useCallback(async (preferredId?: string) => {
    setLoading(true)
    setError("")
    try {
      const rows = await api.get<ProjectSummary[]>("/strategy/projects")
      setProjects(rows)
      const next = rows.find((item) => item.id === (preferredId ?? selectedStrategy))
        ?? rows.find((item) => item.id === "sdk-v1-default")
        ?? rows[0]
      if (next) adopt(await api.get<StrategyProject>(`/strategy/projects/${next.id}`))
      else setProject(null)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason))
    } finally {
      setLoading(false)
    }
  }, [adopt, selectedStrategy])

  useEffect(() => { void refresh() }, []) // eslint-disable-line react-hooks/exhaustive-deps

  const commitSavedProject = useCallback(async (value: StrategyProject) => {
    if (!value.dirty) return adopt(value)
    try {
      await api.post<SourcePackage>(`/strategy/projects/${value.id}/revisions`, {
        expected_source_sha256: value.draft_source_sha256,
        confirm_save: true,
        confirm_python_execution: true,
      })
      return adopt(await api.get<StrategyProject>(`/strategy/projects/${value.id}`))
    } catch (reason) {
      adopt(value)
      throw reason
    }
  }, [adopt])

  const value = useMemo<StrategySdkValue>(() => ({
    projects,
    project,
    loading,
    error,
    refresh,
    openProject,
    createProject: async (targetId, name) => {
      const created = await api.post<StrategyProject>("/strategy/projects/sdk-v1-default/clone", {
        target_id: targetId,
        name,
        confirm_save: true,
        confirm_python_execution: true,
      })
      await refresh(created.id)
      return created
    },
    updateDraft: async (source) => {
      if (!project) throw new Error("请先选择项目")
      return commitSavedProject(await api.put<StrategyProject>(`/strategy/projects/${project.id}/draft`, {
        source,
        expected_source_sha256: project.draft_source_sha256,
        confirm_write: true,
      }))
    },
    updateMetadata: async (values) => adopt(await api.put<StrategyProject>(
      `/strategy/projects/${project?.id}/metadata`,
      { ...values, confirm_write: true },
    )),
    structuredEdit: async (payload) => {
      if (!project) throw new Error("请先选择项目")
      const response = await api.post<{ project: StrategyProject }>(
        `/strategy/projects/${project.id}/edits`,
        {
          ...payload,
          expected_source_sha256: project.draft_source_sha256,
          confirm_write: true,
        },
      )
      return commitSavedProject(response.project)
    },
    installFactorTemplate: async (templateId) => {
      if (!project) throw new Error("请先选择项目")
      const response = await api.post<{ project: StrategyProject }>(
        `/strategy/projects/${project.id}/factor-templates/${encodeURIComponent(templateId)}`,
        {
          expected_source_sha256: project.draft_source_sha256,
          confirm_write: true,
        },
      )
      return commitSavedProject(response.project)
    },
    removeProject: async () => {
      if (!project) return
      await api.delete(`/strategy/projects/${project.id}?confirm_delete=true`)
      setSelectedStrategy(null)
      await refresh("sdk-v1-default")
    },
  }), [adopt, commitSavedProject, error, loading, openProject, project, projects, refresh, setSelectedStrategy])

  return <StrategySdkContext.Provider value={value}>{children}</StrategySdkContext.Provider>
}

export function useStrategySdk(): StrategySdkValue {
  const value = useContext(StrategySdkContext)
  if (!value) throw new Error("useStrategySdk must be used within StrategySdkProvider")
  return value
}
