import { useEffect, useMemo, useState } from "react"
import {
  CalendarDays,
  Check,
  Code2,
  Database,
  Eye,
  FolderKanban,
  Layers3,
  Play,
  Plus,
  Save,
  Search,
} from "lucide-react"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { useWorkspace } from "@/contexts/WorkspaceContext"
import {
  api,
  type PipelineComponentDetail,
  type PipelineComponentSummary,
  type PipelineProjectDetail,
  type PythonPipelineStage,
} from "@/lib/api"
import { usePipelineStageRun } from "@/hooks/use-pipeline-stage-run"
import { useDataProfile } from "@/lib/data-profile"
import { Widget } from "@/widgets/Widget"
import {
  SelectionChartWidget,
  SelectionDistributionWidget,
  SelectionFactorEvidenceWidget,
  SelectionFunnelWidget,
  SelectionRankingWidget,
} from "./StageResultPanels"

type WorkbenchTab = "code" | "parameters" | "preview"

const STAGES: Array<{
  id: PythonPipelineStage
  title: string
  action: string
}> = [
  { id: "selection", title: "信号模型", action: "预览信号截面" },
  { id: "portfolio", title: "组合", action: "构建目标组合" },
  { id: "execution", title: "执行", action: "生成执行方案" },
]

const stageMeta = (stage: PythonPipelineStage) => STAGES.find((item) => item.id === stage)!

function jsonText(value: unknown) {
  return JSON.stringify(value, null, 2)
}

function createInternalId(prefix: string) {
  const token = `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`
  return `${prefix}-${token}`
}

