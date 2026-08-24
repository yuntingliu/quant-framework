import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent } from "react"
import { useQueryClient } from "@tanstack/react-query"
import {
  Bot,
  CheckCircle2,
  CircleX,
  LoaderCircle,
  MessageSquarePlus,
  Send,
  Square,
  Wrench,
} from "lucide-react"

import { SafeMarkdown, SafeMarkdownFrame } from "@/components/shared/SafeMarkdown"
import { useAgentPrompt, type AgentDecisionNotebook } from "@/contexts/AgentPromptContext"
import { useGlobalFilter } from "@/contexts/GlobalFilterContext"
import { useLanguage } from "@/contexts/LanguageContext"
import { useWorkspace } from "@/contexts/WorkspaceContext"
import { usePublishedAgent } from "@/hooks/usePublishedAgent"
import type {
  AgentToolActivity,
  PublishedHarnessArtifact,
} from "@/lib/conexus/types"
import { useDataProfile } from "@/lib/data-profile"
import {
  WORKSPACE_COMMAND_EVENT,
  parseAgentWorkspaceCommandBatch,
  type AgentWorkspaceCommandEventDetail,
  type AgentWorkspaceCommandReceipt,
} from "@/workspace/agentCommands"
import { parseAgentResearchResult, type AgentResearchResult } from "@/workspace/researchResults"
import { agentWorkspaceWidgetIds } from "@/widgets/registry/catalog"

const DECISION_NOTEBOOK_NODE_ID = "alphalab-decision-notebook-v1"
const WORKSPACE_COMMANDS_NODE_ID = "alphalab-workspace-commands-v1"
const WORKSPACE_RESULT_NODE_ID = "alphalab-workspace-result-v1"
const WORKSPACE_DOCUMENT_NODE_ID = "alphalab-research-document-v1"

function customNodePayload(value: unknown, expectedType: string): unknown {
  if (!value || typeof value !== "object" || Array.isArray(value)) return value
  const record = value as Record<string, unknown>
  const nested = record.data
  return record.customType === expectedType && nested && typeof nested === "object" && !Array.isArray(nested)
    ? nested
    : value
}

const WORKSPACE_CAPABILITIES = {
  modes: ["data", "factor", "project", "selection", "portfolio", "execution", "backtest", "report"],
  widgets: agentWorkspaceWidgetIds,
  commandTypes: [
    "switch_mode",
    "open_widget",
    "open_result",
    "close_widget",
    "set_focus",
    "set_link_symbol",
    "show_right_rail",
    "refresh_data",
    "save_layout",
    "reset_layout",
  ],
} as const

function decisionNotebookFromArtifacts(artifacts: PublishedHarnessArtifact[]): AgentDecisionNotebook | null {
  for (const artifact of [...artifacts].reverse()) {
    const rawValue = artifact.kind === "json" && artifact.outputKey === "decisionNotebook"
      ? artifact.content.value
      : artifact.kind === "node" && artifact.producerNodeId === DECISION_NOTEBOOK_NODE_ID
        ? artifact.content.node.values.data
        : null
    const value = customNodePayload(rawValue, "alphalab_decision_notebook")
    if (!value || typeof value !== "object" || Array.isArray(value)) continue
    const notebook = value as Record<string, unknown>
    if (
      typeof notebook.classification !== "string"
      || typeof notebook.baseCase !== "string"
      || typeof notebook.riskCase !== "string"
      || typeof notebook.nextAction !== "string"
      || !Array.isArray(notebook.candidateExpressions)
      || notebook.candidateExpressions.some((item) => typeof item !== "string")
    ) continue
    return {
      classification: notebook.classification,
      baseCase: notebook.baseCase,
      riskCase: notebook.riskCase,
      nextAction: notebook.nextAction,
      candidateExpressions: notebook.candidateExpressions as string[],
      runId: artifact.runId,
      updatedAt: artifact.createdAt,
    }
  }
  return null
}

function workspaceCommandsFromArtifact(artifact: PublishedHarnessArtifact): unknown {
  if (artifact.kind === "json" && artifact.outputKey === "workspaceCommands") {
    return customNodePayload(artifact.content.value, "alphalab_workspace_commands")
  }
  if (artifact.kind === "node" && artifact.producerNodeId === WORKSPACE_COMMANDS_NODE_ID) {
    return customNodePayload(artifact.content.node.values.data, "alphalab_workspace_commands")
  }
  return null
}

