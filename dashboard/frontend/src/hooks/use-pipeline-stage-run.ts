import { useQuery, useQueryClient } from "@tanstack/react-query"

import { api, type PipelineAnalysis, type PipelinePreview, type PythonPipelineStage } from "@/lib/api"

const previewKey = (projectId: string | null, stage: PythonPipelineStage) => ["pipeline", "preview", projectId, stage] as const
const analysisKey = (projectId: string | null, stage: PythonPipelineStage) => ["pipeline", "analysis", projectId, stage] as const

export function usePipelineStageRun(projectId: string | null, stage: PythonPipelineStage) {
  const queryClient = useQueryClient()
  const preview = useQuery({
    queryKey: previewKey(projectId, stage),
    queryFn: () => api.post<PipelinePreview>(
      `/pipeline/projects/${projectId}/preview`,
      { stage, profile: "demo" },
    ),
    enabled: false,
    staleTime: Infinity,
  })
  const analysis = useQuery({
    queryKey: analysisKey(projectId, stage),
    queryFn: () => api.post<PipelineAnalysis>(
      `/pipeline/projects/${projectId}/analysis`,
      { stage, profile: "demo", months: 12 },
    ),
    enabled: false,
    staleTime: Infinity,
  })

  async function run() {
    if (!projectId) throw new Error("请先选择策略项目")
    const [previewResult, analysisResult] = await Promise.all([
      preview.refetch({ throwOnError: true }),
      analysis.refetch({ throwOnError: true }),
    ])
    return { preview: previewResult.data, analysis: analysisResult.data }
  }

  async function clear() {
    await Promise.all([
      queryClient.cancelQueries({ queryKey: ["pipeline", "preview", projectId] }),
      queryClient.cancelQueries({ queryKey: ["pipeline", "analysis", projectId] }),
    ])
    queryClient.removeQueries({ queryKey: ["pipeline", "preview", projectId] })
    queryClient.removeQueries({ queryKey: ["pipeline", "analysis", projectId] })
  }

  return {
    preview: preview.data ?? null,
    analysis: analysis.data ?? null,
    isRunning: preview.isFetching || analysis.isFetching,
    error: preview.error ?? analysis.error,
    run,
    clear,
  }
}
