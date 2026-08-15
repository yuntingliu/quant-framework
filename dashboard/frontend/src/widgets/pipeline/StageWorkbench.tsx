import { useEffect, useMemo, useState } from "react"
import {
  Check,
  Eye,
  GitCommitHorizontal,
  Play,
  Plus,
  Save,
} from "lucide-react"

import { useWorkspace } from "@/contexts/WorkspaceContext"
import {
  api,
  type PipelineComponentDetail,
  type PipelineComponentSummary,
  type PipelinePreview,
  type PipelineProjectDetail,
  type PipelineProjectSummary,
  type PythonPipelineStage,
} from "@/lib/api"
import type { WorkspaceMode } from "@/layouts/presets"
import { Widget } from "@/widgets/Widget"

type WorkbenchTab = "code" | "parameters" | "settings" | "preview"

const STAGES: Array<{
  id: PythonPipelineStage
  mode: WorkspaceMode
  title: string
  verb: string
  contract: string
}> = [
  { id: "universe", mode: "universe", title: "标的池", verb: "build_universe", contract: "合格候选 → symbols" },
  { id: "selection", mode: "selection", title: "选股", verb: "select_assets", contract: "标的池 + 横截面信号 → selected / scores" },
  { id: "timing", mode: "timing", title: "择时", verb: "compute_exposure", contract: "市场历史 → exposure" },
  { id: "portfolio", mode: "portfolio", title: "组合", verb: "construct_portfolio", contract: "选股 + 择时 → proposed weights" },
  { id: "risk", mode: "risk", title: "风控", verb: "apply_risk", contract: "组合权重 → constrained weights" },
  { id: "execution", mode: "execution", title: "执行", verb: "create_orders", contract: "目标权重 → execution assumptions" },
]

const stageMeta = (stage: PythonPipelineStage) => STAGES.find((item) => item.id === stage)!

function jsonText(value: unknown) {
  return JSON.stringify(value, null, 2)
}

