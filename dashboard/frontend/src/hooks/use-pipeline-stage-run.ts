import { useQuery, useQueryClient } from "@tanstack/react-query"

import { useWorkspace } from "@/contexts/WorkspaceContext"
import { api, type PipelinePreview, type PythonPipelineStage } from "@/lib/api"
import { useDataProfile } from "@/lib/data-profile"

const previewKey = (
  projectId: string | null,
  revision: number | null,
  stage: PythonPipelineStage,
  profile: "demo" | "runtime",
  asOfDate: string | null,
) => ["pipeline", "preview", projectId, revision, stage, profile, asOfDate] as const

export function usePipelineStageRun(projectId: string | null, stage: PythonPipelineStage) {
  const queryClient = useQueryClient()
  const [profile] = useDataProfile()
  const {
    selectedDate,
    selectedStrategyRevision,
    stageRunContexts,
    setStageRunContext,
  } = useWorkspace()
  const currentContext = {
    projectId: projectId ?? "",
    revision: selectedStrategyRevision ?? 0,
    profile,
    asOfDate: selectedDate,
  }
  const priorContext = stageRunContexts[stage]
  const isStale = Boolean(priorContext && (
    priorContext.projectId !== currentContext.projectId
    || priorContext.revision !== currentContext.revision
    || priorContext.profile !== currentContext.profile
    || priorContext.asOfDate !== currentContext.asOfDate
  ))
  const preview = useQuery({
    queryKey: previewKey(projectId, selectedStrategyRevision, stage, profile, selectedDate),
    queryFn: () => api.post<PipelinePreview>(
      `/pipeline/projects/${projectId}/preview`,
      { stage, profile, ...(selectedDate ? { as_of_date: selectedDate } : {}) },
    ),
    enabled: false,
    staleTime: Infinity,
  })

  async function run() {
    if (!projectId) throw new Error("请先选择策略项目")
    const result = await preview.refetch({ throwOnError: true })
    if (result.data) {
      for (const executedStage of result.data.executed_stages) {
        queryClient.setQueryData(
          previewKey(projectId, result.data.revision, executedStage, profile, selectedDate),
          result.data,
        )
        setStageRunContext(executedStage, {
          ...currentContext,
          revision: result.data.revision,
        })
      }
    }
    return result.data
  }

  async function clear() {
    await queryClient.cancelQueries({ queryKey: ["pipeline", "preview", projectId] })
    queryClient.removeQueries({ queryKey: ["pipeline", "preview", projectId] })
  }

  return {
    preview: preview.data ?? null,
    profile,
    asOfDate: selectedDate,
    isRunning: preview.isFetching,
    error: preview.error,
    isStale,
    run,
    clear,
  }
}