function workspaceResultValueFromArtifact(artifact: PublishedHarnessArtifact): unknown {
  const value = artifact.kind === "json" && artifact.outputKey === "workspaceResult"
    ? artifact.content.value
    : artifact.kind === "node" && artifact.producerNodeId === WORKSPACE_RESULT_NODE_ID
      ? artifact.content.node.values.data
      : undefined
  return customNodePayload(value, "alphalab_workspace_result")
}

function workspaceDocumentFromArtifact(artifact: PublishedHarnessArtifact): string | null {
  if (artifact.kind !== "document") return null
  return artifact.outputKey === "workspaceDocument" || artifact.producerNodeId === WORKSPACE_DOCUMENT_NODE_ID
    ? artifact.content.markdown
    : null
}

const TOOL_LABELS: Record<string, { zh: string; en: string }> = {
  observe_nodes: { zh: "读取工作台上下文", en: "Read workspace context" },
  update_nodes: { zh: "更新决策笔记", en: "Update decision notebook" },
  commit_harness_outputs: { zh: "原子提交研究结果", en: "Commit research outputs atomically" },
  list_nodes: { zh: "检查工作台节点", en: "Inspect workspace nodes" },
  alphalab_get_workspace_context: { zh: "读取项目与数据目录", en: "Read project and data catalog" },
  alphalab_get_pipeline_project: { zh: "读取三阶段项目", en: "Read three-stage project" },
  alphalab_manage_pipeline: { zh: "管理组件与项目", en: "Manage components and projects" },
  alphalab_preview_pipeline: { zh: "运行阶段预览", en: "Preview pipeline stage" },
  alphalab_get_market_bars: { zh: "读取历史行情", en: "Read historical bars" },
  alphalab_get_fundamentals: { zh: "读取点时基本面", en: "Read point-in-time fundamentals" },
  alphalab_get_factor_returns: { zh: "读取因子收益", en: "Read factor returns" },
  alphalab_evaluate_factor: { zh: "评估因子", en: "Evaluate factor" },
  alphalab_get_backtest: { zh: "读取回测结果", en: "Read backtest result" },
  alphalab_run_backtest: { zh: "运行回测", en: "Run backtest" },
  alphalab_get_reports: { zh: "读取报告库", en: "Read report library" },
  alphalab_save_report: { zh: "保存研究报告", en: "Save research report" },
  alphalab_data_catalog: { zh: "读取数据目录", en: "Read data catalog" },
  alphalab_data_status: { zh: "检查数据状态", en: "Check data status" },
  alphalab_data_plan_sync: { zh: "规划数据同步", en: "Plan data sync" },
  alphalab_data_run_sync: { zh: "执行数据同步", en: "Run data sync" },
  alphalab_data_validate: { zh: "校验数据质量", en: "Validate data quality" },
  alphalab_data_query: { zh: "查询运行时数据", en: "Query runtime data" },
}

function ToolActivityList({
  activities,
  language,
  title,
}: {
  activities: AgentToolActivity[]
  language: "zh" | "en"
  title: string
}) {
  if (activities.length === 0) return null
  return (
    <div className="space-y-1">
      <div className="flex items-center gap-1 font-medium text-foreground"><Wrench className="h-3 w-3" />{title}</div>
      {activities.slice(-6).map((activity) => (
        <div key={activity.callId} className="flex items-start gap-1.5">
          {activity.state === "running"
            ? <LoaderCircle className="mt-0.5 h-3 w-3 shrink-0 animate-spin" />
            : activity.success === false
              ? <CircleX className="mt-0.5 h-3 w-3 shrink-0 text-rose-500" />
              : <CheckCircle2 className="mt-0.5 h-3 w-3 shrink-0 text-emerald-500" />}
          <span>{TOOL_LABELS[activity.name]?.[language] ?? activity.name}{activity.message ? `：${activity.message}` : ""}</span>
        </div>
      ))}
    </div>
  )
}