export function StageWorkbench({
  stage,
  onBack,
}: {
  stage: PythonPipelineStage
  onBack?: () => void
}) {
  const meta = stageMeta(stage)
  const {
    selectedStrategy,
    selectedStrategyRevision,
    setActiveMode,
    setSelectedStrategyRevision,
  } = useWorkspace()
  const [project, setProject] = useState<PipelineProjectDetail | null>(null)
  const [components, setComponents] = useState<PipelineComponentSummary[]>([])
  const [component, setComponent] = useState<PipelineComponentDetail | null>(null)
  const [source, setSource] = useState("")
  const [parameters, setParameters] = useState("{}")
  const [componentName, setComponentName] = useState(`自定义${meta.title}`)
  const [activeTab, setActiveTab] = useState<WorkbenchTab>("code")
  const [componentQuery, setComponentQuery] = useState("")
  const [newComponentOpen, setNewComponentOpen] = useState(false)
  const [error, setError] = useState("")
  const [busy, setBusy] = useState(false)
  const stageRun = usePipelineStageRun(selectedStrategy, stage)

  useEffect(() => {
    setActiveTab("code")
    setComponentQuery("")
    setComponentName(`自定义${meta.title}`)
    api.get<PipelineComponentSummary[]>(`/pipeline/components?stage=${stage}`)
      .then(setComponents)
      .catch((reason: Error) => setError(reason.message))
  }, [stage]) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!selectedStrategy) {
      setProject(null)
      setComponent(null)
      return
    }
    api.get<PipelineProjectDetail>(`/pipeline/projects/${selectedStrategy}`)
      .then((value) => {
        setProject(value)
        setSelectedStrategyRevision(value.revision)
      })
      .catch((reason: Error) => setError(reason.message))
  }, [selectedStrategy, selectedStrategyRevision, setSelectedStrategyRevision])

  const selectedRef = project?.components[stage]
  const filteredComponents = useMemo(() => {
    const query = componentQuery.trim().toLowerCase()
    if (!query) return components
    return components.filter((item) => (
      item.name.toLowerCase().includes(query)
      || item.description.toLowerCase().includes(query)
    ))
  }, [componentQuery, components])

  useEffect(() => {
    if (!selectedRef) return
    api.get<PipelineComponentDetail>(
      `/pipeline/components/${selectedRef.component_id}?version=${selectedRef.version}`,
    ).then((value) => {
      setComponent(value)
      setSource(value.source)
      setParameters(jsonText(value.parameters))
    }).catch((reason: Error) => setError(reason.message))
  }, [selectedRef?.component_id, selectedRef?.version]) // eslint-disable-line react-hooks/exhaustive-deps

  const isApplied = Boolean(
    component
    && selectedRef?.component_id === component.id
    && selectedRef.version === component.version,
  )
  const isPinnedComponent = Boolean(
    component && selectedRef?.component_id === component.id,
  )
  const isDirty = Boolean(
    component
    && (source !== component.source || parameters !== jsonText(component.parameters)),
  )
  const pinnedComponentName = components.find(
    (item) => item.id === selectedRef?.component_id,
  )?.name
  const primaryComponentAction = !component?.editable
    ? "另存为新组件"
    : isDirty
      ? isPinnedComponent && project?.editable ? "保存并应用" : "保存组件"
      : isApplied ? "已应用" : "应用到项目"
  const primaryComponentActionDisabled = Boolean(
    busy
    || stageRun.isRunning
    || !component
    || (component.editable && !isDirty && (isApplied || !project?.editable)),
  )

  const preview = stageRun.preview
  const output = useMemo(() => preview?.stage_outputs?.[stage], [preview, stage])
  const input = useMemo(() => {
    if (!preview) return null
    const outputs = preview.stage_outputs
    if (stage === "selection") {
      return {
        signal_date: preview.signal_date,
        eligible_count: preview.diagnostics.eligible_count,
        factor_names: preview.diagnostics.factor_names,
      }
    }
    if (stage === "portfolio") return { selection: outputs.selection }
    return { portfolio: outputs.portfolio }
  }, [preview, stage])

  async function addComponent() {
    if (!component || !componentName.trim()) return
    setBusy(true)
    setError("")
    try {
      const value = await api.post<PipelineComponentDetail>(
        `/pipeline/components/${component.id}/clone`,
        { target_id: createInternalId(stage), name: componentName.trim() },
      )
      setComponents(await api.get<PipelineComponentSummary[]>(`/pipeline/components?stage=${stage}`))
      setComponent(value)
      setSource(value.source)
      setParameters(jsonText(value.parameters))
      setActiveTab("code")
      setNewComponentOpen(false)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason))
    } finally {
      setBusy(false)
    }
  }

  async function updateProject(
    nextComponents: PipelineProjectDetail["components"],
    nextSettings: Record<string, unknown>,
  ) {
    if (!project) return
    if (!project.editable) throw new Error("当前项目只读，请到研究项目工作台复制或新建项目")
    const value = await api.put<PipelineProjectDetail>(`/pipeline/projects/${project.id}`, {
      name: project.name,
      description: project.description,
      components: nextComponents,
      settings: nextSettings,
    })
    setProject(value)
    setSelectedStrategyRevision(value.revision)
    await stageRun.clear()
  }

  async function loadComponent(componentId: string, pinnedSnapshot?: number) {
    setBusy(true)
    setError("")
    try {
      const detail = await api.get<PipelineComponentDetail>(
        `/pipeline/components/${componentId}${pinnedSnapshot ? `?version=${pinnedSnapshot}` : ""}`,
      )
      setComponent(detail)
      setSource(detail.source)
      setParameters(jsonText(detail.parameters))
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason))
    } finally {
      setBusy(false)
    }
  }

  async function applyComponent() {
    if (!project || !component) return
    setBusy(true)
    setError("")
    try {
      await updateProject({
        ...project.components,
        [stage]: { component_id: component.id, version: component.version },
      }, project.settings)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason))
    } finally {
      setBusy(false)
    }
  }

  async function saveComponent(applyAfterSave: boolean) {
    if (!component) return
    setBusy(true)
    setError("")
    try {
      const parsed = JSON.parse(parameters) as Record<string, unknown>
      const value = await api.post<PipelineComponentDetail>(
        `/pipeline/components/${component.id}/versions`,
        {
          source,
          parameters: parsed,
          notes: `saved from ${meta.title}工作台`,
        },
      )
      setComponent(value)
      setComponents(await api.get<PipelineComponentSummary[]>(`/pipeline/components?stage=${stage}`))
      if (applyAfterSave && project?.editable) {
        await updateProject({
          ...project.components,
          [stage]: { component_id: value.id, version: value.version },
        }, project.settings)
      }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason))
    } finally {
      setBusy(false)
    }
  }

  async function runPrimaryComponentAction() {
    if (!component) return
    if (!component.editable) {
      setNewComponentOpen(true)
      return
    }
    if (isDirty) {
      await saveComponent(isPinnedComponent && Boolean(project?.editable))
      return
    }
    if (!isApplied) await applyComponent()
  }

  async function runPreview() {
    if (!project) return
    setActiveTab("preview")
    setError("")
    try {
      await stageRun.run()
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason))
    }
  }

  const tabs: Array<{ id: WorkbenchTab; label: string }> = [
    { id: "code", label: "Python 代码" },
    { id: "parameters", label: "组件参数" },
    { id: "preview", label: "输入 / 输出" },
  ]

  return (
    <Widget headerless>
      <>
        <div className="python-stage-workbench">
          <section className="backtest-run-setup pipeline-stage-setup">
            <div className="backtest-run-controls pipeline-stage-controls">
              <div className="pipeline-pinned-component">
                <span>当前项目</span>
                <strong>{project?.name ?? "—"}</strong>
              </div>
              <div className="pipeline-pinned-component">
                <span>项目当前使用</span>
                <strong>{pinnedComponentName ?? "—"}</strong>
              </div>
              <div className="pipeline-stage-actions">
                {onBack ? (
                  <button className="secondary-command" type="button" onClick={onBack}>
                    <Layers3 size={14} />返回模型配置
                  </button>
                ) : null}
                <button
                  className="primary-command"
                  type="button"
                  onClick={() => void runPreview()}
                  disabled={busy || stageRun.isRunning || !project}
                >
                  <Play size={14} />{stageRun.isRunning ? "运行中…" : meta.action}
                </button>
              </div>
            </div>
          </section>

          {error && <div className="workbench-message error">{error}</div>}
          {!project && (
            <div className="workbench-message warning">
              请先在研究项目工作台选择或新建项目。
              <button className="secondary-command" type="button" onClick={() => setActiveMode("project")}>前往研究项目</button>
            </div>
          )}
          {stageRun.isStale && !stageRun.isRunning && (
            <div className="workbench-message warning">项目、数据环境或数据截至日已变化，当前阶段结果已过期，请重新运行。</div>
          )}

          <div className="pipeline-workbench-layout">
            <aside className="pipeline-workbench-sidebar">
              <div className="workbench-body">
                <div className="backtest-section-heading">
                  <div>
                    <strong>{meta.title}组件库</strong>
                  </div>
                </div>

                <div className="pipeline-library-toolbar">
                  <label className="pipeline-library-search">
                    <Search size={13} aria-hidden="true" />
                    <input
                      aria-label="搜索组件"
                      placeholder="搜索组件"
                      value={componentQuery}
                      onChange={(event) => setComponentQuery(event.target.value)}
                    />
                  </label>
                  <button
                    className="icon-command"
                    type="button"
                    title="添加新组件"
                    aria-label="添加新组件"
                    onClick={() => setNewComponentOpen(true)}
                    disabled={!component}
                  >
                    <Plus size={14} />
                  </button>
                </div>

                {filteredComponents.length > 0 ? (
                  <div className="editor-list pipeline-component-list">
                    {filteredComponents.map((item) => {
                      const isPinned = item.id === selectedRef?.component_id
                      return (
                        <button
                          key={item.id}
                          type="button"
                          className={component?.id === item.id ? "active" : ""}
                          onClick={() => void loadComponent(
                            item.id,
                            isPinned ? selectedRef?.version : undefined,
                          )}
                          disabled={busy}
                        >
                          <strong>{item.name}</strong>
                          {item.description && <small>{item.description}</small>}
                          {isPinned && <em>当前项目</em>}
                        </button>
                      )
                    })}
                  </div>
                ) : (
                  <div className="workbench-message">没有匹配的组件</div>
                )}
              </div>
            </aside>

            <main className="pipeline-workbench-main">
              <div className="backtest-run-controls pipeline-component-bar">
                <div className="pipeline-component-identity">
                  <strong>{component?.name ?? "—"}</strong>
                  <small>{component?.description || "从左侧组件库选择一个组件"}</small>
                </div>
                <div className="pipeline-component-actions">
                  <button
                    className="primary-command"
                    type="button"
                    onClick={() => void runPrimaryComponentAction()}
                    disabled={primaryComponentActionDisabled}
                  >
                    {!component?.editable ? <Plus size={14} /> : isDirty ? <Save size={14} /> : <Check size={14} />}
                    {primaryComponentAction}
                  </button>
                </div>
              </div>

              <div className="workbench-tabs" role="tablist">
                {tabs.map((tab) => (
                  <button
                    key={tab.id}
                    type="button"
                    onClick={() => setActiveTab(tab.id)}
                    role="tab"
                    aria-selected={activeTab === tab.id}
                  >
                    {tab.label}
                  </button>
                ))}
              </div>

              <div className="workbench-body pipeline-editor-body">
                {activeTab === "code" && (
                  <section>
                    <textarea
                      className="code-view code-editor pipeline-code-editor"
                      spellCheck={false}
                      value={source}
                      readOnly={!component?.editable}
                      onChange={(event) => setSource(event.target.value)}
                    />
                  </section>
                )}

                {activeTab === "parameters" && (
                  <section>
                    <textarea
                      className="code-view code-editor pipeline-code-editor"
                      spellCheck={false}
                      value={parameters}
                      readOnly={!component?.editable}
                      onChange={(event) => setParameters(event.target.value)}
                    />
                  </section>
                )}

                {activeTab === "preview" && (
                  <section>
                    <div className="pipeline-preview-grid">
                      <article>
                        <div className="detail-strip"><strong>输入</strong></div>
                        <pre className="code-view">{input ? jsonText(input) : "运行后显示"}</pre>
                      </article>
                      <article>
                        <div className="detail-strip"><strong>输出</strong></div>
                        <pre className="code-view">{output ? jsonText(output) : "运行后显示"}</pre>
                      </article>
                    </div>
                    {preview && (
                      <div className="detail-strip pipeline-preview-meta">
                        <Eye size={12} />
                        {preview.profile === "runtime" ? "本地 RQ" : "演示数据"} · {preview.signal_date}
                      </div>
                    )}
                  </section>
                )}
              </div>
            </main>
          </div>
        </div>

        <Dialog open={newComponentOpen} onOpenChange={setNewComponentOpen}>
          <DialogContent className="max-w-sm">
            <DialogHeader>
              <DialogTitle>添加{meta.title}组件</DialogTitle>
              <DialogDescription>
                以“{component?.name ?? "当前组件"}”为起点，在同一组件库中创建可编辑组件。
              </DialogDescription>
            </DialogHeader>
            <div className="pipeline-dialog-form">
              <label>
                <span>组件名称</span>
                <input
                  autoFocus
                  maxLength={100}
                  value={componentName}
                  onChange={(event) => setComponentName(event.target.value)}
                />
              </label>
            </div>
            <DialogFooter>
              <button className="secondary-command" type="button" onClick={() => setNewComponentOpen(false)}>
                取消
              </button>
              <button className="primary-command" type="button" onClick={() => void addComponent()} disabled={busy || !component || !componentName.trim()}>
                <Plus size={14} />添加组件
              </button>
            </DialogFooter>
          </DialogContent>
        </Dialog>

      </>
    </Widget>
  )
}

