import { useEffect, useMemo, useState } from "react"
import {
  Check,
  Eye,
  Play,
  Plus,
  Save,
  Search,
} from "lucide-react"

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
import { Widget } from "@/widgets/Widget"

type WorkbenchTab = "code" | "parameters" | "preview"

const STAGES: Array<{
  id: PythonPipelineStage
  title: string
  action: string
}> = [
  { id: "universe", title: "标的池", action: "生成标的池" },
  { id: "selection", title: "选股", action: "生成选股池" },
  { id: "timing", title: "择时", action: "计算择时仓位" },
  { id: "portfolio", title: "组合", action: "构建目标组合" },
  { id: "risk", title: "风控", action: "应用风险约束" },
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

export function StageWorkbench({ stage }: { stage: PythonPipelineStage }) {
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
    if (stage === "universe") {
      return {
        signal_date: preview.signal_date,
        eligible_count: preview.diagnostics.eligible_count,
        factor_names: preview.diagnostics.factor_names,
      }
    }
    if (stage === "selection") return { universe: outputs.universe }
    if (stage === "timing") return { universe: outputs.universe, selection: outputs.selection }
    if (stage === "portfolio") return { selection: outputs.selection, timing: outputs.timing }
    if (stage === "risk") return { portfolio: outputs.portfolio }
    return { risk: outputs.risk }
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

export const UniverseWorkbenchWidget = () => <StageWorkbench stage="universe" />
export const SelectionWorkbenchWidget = () => <StageWorkbench stage="selection" />
export const TimingWorkbenchWidget = () => <StageWorkbench stage="timing" />
export const PortfolioWorkbenchWidget = () => <StageWorkbench stage="portfolio" />
export const RiskWorkbenchWidget = () => <StageWorkbench stage="risk" />
export const ExecutionWorkbenchWidget = () => <StageWorkbench stage="execution" />