export function StageWorkbench({ stage }: { stage: PythonPipelineStage }) {
  const meta = stageMeta(stage)
  const { selectedStrategy, setSelectedStrategy, setActiveMode } = useWorkspace()
  const [projects, setProjects] = useState<PipelineProjectSummary[]>([])
  const [project, setProject] = useState<PipelineProjectDetail | null>(null)
  const [components, setComponents] = useState<PipelineComponentSummary[]>([])
  const [component, setComponent] = useState<PipelineComponentDetail | null>(null)
  const [source, setSource] = useState("")
  const [parameters, setParameters] = useState("{}")
  const [settings, setSettings] = useState("{}")
  const [targetId, setTargetId] = useState("my-six-stage-strategy")
  const [componentTargetId, setComponentTargetId] = useState(`my-${stage}-component`)
  const [preview, setPreview] = useState<PipelinePreview | null>(null)
  const [activeTab, setActiveTab] = useState<WorkbenchTab>("code")
  const [message, setMessage] = useState("")
  const [error, setError] = useState("")
  const [busy, setBusy] = useState(false)

  const supportsSettings = stage === "universe" || stage === "selection"

  async function refreshProjects(preferred?: string) {
    const values = await api.get<PipelineProjectSummary[]>("/pipeline/projects")
    setProjects(values)
    const requested = preferred ?? selectedStrategy
    const next = values.find((item) => item.id === requested)?.id
      ?? values.find((item) => item.id === "six-stage-default")?.id
      ?? values[0]?.id
      ?? ""
    if (next) setSelectedStrategy(next)
  }

  useEffect(() => {
    setActiveTab("code")
    setComponentTargetId(`my-${stage}-component`)
    Promise.all([
      api.get<PipelineProjectSummary[]>("/pipeline/projects"),
      api.get<PipelineComponentSummary[]>(`/pipeline/components?stage=${stage}`),
    ]).then(([projectRows, componentRows]) => {
      setProjects(projectRows)
      setComponents(componentRows)
      const next = projectRows.find((item) => item.id === selectedStrategy)?.id
        ?? projectRows.find((item) => item.id === "six-stage-default")?.id
        ?? projectRows[0]?.id
        ?? null
      setSelectedStrategy(next)
    }).catch((reason: Error) => setError(reason.message))
  }, [stage]) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!selectedStrategy) return
    setPreview(null)
    setMessage("")
    api.get<PipelineProjectDetail>(`/pipeline/projects/${selectedStrategy}`)
      .then((value) => {
        setProject(value)
        setSettings(jsonText(value.settings))
      })
      .catch((reason: Error) => setError(reason.message))
  }, [selectedStrategy])

  const selectedRef = project?.components[stage]
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

  async function cloneProject() {
    if (!project) return
    setBusy(true)
    setError("")
    setMessage("")
    try {
      const value = await api.post<PipelineProjectDetail>(
        `/pipeline/projects/${project.id}/clone`,
        { target_id: targetId },
      )
      await refreshProjects(value.id)
      setMessage(`已创建可编辑项目 ${value.id}`)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason))
    } finally {
      setBusy(false)
    }
  }

  async function addComponent() {
    if (!component) return
    setBusy(true)
    setError("")
    setMessage("")
    try {
      const value = await api.post<PipelineComponentDetail>(
        `/pipeline/components/${component.id}/clone`,
        { target_id: componentTargetId },
      )
      setComponents(await api.get<PipelineComponentSummary[]>(`/pipeline/components?stage=${stage}`))
      setComponent(value)
      setSource(value.source)
      setParameters(jsonText(value.parameters))
      setActiveTab("code")
      if (project?.editable) {
        await updateProject(value, value.version)
        setMessage(`已添加并应用组件 ${value.id}`)
      } else {
        setMessage(`已添加组件 ${value.id}；请先创建自己的项目再应用`)
      }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason))
    } finally {
      setBusy(false)
    }
  }

  async function updateProject(
    nextComponent = component,
    nextVersion = component?.version,
    nextSettings?: Record<string, unknown>,
  ) {
    if (!project || !nextComponent || !nextVersion) return
    if (!project.editable) throw new Error("系统预置项目不可修改，请先创建自己的策略项目")
    const refs = {
      ...project.components,
      [stage]: { component_id: nextComponent.id, version: nextVersion },
    }
    const value = await api.put<PipelineProjectDetail>(`/pipeline/projects/${project.id}`, {
      name: project.name,
      description: project.description,
      components: refs,
      settings: nextSettings ?? project.settings,
    })
    setProject(value)
    await refreshProjects(value.id)
  }

  async function applyComponent(componentId: string, version?: number) {
    setBusy(true)
    setError("")
    setMessage("")
    try {
      const detail = await api.get<PipelineComponentDetail>(
        `/pipeline/components/${componentId}${version ? `?version=${version}` : ""}`,
      )
      setComponent(detail)
      setSource(detail.source)
      setParameters(jsonText(detail.parameters))
      if (project?.editable) {
        await updateProject(detail, detail.version)
        setMessage(`项目已固定到 ${detail.id}@${detail.version}`)
      } else {
        setMessage(`当前仅查看 ${detail.id}@${detail.version}；系统预置项目不会被改动`)
      }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason))
    } finally {
      setBusy(false)
    }
  }

  async function saveVersion() {
    if (!component) return
    setBusy(true)
    setError("")
    setMessage("")
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
      if (project?.editable && project.components[stage].component_id === value.id) {
        await updateProject(value, value.version)
      }
      setMessage(`已保存并固定到 ${value.id}@${value.version}`)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason))
    } finally {
      setBusy(false)
    }
  }

  async function saveSettings() {
    setBusy(true)
    setError("")
    setMessage("")
    try {
      const parsed = JSON.parse(settings) as Record<string, unknown>
      await updateProject(component, component?.version, parsed)
      setMessage("项目数据与信号设置已保存")
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason))
    } finally {
      setBusy(false)
    }
  }

  async function runPreview() {
    if (!project) return
    setActiveTab("preview")
    setBusy(true)
    setError("")
    setMessage("")
    try {
      const value = await api.post<PipelinePreview>(
        `/pipeline/projects/${project.id}/preview`,
        { profile: "demo" },
      )
      setPreview(value)
      setMessage(`完整策略已运行，正在查看 ${meta.title} 阶段`)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason))
    } finally {
      setBusy(false)
    }
  }

  const tabs: Array<{ id: WorkbenchTab; label: string }> = [
    { id: "code", label: "Python 代码" },
    { id: "parameters", label: "组件参数" },
    ...(supportsSettings ? [{ id: "settings" as const, label: "项目设置" }] : []),
    { id: "preview", label: "输入 / 输出" },
  ]

  return (
    <Widget title={`${meta.title}工作台`} bodyPadding="none">
      <div className="python-stage-workbench">
        <nav className="workbench-tabs pipeline-stage-tabs" aria-label="六阶段策略流程">
          {STAGES.map((item, index) => (
            <button
              key={item.id}
              type="button"
              role="tab"
              aria-selected={item.id === stage}
              onClick={() => setActiveMode(item.mode)}
            >
              {index + 1}. {item.title}
            </button>
          ))}
        </nav>

        <section className="backtest-run-setup pipeline-stage-setup">
          <div className="backtest-section-heading">
            <div>
              <strong>{meta.verb}(context)</strong>
              <span>阶段 {STAGES.findIndex((item) => item.id === stage) + 1} / 6 · {meta.contract}</span>
            </div>
            <small>{project?.id ?? "—"} · revision {project?.revision ?? "—"}</small>
          </div>
          <div className="backtest-run-controls pipeline-stage-controls">
            <label>
              <span>策略项目</span>
              <select
                value={project?.id ?? ""}
                onChange={(event) => setSelectedStrategy(event.target.value)}
              >
                {projects.map((item) => (
                  <option key={item.id} value={item.id}>{item.name} · r{item.revision}</option>
                ))}
              </select>
            </label>
            <span className={`status-pill ${project?.built_in ? "neutral" : "ready"}`}>
              {project?.built_in ? "系统预置项目" : "用户项目"}
            </span>
            <button
              className="primary-command"
              type="button"
              onClick={() => void runPreview()}
              disabled={busy || !project}
            >
              <Play size={14} />{busy ? "运行中…" : "运行预览"}
            </button>
          </div>
        </section>

        {message && !error && <div className="workbench-message research-message">{message}</div>}
        {error && <div className="workbench-message error">{error}</div>}

        <div className="pipeline-workbench-layout">
          <aside className="pipeline-workbench-sidebar">
            <div className="workbench-body">
              <div className="backtest-section-heading">
                <div>
                  <strong>{meta.title}组件库</strong>
                  <span>系统预置和用户添加统一收录；项目固定引用所选版本。</span>
                </div>
                <small>{components.length} 个组件</small>
              </div>

              <div className="pipeline-project-info">
                <strong>{project?.name ?? "加载中…"}</strong>
                <span>{project?.description || "六个组件版本共同组成完整策略。"}</span>
              </div>

              {components.length > 0 ? (
                <div className="editor-list pipeline-component-list">
                  {components.map((item) => (
                    <button
                      key={item.id}
                      type="button"
                      className={component?.id === item.id ? "active" : ""}
                      onClick={() => void applyComponent(item.id)}
                      disabled={busy}
                    >
                      <strong>{item.name}</strong>
                      <span>{item.id}</span>
                      <small>
                        {item.built_in ? "系统预置" : "用户添加"} · v{item.latest_version} · {item.version_count} 个版本
                      </small>
                    </button>
                  ))}
                </div>
              ) : (
                <div className="workbench-message">组件库暂无内容</div>
              )}

              <div className="backtest-run-controls pipeline-inline-form">
                <label>
                  <span>新组件 ID</span>
                  <input
                    value={componentTargetId}
                    onChange={(event) => setComponentTargetId(event.target.value)}
                  />
                </label>
                <button
                  className="secondary-command"
                  type="button"
                  onClick={() => void addComponent()}
                  disabled={busy || !component}
                >
                  <Plus size={14} />添加新组件
                </button>
              </div>

              <div className="backtest-run-controls pipeline-inline-form">
                <label>
                  <span>新项目 ID</span>
                  <input value={targetId} onChange={(event) => setTargetId(event.target.value)} />
                </label>
                <button
                  className="secondary-command"
                  type="button"
                  onClick={() => void cloneProject()}
                  disabled={busy || !project}
                >
                  <GitCommitHorizontal size={14} />以当前配置新建
                </button>
              </div>
            </div>
          </aside>

          <main className="pipeline-workbench-main">
            <div className="backtest-run-controls pipeline-component-bar">
              <div>
                <span>当前组件</span>
                <strong>{component?.name ?? "—"}</strong>
                <small>{component?.id}@{component?.version} · {component?.source_sha256.slice(0, 12)}</small>
              </div>
              <label>
                <span>版本</span>
                <select
                  value={component?.version ?? 1}
                  onChange={(event) => component && void applyComponent(
                    component.id,
                    Number(event.target.value),
                  )}
                >
                  {(component?.versions ?? []).map((version) => (
                    <option key={version} value={version}>v{version}</option>
                  ))}
                </select>
              </label>
              <button
                className="primary-command"
                type="button"
                onClick={() => void saveVersion()}
                disabled={busy || !component?.editable}
                title={component?.editable ? "保存为不可变新版本" : "系统预置组件需先添加为新组件"}
              >
                <Save size={14} />保存新版本
              </button>
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
                  <div className="backtest-section-heading">
                    <div>
                      <strong>Python 源码</strong>
                      <span>唯一逻辑来源 · 入口必须是 {meta.verb}(context)</span>
                    </div>
                  </div>
                  <textarea
                    className="code-view code-editor pipeline-code-editor"
                    spellCheck={false}
                    value={source}
                    readOnly={!component?.editable}
                    onChange={(event) => setSource(event.target.value)}
                  />
                  {!component?.editable && (
                    <div className="workbench-message">
                      这是组件库的系统预置版本。请在左侧输入新 ID，以当前组件为起点添加可编辑组件。
                    </div>
                  )}
                </section>
              )}

              {activeTab === "parameters" && (
                <section>
                  <div className="backtest-section-heading">
                    <div>
                      <strong>组件参数 JSON</strong>
                      <span>参数随组件版本冻结；策略逻辑仍只存在于 Python 函数中。</span>
                    </div>
                  </div>
                  <textarea
                    className="code-view code-editor pipeline-code-editor"
                    spellCheck={false}
                    value={parameters}
                    readOnly={!component?.editable}
                    onChange={(event) => setParameters(event.target.value)}
                  />
                </section>
              )}

              {activeTab === "settings" && supportsSettings && (
                <section>
                  <div className="backtest-section-heading">
                    <div>
                      <strong>项目数据与信号设置</strong>
                      <span>基础池、点时过滤和横截面信号定义属于整个项目。</span>
                    </div>
                  </div>
                  <textarea
                    className="code-view code-editor pipeline-code-editor"
                    spellCheck={false}
                    value={settings}
                    readOnly={!project?.editable}
                    onChange={(event) => setSettings(event.target.value)}
                  />
                  <button
                    className="primary-command pipeline-settings-save"
                    type="button"
                    onClick={() => void saveSettings()}
                    disabled={busy || !project?.editable}
                  >
                    <Check size={14} />保存项目设置
                  </button>
                </section>
              )}

              {activeTab === "preview" && (
                <section>
                  <div className="backtest-section-heading">
                    <div>
                      <strong>真实阶段预览</strong>
                      <span>运行完整冻结源码，再展示本阶段实际输入与输出。</span>
                    </div>
                    <button
                      className="secondary-command"
                      type="button"
                      onClick={() => void runPreview()}
                      disabled={busy || !project}
                    >
                      <Play size={14} />{preview ? "重新运行" : "运行完整策略"}
                    </button>
                  </div>
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
                      {preview.signal_date} · {preview.source_sha256.slice(0, 12)} ·
                      {Object.keys(preview.targets).length} 个最终持仓
                    </div>
                  )}
                </section>
              )}
            </div>
          </main>
        </div>
      </div>
    </Widget>
  )
}

export const UniverseWorkbenchWidget = () => <StageWorkbench stage="universe" />
export const SelectionWorkbenchWidget = () => <StageWorkbench stage="selection" />
export const TimingWorkbenchWidget = () => <StageWorkbench stage="timing" />
export const PortfolioWorkbenchWidget = () => <StageWorkbench stage="portfolio" />
export const RiskWorkbenchWidget = () => <StageWorkbench stage="risk" />
export const ExecutionWorkbenchWidget = () => <StageWorkbench stage="execution" />