function Artifact({ artifact }: { artifact: PublishedHarnessArtifact }) {
  const { language } = useLanguage()
  const workspaceDocument = workspaceDocumentFromArtifact(artifact)
  if (workspaceDocument !== null) {
    if (!workspaceDocument.trim()) return null
    return (
      <details open className="rounded border border-border bg-background text-xs">
        <summary className="cursor-pointer border-b border-border px-3 py-2 font-medium text-foreground">
          {artifact.title || (language === "zh" ? "研究报告" : "Research report")}
        </summary>
        <div className="max-h-[32rem] overflow-auto p-3">
          <SafeMarkdown>{workspaceDocument}</SafeMarkdown>
        </div>
      </details>
    )
  }
  if (
    artifact.outputKey === "workspaceCommands"
    || artifact.producerNodeId === WORKSPACE_COMMANDS_NODE_ID
    || artifact.outputKey === "workspaceResult"
    || artifact.producerNodeId === WORKSPACE_RESULT_NODE_ID
    || artifact.outputKey === "workspaceDocument"
    || artifact.producerNodeId === WORKSPACE_DOCUMENT_NODE_ID
    || artifact.outputKey === "decisionNotebook"
    || artifact.producerNodeId === DECISION_NOTEBOOK_NODE_ID
  ) return null
  if (artifact.kind === "document") {
    return <SafeMarkdownFrame><SafeMarkdown>{artifact.content.markdown}</SafeMarkdown></SafeMarkdownFrame>
  }
  if (artifact.kind === "image") {
    return <img className="max-h-80 rounded border border-border" src={artifact.content.src} alt={artifact.content.caption ?? artifact.title} />
  }
  if (artifact.kind === "file") {
    return artifact.content.url
      ? <a className="block rounded border border-border bg-background px-3 py-2 text-xs text-primary" href={artifact.content.url} target="_blank" rel="noreferrer">{artifact.content.name}</a>
      : <div className="break-all rounded border border-border bg-background px-3 py-2 font-mono text-xs text-muted-foreground">{artifact.content.path ?? artifact.content.name}</div>
  }
  const value = artifact.kind === "json" ? artifact.content.value : artifact.content.node.values
  return (
    <details className="rounded border border-border bg-background p-2 text-xs">
      <summary className="cursor-pointer font-medium text-foreground">{artifact.title}</summary>
      <pre className="mt-2 max-h-64 overflow-auto whitespace-pre-wrap text-[11px] text-muted-foreground">{JSON.stringify(value, null, 2)}</pre>
    </details>
  )
}