type SignalFactor = {
  name: string
  source?: string
  direction?: string
  weight?: number
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {}
}

function asFiniteNumber(value: unknown, fallback: number): number {
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed : fallback
}

function SignalModelEditor({ onAdvanced }: { onAdvanced: (stage?: "selection" | "portfolio") => void }) {
  const {
    selectedDate,
    selectedStrategy,
    selectedStrategyRevision,
    setActiveMode,
    setSelectedDate,
    setSelectedStrategyRevision,
  } = useWorkspace()
  const [profile] = useDataProfile()
  const [project, setProject] = useState<PipelineProjectDetail | null>(null)
  const [frequency, setFrequency] = useState("monthly")
  const [normalization, setNormalization] = useState("percentile_rank")
  const [count, setCount] = useState(20)
  const [exitRank, setExitRank] = useState(30)
  const [coverage, setCoverage] = useState(0.5)
  const [weights, setWeights] = useState<Record<string, number>>({})
  const [allocationMethod, setAllocationMethod] = useState("equal_weight")
  const [rankDecay, setRankDecay] = useState(1)
  const [maxWeight, setMaxWeight] = useState(0.1)
  const [grossExposure, setGrossExposure] = useState(1)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState("")
  const [saved, setSaved] = useState(false)
  const [dirty, setDirty] = useState(false)
  const [workspaceView, setWorkspaceView] = useState<"build" | "results">("build")
  const stageRun = usePipelineStageRun(selectedStrategy, "portfolio")

  useEffect(() => {
    if (!selectedStrategy) {
      setProject(null)
      return
    }
    api.get<PipelineProjectDetail>(`/pipeline/projects/${selectedStrategy}`)
      .then((value) => {
        const manifest = value.component_manifest.find((item) => item.stage === "selection")
        const rawParameters = asRecord(manifest?.parameters)
        const parameters = {
          ...asRecord(rawParameters.selection),
          ...rawParameters,
        }
        const portfolioManifest = value.component_manifest.find((item) => item.stage === "portfolio")
        const rawPortfolioParameters = asRecord(portfolioManifest?.parameters)
        const portfolioParameters = {
          ...asRecord(rawPortfolioParameters.portfolio),
          ...rawPortfolioParameters,
        }
        const factors = Array.isArray(value.settings.factors)
          ? value.settings.factors.filter((item): item is SignalFactor => Boolean(item && typeof item === "object" && !Array.isArray(item) && String((item as SignalFactor).name || "").trim()))
          : []
        const parameterWeights = asRecord(parameters.factor_weights)
        const nextCount = Math.max(1, Math.round(asFiniteNumber(parameters.count, 20)))
        setProject(value)
        setFrequency(String(parameters.signal_frequency || "monthly"))
        setNormalization(String(parameters.normalization || "percentile_rank"))
        setCount(nextCount)
        setExitRank(Math.max(nextCount, Math.round(asFiniteNumber(parameters.exit_rank, nextCount))))
        setCoverage(Math.min(1, Math.max(0, asFiniteNumber(parameters.min_factor_coverage, 0.5))))
        setAllocationMethod(String(portfolioParameters.optimizer || "equal_weight"))
        setRankDecay(Math.max(0, asFiniteNumber(portfolioParameters.rank_decay, 1)))
        setMaxWeight(Math.min(1, Math.max(0.001, asFiniteNumber(portfolioParameters.max_weight, 0.1))))
        setGrossExposure(Math.min(1, Math.max(0.001, asFiniteNumber(portfolioParameters.max_gross_exposure, 1))))
        setWeights(Object.fromEntries(factors.map((factor) => [
          factor.name,
          Math.max(0, asFiniteNumber(parameterWeights[factor.name], asFiniteNumber(factor.weight, 1))),
        ])))
        setSaved(false)
        setDirty(false)
        setSelectedStrategyRevision(value.revision)
      })
      .catch((reason: Error) => setError(reason.message))
  }, [selectedStrategy, selectedStrategyRevision, setSelectedStrategyRevision])

  const factors = useMemo<SignalFactor[]>(() => (
    Array.isArray(project?.settings.factors)
      ? project.settings.factors.filter((item): item is SignalFactor => Boolean(item && typeof item === "object" && !Array.isArray(item) && String((item as SignalFactor).name || "").trim()))
      : []
  ), [project?.settings.factors])
  const weightTotal = factors.reduce((sum, factor) => sum + (weights[factor.name] ?? 0), 0)

  async function saveModel() {
    if (!project) return
    if (!project.editable) {
      setError("当前项目只读，请先在研究项目工作区复制项目")
      return
    }
    if (exitRank < count) {
      setError("退出排名必须大于或等于目标数量")
      return
    }
    if (factors.length && weightTotal <= 0) {
      setError("至少需要一个权重大于 0 的因子")
      return
    }
    if (maxWeight <= 0 || grossExposure <= 0) {
      setError("单只权重上限和目标总仓位必须大于 0")
      return
    }
    setBusy(true)
    setError("")
    setSaved(false)
    try {
      const stageParameters = asRecord(project.settings.stage_parameters)
      const currentSelection = asRecord(stageParameters.selection)
      const currentPortfolio = asRecord(stageParameters.portfolio)
      const value = await api.put<PipelineProjectDetail>(`/pipeline/projects/${project.id}`, {
        name: project.name,
        description: project.description,
        components: project.components,
        settings: {
          ...project.settings,
          stage_parameters: {
            ...stageParameters,
            selection: {
              ...currentSelection,
              signal_frequency: frequency,
              normalization,
              count,
              exit_rank: exitRank,
              min_factor_coverage: coverage,
              factor_weights: Object.fromEntries(factors.map((factor) => [factor.name, weights[factor.name] ?? 0])),
            },
            portfolio: {
              ...currentPortfolio,
              optimizer: allocationMethod,
              rank_decay: rankDecay,
              max_weight: maxWeight,
              max_gross_exposure: grossExposure,
            },
          },
        },
      })
      setProject(value)
      setSelectedStrategyRevision(value.revision)
      await stageRun.clear()
      window.dispatchEvent(new CustomEvent("alphalab:projectUpdated", { detail: value }))
      setSaved(true)
      setDirty(false)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason))
    } finally {
      setBusy(false)
    }
  }

  async function previewModel() {
    setError("")
    try {
      const preview = await stageRun.run()
      if (preview) setWorkspaceView("results")
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason))
    }
  }

  function useEqualWeights() {
    setWeights(Object.fromEntries(factors.map((factor) => [factor.name, 1])))
    setSaved(false)
    setDirty(true)
  }

  function toggleFactor(name: string) {
    setWeights((current) => ({ ...current, [name]: (current[name] ?? 0) > 0 ? 0 : 1 }))
    setSaved(false)
    setDirty(true)
  }

  const activeFactorCount = factors.filter((factor) => (weights[factor.name] ?? 0) > 0).length
  const sourceLabels: Record<string, string> = {
    technical: "技术",
    fundamental: "财务",
    expression: "表达式",
  }
  const allocationLabels: Record<string, string> = {
    equal_weight: "等权",
    score_weight: "按综合得分",
    rank_decay: "按排名衰减",
  }

  return (
    <Widget headerless>
      <div className="signal-model-shell">
        <header className="signal-model-header">
          <div className="signal-model-title">
            <span className="signal-model-mark"><Layers3 size={18} /></span>
            <div>
              <div><h1>信号模型</h1><Badge variant="outline">{profile === "demo" ? "示例数据" : "本地数据"}</Badge></div>
              <p>把项目因子变成每个决策日的横截面排名与目标仓位</p>
            </div>
          </div>
          <div className="signal-model-header-actions">
            <Button variant="outline" size="sm" onClick={() => setActiveMode("project")}><FolderKanban />{project?.name ?? "选择项目"}</Button>
            <Button variant="ghost" size="sm" onClick={() => onAdvanced("selection")}><Code2 />高级组件</Button>
          </div>
        </header>

        <section className="signal-model-asof" aria-label="当前截面">
          <div className="signal-model-asof-heading">
            <span><CalendarDays size={15} /></span>
            <div><strong>当前截面</strong><small>选择这次预览使用的数据截止日；留空表示最新可用数据</small></div>
          </div>
          <div className="signal-model-date-control">
            <input aria-label="截面日期" type="date" value={selectedDate ?? ""} onChange={(event) => setSelectedDate(event.target.value || null)} />
            <Button variant={selectedDate ? "outline" : "secondary"} size="sm" onClick={() => setSelectedDate(null)}>最新数据</Button>
          </div>
          <div className="signal-model-asof-status">
            <Database size={13} />
            <span>{stageRun.preview ? `实际截面 ${stageRun.preview.signal_date}` : selectedDate ? `计划截面 ${selectedDate}` : "将在数据最新日期生成"}</span>
          </div>
          <Button size="sm" onClick={() => void previewModel()} disabled={busy || stageRun.isRunning || !project || dirty} isLoading={stageRun.isRunning} title={dirty ? "模型配置有改动，请先保存" : "生成当前日期的横截面排名"}><Play />{dirty ? "请先保存" : "生成截面"}</Button>
        </section>

        {error ? <div className="workbench-message error signal-model-message">{error}</div> : null}
        {!project ? (
          <div className="workbench-message warning signal-model-message">请先选择研究项目，信号模型才能读取项目因子。</div>
        ) : null}

        {project ? (
          <Tabs className="signal-model-tabs" value={workspaceView} onValueChange={(value) => setWorkspaceView(value as "build" | "results")}>
            <div className="signal-model-toolbar">
              <TabsList>
                <TabsTrigger value="build">模型构建</TabsTrigger>
                <TabsTrigger value="results">当前截面{stageRun.isStale ? <span className="signal-model-tab-warning">需要更新</span> : null}</TabsTrigger>
              </TabsList>
              {workspaceView === "build" ? (
                <span className="signal-model-toolbar-hint">选择因子 → 合成排名 → 生成信号集合 → 分配目标仓位</span>
              ) : (
                <span className={stageRun.preview && !stageRun.isStale ? "signal-model-result-ready" : "signal-model-toolbar-hint"}>
                  {stageRun.preview ? `${stageRun.isStale ? "已有结果已过期" : "已生成"} · ${stageRun.preview.signal_date}` : "尚未生成当前截面"}
                </span>
              )}
            </div>

            <TabsContent className="signal-model-content" value="build">
              <div className="signal-model-build-grid">
                <section className="signal-model-section">
                  <div className="signal-model-section-header">
                    <div><span className="signal-model-section-index">1</span><span><strong>选择参与合成的因子</strong><small>权重为 0 的因子会停用，也不计入数据覆盖率</small></span></div>
                    <div><Badge variant="outline">启用 {activeFactorCount}/{factors.length}</Badge><Button variant="ghost" size="sm" onClick={() => setActiveMode("factor")}>管理因子</Button>{factors.length ? <Button variant="outline" size="sm" onClick={useEqualWeights}>全部等权</Button> : null}</div>
                  </div>
                  {factors.length ? (
                    <div className="signal-factor-table">
                      <div className="signal-factor-table-head"><span>状态 / 因子</span><span>优选方向</span><span>有效权重</span></div>
                      {factors.map((factor) => {
                        const weight = weights[factor.name] ?? 0
                        const enabled = weight > 0
                        const share = weightTotal > 0 ? weight / weightTotal : 0
                        return (
                          <div className={enabled ? "signal-factor-row" : "signal-factor-row disabled"} key={factor.name}>
                            <button className={enabled ? "signal-factor-toggle active" : "signal-factor-toggle"} type="button" aria-label={`${enabled ? "停用" : "启用"}${factor.name}`} onClick={() => toggleFactor(factor.name)}><i /></button>
                            <div className="signal-factor-name"><strong>{factor.name}</strong><small>{sourceLabels[factor.source ?? ""] ?? factor.source ?? "因子"}{enabled ? ` · 组合占比 ${(share * 100).toFixed(0)}%` : " · 已停用"}</small></div>
                            <span className="signal-factor-direction">{factor.direction === "short" ? "值小优" : "值大优"}</span>
                            <input aria-label={`${factor.name} 权重`} type="number" min="0" step="0.1" value={weight} onChange={(event) => { setWeights((current) => ({ ...current, [factor.name]: Math.max(0, asFiniteNumber(event.target.value, 0)) })); setSaved(false); setDirty(true) }} />
                          </div>
                        )
                      })}
                    </div>
                  ) : (
                    <div className="signal-model-empty">当前项目还没有因子。<Button size="sm" onClick={() => setActiveMode("factor")}>去因子研究添加</Button></div>
                  )}
                </section>

                <div className="signal-model-settings-stack">
                  <section className="signal-model-section">
                    <div className="signal-model-section-header">
                      <div><span className="signal-model-section-index">2</span><span><strong>统一尺度并合成分数</strong><small>先标准化各因子，再按上面的有效权重求和</small></span></div>
                    </div>
                    <div className="signal-model-form-grid two">
                      <label><span>多久重新计算一次</span><select value={frequency} onChange={(event) => { setFrequency(event.target.value); setSaved(false); setDirty(true) }}><option value="daily">每日</option><option value="weekly">每周</option><option value="monthly">每月</option></select><small>回测会在该频率的每个决策日重新生成排名</small></label>
                      <label><span>把不同量纲变到同一尺度</span><select value={normalization} onChange={(event) => { setNormalization(event.target.value); setSaved(false); setDirty(true) }}><option value="percentile_rank">横截面百分位排名</option><option value="zscore">横截面 Z-score</option></select><small>{normalization === "percentile_rank" ? "结果落在 0–1，直观且对极值更稳健" : "保留因子间标准差距离，极值影响更明显"}</small></label>
                    </div>
                  </section>

                  <section className="signal-model-section">
                    <div className="signal-model-section-header">
                      <div><span className="signal-model-section-index">3</span><span><strong>定义信号集合与换仓缓冲</strong><small>综合排名决定新证券进入和原持仓退出的边界</small></span></div>
                    </div>
                    <div className="signal-model-form-grid three">
                      <label><span>每期输出多少只</span><input type="number" min="1" value={count} onChange={(event) => { const value = Math.max(1, Math.round(asFiniteNumber(event.target.value, 1))); setCount(value); setExitRank((current) => Math.max(current, value)); setSaved(false); setDirty(true) }} /><small>新证券需要进入综合排名前 {count} 名</small></label>
                      <label><span>持仓跌出前多少名才移除</span><input type="number" min={count} value={exitRank} onChange={(event) => { setExitRank(Math.max(count, Math.round(asFiniteNumber(event.target.value, count)))); setSaved(false); setDirty(true) }} /><small>第 {count + 1}–{exitRank} 名是持仓缓冲区</small></label>
                      <label><span>至少多少因子有有效值</span><input type="number" min="0" max="1" step="0.05" value={coverage} onChange={(event) => { setCoverage(Math.min(1, Math.max(0, asFiniteNumber(event.target.value, 0)))); setSaved(false); setDirty(true) }} /><small>{Math.round(coverage * 100)}% 覆盖后才参与排名</small></label>
                    </div>
                  </section>

                  <section className="signal-model-section">
                    <div className="signal-model-section-header">
                      <div><span className="signal-model-section-index">4</span><span><strong>把信号转换成目标仓位</strong><small>在同一个决策日内，将入选证券的排名与得分映射为可执行权重</small></span></div>
                      <div><Button variant="ghost" size="sm" onClick={() => onAdvanced("portfolio")}><Code2 />高级仓位组件</Button></div>
                    </div>
                    <div className="signal-allocation-methods" role="radiogroup" aria-label="仓位分配方式">
                      {[
                        { id: "equal_weight", title: "等权", detail: "每只证券目标权重相同，最容易解释" },
                        { id: "score_weight", title: "按综合得分", detail: "得分越高权重越大，保留信号强弱" },
                        { id: "rank_decay", title: "按排名衰减", detail: "越靠前权重越大，对极端分数更稳健" },
                      ].map((method) => (
                        <button
                          className={allocationMethod === method.id ? "active" : ""}
                          key={method.id}
                          type="button"
                          role="radio"
                          aria-checked={allocationMethod === method.id}
                          onClick={() => { setAllocationMethod(method.id); setSaved(false); setDirty(true) }}
                        >
                          <span><i /> <strong>{method.title}</strong></span>
                          <small>{method.detail}</small>
                        </button>
                      ))}
                    </div>
                    <div className="signal-model-form-grid three allocation">
                      <label><span>目标总仓位</span><input type="number" min="0.01" max="1" step="0.05" value={grossExposure} onChange={(event) => { setGrossExposure(Math.min(1, Math.max(0.01, asFiniteNumber(event.target.value, 1)))); setSaved(false); setDirty(true) }} /><small>本期最多使用 {Math.round(grossExposure * 100)}% 资金，其余保留现金</small></label>
                      <label><span>单只证券权重上限</span><input type="number" min="0.001" max="1" step="0.01" value={maxWeight} onChange={(event) => { setMaxWeight(Math.min(1, Math.max(0.001, asFiniteNumber(event.target.value, 0.1)))); setSaved(false); setDirty(true) }} /><small>单只最多 {Math.round(maxWeight * 100)}%；约束不足时会保留额外现金</small></label>
                      {allocationMethod === "rank_decay" ? (
                        <label><span>排名衰减强度</span><input type="number" min="0" max="4" step="0.1" value={rankDecay} onChange={(event) => { setRankDecay(Math.min(4, Math.max(0, asFiniteNumber(event.target.value, 1)))); setSaved(false); setDirty(true) }} /><small>0 接近等权；数值越大，仓位越集中在头部</small></label>
                      ) : (
                        <div className="signal-allocation-summary"><span>预计可分配上限</span><strong>{Math.round(Math.min(grossExposure, count * maxWeight) * 100)}%</strong><small>{count} 只 × 单只上限 {Math.round(maxWeight * 100)}%</small></div>
                      )}
                    </div>
                  </section>

                  {!project.editable ? <div className="workbench-message warning">当前项目只读；可以生成截面查看结果，复制项目后才能修改模型。</div> : null}
                </div>
              </div>
            </TabsContent>

            <TabsContent className="signal-model-content signal-model-results" value="results">
              {!stageRun.preview ? (
                <div className="signal-model-results-empty">
                  <Layers3 size={28} />
                  <strong>还没有截面结果</strong>
                  <span>保存模型后，选择上方数据截止日并生成截面。这里会集中展示覆盖情况、因子关系、综合得分、证券排名与目标权重。</span>
                  <Button size="sm" onClick={() => void previewModel()} disabled={busy || stageRun.isRunning || dirty} isLoading={stageRun.isRunning}><Play />生成当前截面</Button>
                </div>
              ) : (
                <div className="signal-results-grid">
                  {stageRun.isStale && !stageRun.isRunning ? <div className="workbench-message warning signal-results-warning">项目、数据环境或截面日期已经变化，当前结果仅供参考，请重新生成。</div> : null}
                  <section className="signal-result-card coverage">
                    <header><div><strong>数据覆盖与筛选漏斗</strong><small>哪些证券进入评分、哪些证券被排除</small></div><Badge variant="outline">{stageRun.preview.signal_date}</Badge></header>
                    <SelectionFunnelWidget embedded />
                  </section>
                  <section className="signal-result-card evidence">
                    <header><div><strong>因子结构</strong><small>检查权重、频率以及当前截面的因子冗余</small></div></header>
                    <SelectionFactorEvidenceWidget embedded />
                  </section>
                  <section className="signal-result-card ranking">
                    <header><div><strong>综合排名与目标仓位</strong><small>点击证券可联动查看右侧入选日行情</small></div></header>
                    <SelectionRankingWidget embedded />
                  </section>
                  <section className="signal-result-card distribution">
                    <header><div><strong>得分分布</strong><small>确认综合分数的形状与本期入选门槛</small></div></header>
                    <SelectionDistributionWidget embedded />
                  </section>
                  <section className="signal-result-card chart">
                    <header><div><strong>入选证券行情</strong><small>在排名中选择证券，观察截面日前一年的价格位置</small></div></header>
                    <SelectionChartWidget embedded />
                  </section>
                </div>
              )}
            </TabsContent>
          </Tabs>
        ) : null}

        {project ? (
          <footer className="signal-model-footer">
            <div>
              <strong>{dirty ? "模型配置尚未保存" : saved ? "模型已保存" : "模型配置已同步"}</strong>
              <span>{dirty ? "保存后才能用新配置生成截面" : `${activeFactorCount} 个有效因子 · ${normalization === "percentile_rank" ? "百分位" : "Z-score"}合成 · 输出 ${count} 只 · ${allocationLabels[allocationMethod] ?? allocationMethod}`}</span>
            </div>
            <Button variant={dirty ? "default" : "outline"} size="sm" onClick={() => void saveModel()} disabled={busy || !project.editable || !dirty} isLoading={busy}><Save />{dirty ? "保存模型" : "已保存"}</Button>
          </footer>
        ) : null}
      </div>
    </Widget>
  )
}

export function SelectionWorkbenchWidget() {
  const [advancedStage, setAdvancedStage] = useState<"selection" | "portfolio" | null>(null)
  return advancedStage
    ? <StageWorkbench stage={advancedStage} onBack={() => setAdvancedStage(null)} />
    : <SignalModelEditor onAdvanced={(stage = "selection") => setAdvancedStage(stage)} />
}
export const PortfolioWorkbenchWidget = () => <StageWorkbench stage="portfolio" />
export const ExecutionWorkbenchWidget = () => <StageWorkbench stage="execution" />