export function ResearchAgentPanel() {
  const { language } = useLanguage()
  const workspace = useWorkspace()
  const { selectedFactors, startDate, endDate } = useGlobalFilter()
  const [activeDataProfile] = useDataProfile()
  const queryClient = useQueryClient()
  const { stagedPrompt, consumePrompt, setDecisionNotebook, registerResearchResult, refreshResearchResults } = useAgentPrompt()
  const [draft, setDraft] = useState("")
  const [promptContext, setPromptContext] = useState<Record<string, unknown>>({})
  const [workspaceReceipts, setWorkspaceReceipts] = useState<AgentWorkspaceCommandReceipt[]>([])
  const messageEndRef = useRef<HTMLDivElement>(null)
  const pendingWorkspaceRequestIdsRef = useRef(new Set<string>())
  const processedWorkspaceArtifactIdsRef = useRef(new Set<string>())

  const onCompleted = useCallback(() => {
    void queryClient.invalidateQueries({ queryKey: ["v2", "backtests"] })
    void queryClient.invalidateQueries({ queryKey: ["backtests"] })
    void queryClient.invalidateQueries({ queryKey: ["strategies"] })
    void queryClient.invalidateQueries({ queryKey: ["data-center"] })
    void refreshResearchResults().catch(() => undefined)
  }, [queryClient, refreshResearchResults])

  const agent = usePublishedAgent({
    onCompleted,
  })

  useEffect(() => {
    if (!stagedPrompt) return
    setDraft(stagedPrompt.message)
    setPromptContext(stagedPrompt.context)
    consumePrompt(stagedPrompt.id)
  }, [consumePrompt, stagedPrompt])

  const artifacts = useMemo(
    () => agent.conversation?.messages.flatMap((item) => item.artifacts ?? []) ?? [],
    [agent.conversation?.messages],
  )

  useEffect(() => {
    setDecisionNotebook(decisionNotebookFromArtifacts(artifacts))
  }, [artifacts, setDecisionNotebook])

  useEffect(() => {
    for (const artifact of [...artifacts].reverse()) {
      if (processedWorkspaceArtifactIdsRef.current.has(artifact.id)) continue
      const value = workspaceCommandsFromArtifact(artifact)
      if (!value) continue
      const batch = parseAgentWorkspaceCommandBatch(value)
      if (!batch || !pendingWorkspaceRequestIdsRef.current.has(batch.requestId)) continue
      const runArtifacts = [...artifacts].reverse().filter((candidate) => candidate.runId === artifact.runId)
      const descriptorArtifact = runArtifacts.find((candidate) => workspaceResultValueFromArtifact(candidate) !== undefined)
      const documentArtifact = runArtifacts.find((candidate) => workspaceDocumentFromArtifact(candidate) !== null)
      const descriptor = descriptorArtifact ? workspaceResultValueFromArtifact(descriptorArtifact) : undefined
      const markdown = documentArtifact ? workspaceDocumentFromArtifact(documentArtifact) : null
      const parsedResult = descriptorArtifact
        ? parseAgentResearchResult(descriptor, {
            ...(markdown !== null ? { markdown } : {}),
            runId: artifact.runId,
            ...(documentArtifact ? {
              artifactId: documentArtifact.id,
              updatedAt: documentArtifact.createdAt,
            } : {}),
          })
        : null
      const researchResult: AgentResearchResult | undefined = parsedResult?.requestId === batch.requestId ? parsedResult : undefined
      if (researchResult) registerResearchResult(researchResult)
      processedWorkspaceArtifactIdsRef.current.add(artifact.id)
      pendingWorkspaceRequestIdsRef.current.delete(batch.requestId)
      const detail: AgentWorkspaceCommandEventDetail = {
        artifactId: artifact.id,
        runId: artifact.runId,
        batch,
        ...(researchResult ? { researchResult } : {}),
        receipts: [],
      }
      window.dispatchEvent(new CustomEvent(WORKSPACE_COMMAND_EVENT, { detail }))
      void (detail.receiptPromise ?? Promise.resolve(detail.receipts)).then(setWorkspaceReceipts)
      break
    }
  }, [artifacts, registerResearchResult])

  useEffect(() => {
    messageEndRef.current?.scrollIntoView({ behavior: "smooth", block: "end" })
  }, [agent.conversation?.messages.length, agent.responseActive])

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    const message = draft.trim()
    if (!message) return
    const requestId = `${Date.now()}-${Math.random().toString(36).slice(2)}`
    const context: Record<string, unknown> = {
      ...promptContext,
      requestId,
      source: promptContext.source ?? "agent-rail",
      activeMode: workspace.activeMode,
      activeDataProfile,
      selectedDataset: workspace.selectedDataset,
      selectedFactors,
      factorDateRange: { start: startDate || null, end: endDate || null },
      selectedSymbol: workspace.selectedSymbol,
      linkSymbols: workspace.linkSymbols,
      selectedStrategy: workspace.selectedStrategy,
      selectedBacktest: workspace.selectedBacktest,
      selectedDate: workspace.selectedDate,
      workspaceCapabilities: WORKSPACE_CAPABILITIES,
      lastWorkspaceCommandReceipts: workspaceReceipts,
    }
    pendingWorkspaceRequestIdsRef.current.add(requestId)
    if (await agent.send(message, context)) {
      setDraft("")
      setWorkspaceReceipts([])
      if (!agent.run?.pendingInteraction) setPromptContext({})
    } else {
      pendingWorkspaceRequestIdsRef.current.delete(requestId)
    }
  }

  const copy = language === "zh" ? {
    title: "AI 研究代理",
    newChat: "新会话",
    empty: "发送研究问题，Agent 会调用已发布的 AlphaLab Research Harness。",
    placeholder: agent.run?.pendingInteraction ? "回答 Agent 的问题…" : "输入研究问题…",
    running: "Agent 正在运行…",
    unavailable: "Conexus Research Agent 尚未运行或尚未发布。",
    notConfigured: "状态：not_configured",
    retry: "重试",
    tools: "工具执行",
    workspaceActions: "工作台联动",
    conversation: "Agent 会话",
    stop: "停止",
    send: "发送",
    quickPrompts: ["现在有哪些策略？", "对比当前策略并把研究报告放到中间工作区", "打开数据工作台并刷新数据", "打开动量策略的回测工作台"],
  } : {
    title: "AI Research Agent",
    newChat: "New chat",
    empty: "Send a research request to the published AlphaLab Research Harness.",
    placeholder: agent.run?.pendingInteraction ? "Answer the Agent…" : "Enter a research request…",
    running: "Agent is running…",
    unavailable: "The Conexus Research Agent is not running or has not been published.",
    notConfigured: "Status: not_configured",
    retry: "Retry",
    tools: "Tool activity",
    workspaceActions: "Workspace actions",
    conversation: "Agent conversation",
    stop: "Stop",
    send: "Send",
    quickPrompts: ["What strategies are available?", "Compare current strategies in a workspace report", "Open Data Workbench and refresh data", "Open the momentum backtest workbench"],
  }

  if (!agent.loading && agent.status && !agent.status.available) {
    return (
      <div className="flex h-full items-center justify-center p-3">
        <div className="space-y-2 rounded border border-rose-500/30 bg-rose-500/5 p-3 text-xs text-rose-700 dark:text-rose-300">
          <div>{copy.unavailable}</div>
          <div className="font-mono text-[10px] opacity-80">{copy.notConfigured}</div>
          <button className="rounded border border-current px-2 py-1" onClick={() => void agent.reloadStatus()}>{copy.retry}</button>
        </div>
      </div>
    )
  }

  return (
    <div className="flex h-full min-h-0 flex-col bg-card">
      <div className="flex shrink-0 items-center gap-1 border-b border-border p-2">
          <select
            className="min-w-0 flex-1 rounded border border-border bg-background px-2 py-1 text-xs text-foreground"
            value={agent.conversation?.id ?? ""}
            disabled={agent.responseActive}
            onChange={(event) => { if (event.target.value) void agent.selectConversation(event.target.value) }}
            aria-label={copy.conversation}
          >
            <option value="">{copy.newChat}</option>
            {agent.conversations.map((item) => <option key={item.id} value={item.id}>{item.title}</option>)}
          </select>
          <button className="rounded p-1.5 text-muted-foreground hover:bg-muted" disabled={agent.responseActive} onClick={agent.newConversation} title={copy.newChat}>
            <MessageSquarePlus className="h-4 w-4" />
          </button>
      </div>
      {agent.error ? (
        <button className="shrink-0 border-b border-rose-500/20 bg-rose-500/5 px-3 py-2 text-left text-xs text-rose-700 dark:text-rose-300" onClick={() => void agent.reloadStatus()}>
          {agent.error}
        </button>
      ) : null}
      {agent.loading && !agent.conversation ? (
        <div className="flex min-h-0 flex-1 items-center justify-center gap-2 text-xs text-muted-foreground"><LoaderCircle className="h-4 w-4 animate-spin" />{copy.running}</div>
      ) : (
      <div className="flex h-full min-h-0 flex-col">
        <div className="min-h-0 flex-1 overflow-auto p-4">
          {agent.conversation?.messages.length ? (
            <div className="space-y-4">
              {agent.conversation.messages.map((item) => (
                <div key={item.id} className={item.role === "user" ? "flex justify-end" : "flex justify-start"}>
                  <div className="max-w-[88%] space-y-2">
                    <div className={item.role === "user"
                      ? "rounded-xl bg-primary px-3 py-2 text-sm leading-6 text-primary-foreground"
                      : "rounded-xl border border-border bg-muted/30 px-3 py-2 text-sm leading-6 text-foreground"}
                    >
                      {item.role === "assistant" && !item.error
                        ? <SafeMarkdown className="text-current">{item.content}</SafeMarkdown>
                        : <div className="whitespace-pre-wrap">{item.content}</div>}
                    </div>
                    {item.artifacts?.map((artifact) => <Artifact key={artifact.id} artifact={artifact} />)}
                  </div>
                </div>
              ))}
              {agent.responseActive ? (
                <div className="flex justify-start">
                  <div className="min-w-64 rounded-xl border border-border bg-muted/30 px-3 py-2 text-xs text-muted-foreground">
                    {agent.run?.pendingInteraction ? (
                      <div>
                        <div className="text-foreground">{agent.run.pendingInteraction.question}</div>
                        <div className="mt-2 flex flex-wrap gap-1">
                          {agent.run.pendingInteraction.choices?.map((choice) => (
                            <button key={choice} className="rounded border border-border px-2 py-1" onClick={() => setDraft(choice)}>{choice}</button>
                          ))}
                        </div>
                      </div>
                    ) : (
                      <div className="space-y-2">
                        <span className="flex items-center gap-2"><LoaderCircle className="h-3.5 w-3.5 animate-spin" />{copy.running}</span>
                        {agent.toolActivities.length > 0 ? <div className="border-t border-border pt-2"><ToolActivityList activities={agent.toolActivities} language={language} title={copy.tools} /></div> : null}
                      </div>
                    )}
                  </div>
                </div>
              ) : null}
              {!agent.responseActive && agent.toolActivities.length > 0 ? (
                <details className="rounded border border-border bg-muted/20 px-3 py-2 text-xs text-muted-foreground">
                  <summary className="cursor-pointer text-foreground">{copy.tools} · {agent.toolActivities.length}</summary>
                  <div className="mt-2"><ToolActivityList activities={agent.toolActivities} language={language} title={copy.tools} /></div>
                </details>
              ) : null}
              {!agent.responseActive && workspaceReceipts.length > 0 ? (
                <div className="rounded border border-border bg-muted/20 px-3 py-2 text-xs text-muted-foreground">
                  <div className="mb-1 font-medium text-foreground">{copy.workspaceActions}</div>
                  {workspaceReceipts.map((receipt) => (
                    <div key={`${receipt.index}-${receipt.type}`} className="flex items-start gap-1.5 py-0.5">
                      {receipt.success
                        ? <CheckCircle2 className="mt-0.5 h-3 w-3 shrink-0 text-emerald-500" />
                        : <CircleX className="mt-0.5 h-3 w-3 shrink-0 text-rose-500" />}
                      <span>{receipt.message}</span>
                    </div>
                  ))}
                </div>
              ) : null}
              <div ref={messageEndRef} />
            </div>
          ) : (
            <div className="flex h-full min-h-64 flex-col items-center justify-center gap-3 text-center text-muted-foreground">
              <Bot className="h-8 w-8 text-primary" />
              <p className="max-w-sm text-xs leading-5">{copy.empty}</p>
              <div className="flex flex-wrap justify-center gap-2">
                {copy.quickPrompts.map((prompt) => (
                  <button key={prompt} type="button" className="rounded border border-border bg-background px-2 py-1 text-xs hover:border-primary hover:text-primary" onClick={() => setDraft(prompt)}>{prompt}</button>
                ))}
              </div>
            </div>
          )}
        </div>
        <div className="shrink-0 border-t border-border">
          <form className="flex gap-2 p-3" onSubmit={submit}>
            <textarea
              className="min-h-10 flex-1 resize-none rounded border border-border bg-background px-3 py-2 text-sm text-foreground outline-none focus:border-primary"
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
              placeholder={copy.placeholder}
              disabled={agent.responseActive && !agent.run?.pendingInteraction}
              onKeyDown={(event) => {
                if (event.key === "Enter" && !event.shiftKey) {
                  event.preventDefault()
                  event.currentTarget.form?.requestSubmit()
                }
              }}
            />
            {agent.responseActive && !agent.run?.pendingInteraction ? (
              <button type="button" className="rounded border border-border px-3 text-muted-foreground hover:text-foreground" onClick={() => void agent.cancel()} title={copy.stop}>
                <Square className="h-4 w-4" />
              </button>
            ) : (
              <button type="submit" className="rounded bg-primary px-3 text-primary-foreground disabled:opacity-50" disabled={!draft.trim()} title={copy.send}>
                <Send className="h-4 w-4" />
              </button>
            )}
          </form>
        </div>
      </div>
      )}
    </div>
  )
}
