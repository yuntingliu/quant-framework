import { useCallback, useEffect, useMemo, useState, type PointerEvent as ReactPointerEvent } from "react"
import { useQuery } from "@tanstack/react-query"
import {
  Activity,
  ArrowRight,
  BarChart3,
  Bot,
  ClipboardList,
  Database,
  Gauge,
  Layers,
  PanelRightClose,
  PanelRightOpen,
  PlayCircle,
  Plus,
  Search,
  ShieldCheck,
  SlidersHorizontal,
  Target,
  Wifi,
  WifiOff,
  Workflow,
  X,
  type LucideIcon,
} from "lucide-react"

import { ScoreGauge } from "@/components/shared/ScoreGauge"
import { useAgentPrompt, type AgentDecisionNotebook } from "@/contexts/AgentPromptContext"
import { useLanguage, type TranslationKey } from "@/contexts/LanguageContext"
import { useWorkspace } from "@/contexts/WorkspaceContext"
import type { WorkspaceMode } from "@/layouts/presets"
import { api } from "@/lib/api"
import type { ConexusStatus } from "@/lib/conexus/types"
import { cn } from "@/lib/utils"
import { ResearchAgentPanel } from "@/widgets/research/ResearchAgent"

import { MODE_CONFIG } from "./modes"
import type { RightRailTab, WorkspaceTask } from "./types"

interface TradingStatusSummary {
  connected?: boolean
  mode?: string
  account_id?: string | null
  broker?: string | null
  broker_label?: string | null
  account_currency?: string | null
  supports_real_orders?: boolean
  readonly?: boolean
}

interface TradingAssetSummary {
  cash?: number | null
  total_asset?: number | null
  market_value?: number | null
}

interface DataSourceSummary {
  start?: string | null
  coverage_start?: string | null
  coverage_end?: string | null
  latest_date?: string | null
  latest?: string | null
  expected_latest?: string | null
  needs_update?: boolean
  raw_needs_update?: boolean
  provider_pending?: boolean
  live_connected?: boolean
  live_tick_age_seconds?: number | null
  live_tick_symbol?: string | null
  intraday_mode?: string | null
  stale_days?: number | null
  stock_count?: number | null
  field_count?: number | null
  table_count?: number | null
  phase_label?: string | null
}

interface DataStatusSummary {
  demo?: DataSourceSummary
  runtime?: DataSourceSummary
  rq?: DataSourceSummary
}

interface LatestBacktestSummary {
  id: string
  strategy_id?: string | null
  market?: string | null
  run_at?: string | null
  created_at?: string | null
  annual_return?: number | null
  sharpe?: number | null
  max_drawdown?: number | null
  metrics?: {
    annual_return?: number | null
    sharpe?: number | null
    max_drawdown?: number | null
  } | null
}

type BacktestListSummary = LatestBacktestSummary[]

type RailTone = "ok" | "warn" | "danger" | "muted"

interface RailActivityItem {
  id: string
  time: string
  title: string
  detail: string
  tone: RailTone
  icon: LucideIcon
}

// ---------------------------------------------------------------------------
// Workspace commands
// ---------------------------------------------------------------------------

function dispatchCommandPalette() {
  window.dispatchEvent(new KeyboardEvent("keydown", { key: "k", ctrlKey: true }))
}

// ---------------------------------------------------------------------------
// Context + Agent right rail
// ---------------------------------------------------------------------------

const RIGHT_RAIL_TABS: Array<{ id: RightRailTab; icon: LucideIcon; labelKey: TranslationKey }> = [
  { id: "context", icon: Target, labelKey: "rightRail.context" },
  { id: "agent", icon: Bot, labelKey: "rightRail.agent" },
  { id: "activity", icon: Activity, labelKey: "rightRail.activity" },
]

const RIGHT_RAIL_MIN_WIDTH = 320
const RIGHT_RAIL_MAX_WIDTH = 720

const RIGHT_RAIL_COPY = {
  zh: {
    score: "就绪度",
    scoreReady: "工作台可推进",
    scoreReview: "需要复核",
    scoreBlocked: "缺少关键上下文",
    noIdea: "暂无研究上下文",
    draftReady: "策略草案就绪",
    backtestReady: "回测证据就绪",
    reviewNeeded: "需要复核",
    live: "实盘能力",
    paper: "纸面优先",
    contextStack: "焦点栈",
    dataState: "数据/连接",
    nextActions: "建议动作",
    recentBacktests: "最近回测",
    noRecentBacktests: "暂无最近回测",
    selectBacktest: "载入回测",
    inspect: "检查",
    openData: "数据中心",
    openStrategy: "策略配置",
    openWorkbench: "回测工作台",
    agentReady: "LLM 就绪",
    agentFallback: "本地/回退",
    activityTrail: "操作轨迹",
    gateBoard: "门禁板",
    noActivity: "暂无操作，先从 Context 或 Agent 里推进一步。",
    dataOk: "数据正常",
    dataWarn: "数据需更新",
    connectionOk: "连接在线",
    connectionOff: "连接未开",
    contextWeak: "上下文不足",
    contextOk: "上下文完整",
    commandOpened: "打开命令面板",
    widgetOpened: "打开组件",
    backtestSelected: "已载入回测",
    tabChanged: "切换右栏",
    latest: "最近",
    latestNotebook: "最近决策笔记",
    noNotebook: "运行一次 Agent 后，这里会显示最近的决策笔记摘要。",
    classification: "判断",
    baseCase: "基础情景",
    riskCase: "风险情景",
    nextAction: "下一步",
    candidates: "候选表达式",
    resizeRail: "拖动调整右栏宽度",
  },
  en: {
    score: "Readiness",
    scoreReady: "Workspace ready",
    scoreReview: "Needs review",
    scoreBlocked: "Missing context",
    noIdea: "No idea",
    draftReady: "Draft ready",
    backtestReady: "Backtest ready",
    reviewNeeded: "Review needed",
    live: "Live capable",
    paper: "Paper first",
    contextStack: "Focus stack",
    dataState: "Data/connection",
    nextActions: "Suggested actions",
    recentBacktests: "Recent backtests",
    noRecentBacktests: "No recent backtests",
    selectBacktest: "Load backtest",
    inspect: "Inspect",
    openData: "Data Center",
    openStrategy: "Strategies",
    openWorkbench: "Workbench",
    agentReady: "LLM ready",
    agentFallback: "Local/fallback",
    activityTrail: "Activity trail",
    gateBoard: "Gate board",
    noActivity: "No actions yet. Advance from Context or Agent.",
    dataOk: "Data ready",
    dataWarn: "Data update needed",
    connectionOk: "Connection online",
    connectionOff: "Connection off",
    contextWeak: "Thin context",
    contextOk: "Context complete",
    commandOpened: "Opened command palette",
    widgetOpened: "Opened widget",
    backtestSelected: "Loaded backtest",
    tabChanged: "Switched rail tab",
    latest: "Latest",
    latestNotebook: "Latest decision notebook",
    noNotebook: "Run the Agent once and the latest decision notebook summary appears here.",
    classification: "Classification",
    baseCase: "Base case",
    riskCase: "Risk case",
    nextAction: "Next action",
    candidates: "Candidate expressions",
    resizeRail: "Drag to resize the right rail",
  },
} as const

function formatCompactNumber(value?: number | null) {
  if (value === null || value === undefined || Number.isNaN(value)) return "--"
  return new Intl.NumberFormat(undefined, { notation: "compact", maximumFractionDigits: 1 }).format(value)
}

function formatDateLabel(value?: string | null) {
  if (!value) return "--"
  return value.length > 10 ? value.slice(0, 10) : value
}

function RailIconButton({
  active,
  tone,
  icon: Icon,
  label,
  onClick,
}: {
  active?: boolean
  tone?: RailTone
  icon: LucideIcon
  label: string
  onClick: () => void
}) {
  return (
    <button
      className={cn(
        "relative flex h-10 w-10 items-center justify-center rounded text-muted-foreground transition-colors hover:bg-muted/40 hover:text-foreground",
        active && "bg-primary/10 text-primary ring-1 ring-primary/20",
      )}
      onClick={onClick}
      title={label}
    >
      <Icon className="h-4 w-4" />
      {active && <span className="absolute right-0 top-2 bottom-2 w-0.5 rounded-l bg-primary" />}
      {tone && tone !== "muted" && (
        <span
          className={cn(
            "absolute right-1 top-1 h-1.5 w-1.5 rounded-full",
            tone === "ok" && "bg-emerald-500",
            tone === "warn" && "bg-amber-500",
            tone === "danger" && "bg-rose-500",
          )}
        />
      )}
    </button>
  )
}

function RailStatusDot({ tone = "muted" }: { tone?: RailTone }) {
  return (
    <span
      className={cn(
        "h-2 w-2 shrink-0 rounded-full",
        tone === "ok" && "bg-emerald-500",
        tone === "warn" && "bg-amber-500",
        tone === "danger" && "bg-rose-500",
        tone === "muted" && "bg-muted-foreground/40",
      )}
    />
  )
}

function RailMetric({
  label,
  value,
  detail,
  tone = "muted",
}: {
  label: string
  value: string
  detail?: string
  tone?: RailTone
}) {
  return (
    <div className="min-w-0 rounded border border-border bg-background px-2 py-1.5">
      <div className="flex min-w-0 items-center gap-1.5 text-[10px] text-muted-foreground">
        <RailStatusDot tone={tone} />
        <span className="truncate">{label}</span>
      </div>
      <div className="mt-1 truncate text-xs font-semibold text-foreground">{value}</div>
      {detail ? <div className="mt-0.5 truncate text-[10px] text-muted-foreground">{detail}</div> : null}
    </div>
  )
}

function dataSourceTone(source?: DataSourceSummary): RailTone {
  if (!source) return "muted"
  if (source.provider_pending) return "warn"
  if (source.intraday_mode === "live_ticks") return "ok"
  return source.needs_update ? "warn" : "ok"
}

function DataSourceCard({
  label,
  source,
}: {
  label: string
  source?: DataSourceSummary
}) {
  const { t } = useLanguage()
  const tone = dataSourceTone(source)
  const status = !source
    ? t("rightRail.unknown")
    : source.provider_pending
      ? t("data.providerPending")
      : source.intraday_mode === "live_ticks"
        ? t("data.liveTicks")
        : source.needs_update
          ? t("rightRail.needsUpdate")
          : t("rightRail.ready")
  const detail = source?.intraday_mode === "live_ticks"
    ? [
        source.live_tick_symbol ?? t("data.liveTicks"),
        source.live_tick_age_seconds != null ? `${Math.round(source.live_tick_age_seconds)}s` : null,
      ].filter(Boolean).join(" · ")
    : source?.provider_pending
      ? t("data.providerPending")
      : source?.stale_days
        ? `${t("data.stale")} ${source.stale_days} ${t("data.days")}`
        : formatDateLabel(source?.coverage_end ?? source?.latest_date ?? source?.latest)
  const start = formatDateLabel(source?.coverage_start ?? source?.start)

  return (
    <div className="rounded border border-border bg-background p-2">
      <div className="flex min-w-0 items-center gap-2">
        <RailStatusDot tone={tone} />
        <span className="truncate text-xs font-semibold text-foreground">{label}</span>
        <span className="ml-auto shrink-0 text-[10px] text-muted-foreground">{status}</span>
      </div>
      <div className="mt-2 grid grid-cols-2 gap-1.5 text-[10px] text-muted-foreground">
        <div className="min-w-0">
          <span className="block truncate">{t("data.start")}</span>
          <span className="block truncate text-foreground">{start}</span>
        </div>
        <div className="min-w-0">
          <span className="block truncate">{t("data.latest")}</span>
          <span className="block truncate text-foreground">{detail}</span>
        </div>
      </div>
    </div>
  )
}

function rightRailCopy(language: "zh" | "en") {
  return RIGHT_RAIL_COPY[language]
}

function formatClock(date = new Date()) {
  return new Intl.DateTimeFormat(undefined, { hour: "2-digit", minute: "2-digit" }).format(date)
}

function percentLabel(value?: number | null) {
  if (value === null || value === undefined || Number.isNaN(value)) return "--"
  return `${(value * 100).toFixed(1)}%`
}

function metricValue(item: LatestBacktestSummary, key: "annual_return" | "sharpe" | "max_drawdown") {
  return item.metrics?.[key] ?? item[key] ?? null
}

function buildRailScore({
  activeMode,
  selectedSymbol,
  selectedStrategy,
  selectedBacktest,
  tradingStatus,
  dataStatus,
  agentConfig,
}: {
  activeMode: WorkspaceMode
  selectedSymbol: string | null
  selectedStrategy: string | null
  selectedBacktest: string | null
  tradingStatus?: TradingStatusSummary
  dataStatus?: DataStatusSummary
  agentConfig?: ConexusStatus
}) {
  const dataReady = Boolean(dataStatus?.demo && !dataStatus.demo.needs_update)
  const connectionReady = tradingStatus?.connected === true || activeMode === "home" || activeMode === "research" || activeMode === "data"
  const contextReady = Boolean(selectedSymbol || selectedStrategy || selectedBacktest)
  const researchReady = Boolean(selectedStrategy || selectedBacktest)
  const agentReady = agentConfig?.available === true
  const paperSafe = !tradingStatus?.supports_real_orders
  let score = 20
  if (dataReady) score += 25
  if (connectionReady) score += 15
  if (contextReady) score += 15
  if (researchReady) score += 10
  if (agentReady) score += 10
  if (paperSafe) score += 10
  return Math.min(100, score)
}

function scoreTone(score: number): RailTone {
  if (score >= 82) return "ok"
  if (score >= 62) return "warn"
  return "danger"
}

function scoreLabel(score: number, copy: ReturnType<typeof rightRailCopy>) {
  if (score >= 82) return copy.scoreReady
  if (score >= 62) return copy.scoreReview
  return copy.scoreBlocked
}

function taskStatusLabel({
  selectedStrategy,
  selectedBacktest,
  dataStatus,
  copy,
}: {
  selectedStrategy: string | null
  selectedBacktest: string | null
  dataStatus?: DataStatusSummary
  copy: ReturnType<typeof rightRailCopy>
}) {
  if (dataStatus?.runtime?.needs_update) return copy.reviewNeeded
  if (selectedBacktest) return copy.backtestReady
  if (selectedStrategy) return copy.draftReady
  return copy.noIdea
}

function actionToneClasses(tone: RailTone) {
  if (tone === "ok") return "border-emerald-500/30 bg-emerald-500/10 text-emerald-700 hover:bg-emerald-500/15 dark:text-emerald-300"
  if (tone === "warn") return "border-amber-500/30 bg-amber-500/10 text-amber-700 hover:bg-amber-500/15 dark:text-amber-300"
  if (tone === "danger") return "border-rose-500/30 bg-rose-500/10 text-rose-700 hover:bg-rose-500/15 dark:text-rose-300"
  return "border-border bg-background text-muted-foreground hover:border-primary/40 hover:text-foreground"
}

function RailActionButton({
  icon: Icon,
  label,
  detail,
  tone = "muted",
  onClick,
}: {
  icon: LucideIcon
  label: string
  detail?: string
  tone?: RailTone
  onClick: () => void
}) {
  return (
    <button
      className={cn(
        "flex min-h-10 w-full min-w-0 items-center gap-2 rounded border px-2 py-1.5 text-left text-xs transition-colors",
        actionToneClasses(tone),
      )}
      onClick={onClick}
      title={detail ?? label}
    >
      <Icon className="h-4 w-4 shrink-0" />
      <span className="min-w-0 flex-1">
        <span className="block truncate font-semibold">{label}</span>
        {detail ? <span className="block truncate text-[10px] opacity-80">{detail}</span> : null}
      </span>
      <ArrowRight className="h-3.5 w-3.5 shrink-0 opacity-70" />
    </button>
  )
}

function FocusRow({
  icon: Icon,
  label,
  value,
  actionLabel,
  onAction,
}: {
  icon: LucideIcon
  label: string
  value: string
  actionLabel: string
  onAction: () => void
}) {
  return (
    <div className="rounded border border-border bg-background p-2">
      <div className="flex min-w-0 items-center gap-2">
        <Icon className="h-4 w-4 shrink-0 text-primary" />
        <div className="min-w-0 flex-1">
          <div className="truncate text-[10px] text-muted-foreground">{label}</div>
          <div className="truncate text-xs font-semibold text-foreground">{value}</div>
        </div>
        <button
          className="shrink-0 rounded border border-border px-1.5 py-1 text-[10px] text-muted-foreground transition-colors hover:border-primary/40 hover:text-foreground"
          onClick={onAction}
          title={actionLabel}
        >
          {actionLabel}
        </button>
      </div>
    </div>
  )
}

function RailNotebookLine({ label, value }: { label: string; value?: string }) {
  if (!value) return null
  return (
    <div className="grid grid-cols-[72px_minmax(0,1fr)] gap-2 text-[10px] leading-4">
      <span className="text-muted-foreground">{label}</span>
      <span className="break-words text-foreground">{value}</span>
    </div>
  )
}

function DecisionNotebookCard({ notebook }: { notebook: AgentDecisionNotebook | null }) {
  const { language } = useLanguage()
  const copy = rightRailCopy(language)
  const updatedAt = notebook?.updatedAt ? new Date(notebook.updatedAt) : null
  const updatedLabel = updatedAt && !Number.isNaN(updatedAt.getTime())
    ? updatedAt.toLocaleString(language === "zh" ? "zh-CN" : "en-US", { dateStyle: "short", timeStyle: "short" })
    : null

  return (
    <section className="space-y-2">
      <div className="flex min-w-0 items-center gap-2 text-xs font-semibold text-foreground">
        <ClipboardList className="h-4 w-4 shrink-0 text-primary" />
        <span className="min-w-0 flex-1 truncate">{copy.latestNotebook}</span>
        {updatedLabel ? <span className="shrink-0 text-[9px] font-normal text-muted-foreground">{updatedLabel}</span> : null}
      </div>
      <div className="rounded border border-border bg-background p-2.5">
        {!notebook ? (
          <p className="text-[10px] leading-4 text-muted-foreground">{copy.noNotebook}</p>
        ) : (
          <div className="space-y-2">
            {notebook.classification ? (
              <div className="flex items-center gap-2">
                <span className="text-[10px] text-muted-foreground">{copy.classification}</span>
                <span className="min-w-0 rounded bg-primary/10 px-1.5 py-0.5 text-[10px] font-medium text-primary">
                  {notebook.classification}
                </span>
              </div>
            ) : null}
            <div className="space-y-1.5">
              <RailNotebookLine label={copy.baseCase} value={notebook.baseCase} />
              <RailNotebookLine label={copy.riskCase} value={notebook.riskCase} />
              <RailNotebookLine label={copy.nextAction} value={notebook.nextAction} />
            </div>
            {notebook.candidateExpressions?.length ? (
              <div className="border-t border-border pt-2">
                <div className="mb-1 text-[10px] text-muted-foreground">{copy.candidates}</div>
                <div className="flex flex-wrap gap-1">
                  {notebook.candidateExpressions.map((expression) => (
                    <span key={expression} className="max-w-full break-all rounded bg-muted px-1.5 py-0.5 font-mono text-[9px] text-foreground">
                      {expression}
                    </span>
                  ))}
                </div>
              </div>
            ) : null}
          </div>
        )}
      </div>
    </section>
  )
}

function RightRailContextPanel({
  tradingStatus,
  asset,
  dataStatus,
  decisionNotebook,
  latestBacktests,
  onAddWidget,
  onOpenTask,
  onRecordActivity,
}: {
  tradingStatus?: TradingStatusSummary
  asset?: TradingAssetSummary
  dataStatus?: DataStatusSummary
  decisionNotebook: AgentDecisionNotebook | null
  latestBacktests: LatestBacktestSummary[]
  onAddWidget: (widgetId: string, label: string) => void
  onOpenTask: (task: WorkspaceTask) => void
  onRecordActivity: (title: string, detail: string, tone: RailTone, icon: LucideIcon) => void
}) {
  const workspace = useWorkspace()
  const { language, t } = useLanguage()
  const copy = rightRailCopy(language)
  const modeConfig = MODE_CONFIG[workspace.activeMode]
  const connected = tradingStatus?.connected === true
  const brokerLabel = tradingStatus?.broker_label ?? tradingStatus?.broker ?? t("rightRail.unknown")
  const currency = tradingStatus?.account_currency ? `${tradingStatus.account_currency} ` : ""
  const dataNeedsUpdate = Boolean(dataStatus?.runtime?.needs_update)
  const focusReady = Boolean(workspace.selectedSymbol || workspace.selectedStrategy || workspace.selectedBacktest)

  const selectBacktest = (item: LatestBacktestSummary) => {
    workspace.setSelectedBacktest(item.id)
    if (item.strategy_id) workspace.setSelectedStrategy(item.strategy_id)
    onAddWidget("backtest.workbench", copy.openWorkbench)
    onRecordActivity(copy.backtestSelected, item.strategy_id ? `${item.strategy_id} / ${item.id}` : item.id, "ok", BarChart3)
  }

  return (
    <div className="space-y-3">
      <section className="space-y-2">
        <div className="flex items-center justify-between gap-2 text-xs font-semibold text-foreground">
          <span className="flex min-w-0 items-center gap-2">
            <Layers className="h-4 w-4 text-primary" />
            <span>{copy.contextStack}</span>
          </span>
          <span className={cn("shrink-0 rounded px-1.5 py-0.5 text-[10px]", focusReady ? "bg-emerald-500/10 text-emerald-700 dark:text-emerald-300" : "bg-amber-500/10 text-amber-700 dark:text-amber-300")}>
            {focusReady ? copy.contextOk : copy.contextWeak}
          </span>
        </div>
        <div className="grid grid-cols-1 gap-2">
          <FocusRow
            icon={Target}
            label={t("rightRail.symbol")}
            value={workspace.selectedSymbol ?? t("rightRail.none")}
            actionLabel={copy.openData}
            onAction={() => onAddWidget("data.center", copy.openData)}
          />
          <FocusRow
            icon={SlidersHorizontal}
            label={t("rightRail.strategy")}
            value={workspace.selectedStrategy ?? t("rightRail.none")}
            actionLabel={copy.openStrategy}
            onAction={() => onAddWidget("backtest.workbench", copy.openStrategy)}
          />
          <FocusRow
            icon={BarChart3}
            label={t("rightRail.backtest")}
            value={workspace.selectedBacktest ?? t("rightRail.none")}
            actionLabel={copy.openWorkbench}
            onAction={() => onAddWidget("backtest.workbench", copy.openWorkbench)}
          />
        </div>
        <div className="rounded border border-border bg-background p-2">
          <div className="flex items-center gap-2 text-[10px] text-muted-foreground">
            <Gauge className="h-3.5 w-3.5 text-primary" />
            <span>{t("rightRail.mode")}</span>
          </div>
          <div className="mt-1 truncate text-xs font-semibold text-foreground">{t(modeConfig.labelKey)}</div>
          <div className="mt-1 text-[10px] leading-4 text-muted-foreground">{t(modeConfig.detailKey)}</div>
        </div>
      </section>

      <DecisionNotebookCard notebook={decisionNotebook} />

      <section className="space-y-2">
        <div className="flex items-center gap-2 text-xs font-semibold text-foreground">
          <ClipboardList className="h-4 w-4 text-primary" />
          <span>{copy.nextActions}</span>
        </div>
        <div className="grid gap-2">
          <RailActionButton
            icon={Workflow}
            label={t("task.startResearch")}
            detail={t("task.startResearch.detail")}
            tone={focusReady ? "ok" : "muted"}
            onClick={() => onOpenTask("startResearch")}
          />
          <RailActionButton
            icon={PlayCircle}
            label={t("task.runBacktest")}
            detail={workspace.selectedStrategy ?? t("rightRail.none")}
            tone={workspace.selectedStrategy ? "ok" : "warn"}
            onClick={() => onOpenTask("runBacktest")}
          />
          <RailActionButton
            icon={BarChart3}
            label={t("task.openEvidence")}
            detail={workspace.selectedBacktest ?? copy.recentBacktests}
            tone={workspace.selectedBacktest ? "ok" : "muted"}
            onClick={() => onOpenTask("openEvidence")}
          />
        </div>
      </section>

      <section className="space-y-2">
        <div className="flex items-center gap-2 text-xs font-semibold text-foreground">
          {connected ? <Wifi className="h-4 w-4 text-emerald-500" /> : <WifiOff className="h-4 w-4 text-muted-foreground" />}
          <span>{copy.dataState}</span>
        </div>
        <div className="grid grid-cols-2 gap-2">
          <RailMetric
            label={brokerLabel}
            value={connected ? t("rightRail.connected") : t("rightRail.disconnected")}
            detail={tradingStatus?.mode ?? undefined}
            tone={connected ? "ok" : "muted"}
          />
          <RailMetric
            label={t("rightRail.asset")}
            value={`${currency}${formatCompactNumber(asset?.total_asset)}`}
            detail={asset?.market_value ? `${t("rightRail.asset")} ${formatCompactNumber(asset.market_value)}` : undefined}
            tone={asset?.total_asset ? "ok" : "muted"}
          />
        </div>
        <DataSourceCard label={language === "zh" ? "示例数据" : "Demo data"} source={dataStatus?.demo} />
        <DataSourceCard label={language === "zh" ? "本地 RQ" : "Local RQ"} source={dataStatus?.runtime} />
        {dataNeedsUpdate ? (
          <RailActionButton
            icon={Database}
            label={copy.openData}
            detail={copy.dataWarn}
            tone="warn"
            onClick={() => onAddWidget("data.center", copy.openData)}
          />
        ) : null}
      </section>

      <section className="space-y-2">
        <div className="flex items-center justify-between gap-2 text-xs font-semibold text-foreground">
          <Target className="h-4 w-4 text-primary" />
          <span className="min-w-0 flex-1">{copy.recentBacktests}</span>
          <button
            className="shrink-0 rounded border border-border px-1.5 py-0.5 text-[10px] text-muted-foreground hover:text-foreground"
            onClick={() => onAddWidget("backtest.workbench", copy.openWorkbench)}
          >
            {copy.inspect}
          </button>
        </div>
        <div className="space-y-1.5">
          {latestBacktests.length === 0 ? (
            <div className="rounded border border-border bg-background px-2 py-3 text-xs text-muted-foreground">
              {copy.noRecentBacktests}
            </div>
          ) : latestBacktests.slice(0, 3).map((item) => (
            <button
              key={item.id}
              className="w-full min-w-0 rounded border border-border bg-background p-2 text-left transition-colors hover:border-primary/40 hover:bg-muted/30"
              onClick={() => selectBacktest(item)}
              title={`${copy.selectBacktest}: ${item.id}`}
            >
              <div className="flex min-w-0 items-center gap-2">
                <span className="min-w-0 flex-1 truncate text-xs font-semibold text-foreground">{item.strategy_id ?? item.id}</span>
                <span className="shrink-0 rounded bg-muted px-1.5 py-0.5 font-mono text-[10px] text-muted-foreground">
                  {formatDateLabel(item.run_at ?? item.created_at)}
                </span>
              </div>
              <div className="mt-1 grid grid-cols-3 gap-1 text-[10px] text-muted-foreground">
                <span className="truncate">{percentLabel(metricValue(item, "annual_return"))}</span>
                <span className="truncate">S {metricValue(item, "sharpe")?.toFixed?.(2) ?? "--"}</span>
                <span className="truncate">{percentLabel(metricValue(item, "max_drawdown"))}</span>
              </div>
            </button>
          ))}
        </div>
      </section>
    </div>
  )
}


function RightRailActivityPanel({
  tradingStatus,
  dataStatus,
  activityItems,
  score,
  agentConfig,
}: {
  tradingStatus?: TradingStatusSummary
  dataStatus?: DataStatusSummary
  activityItems: RailActivityItem[]
  score: number
  agentConfig?: ConexusStatus
}) {
  const { language, t } = useLanguage()
  const copy = rightRailCopy(language)
  const connected = tradingStatus?.connected === true
  const dataNeedsUpdate = Boolean(dataStatus?.runtime?.needs_update || dataStatus?.rq?.needs_update)
  const agentReady = agentConfig?.available === true
  const tone = scoreTone(score)

  return (
    <div className="space-y-3">
      <section className="space-y-2">
        <div className="flex items-center gap-2 text-xs font-semibold text-foreground">
          <Gauge className="h-4 w-4 text-primary" />
          <span>{copy.gateBoard}</span>
        </div>
        <div className="flex items-center gap-3 rounded border border-border bg-background p-2">
          <ScoreGauge
            value={score}
            tone={tone === "muted" ? "primary" : tone}
            size={64}
            label={copy.score}
          />
          <div className="min-w-0 flex-1 text-xs font-semibold text-foreground">
            {scoreLabel(score, copy)}
          </div>
        </div>
        <div className="grid gap-2">
          <RailMetric
            label={t("rightRail.connection")}
            value={connected ? t("rightRail.connected") : t("rightRail.disconnected")}
            tone={connected ? "ok" : "muted"}
          />
          <RailMetric
            label={t("rightRail.dataFreshness")}
            value={dataNeedsUpdate ? t("rightRail.needsUpdate") : t("rightRail.ready")}
            tone={dataNeedsUpdate ? "warn" : "ok"}
          />
          <RailMetric
            label={t("rightRail.agent")}
            value={agentReady ? copy.agentReady : copy.agentFallback}
            tone={agentReady ? "ok" : "muted"}
          />
          <RailMetric
            label={t("rightRail.riskGate")}
            value={tradingStatus?.supports_real_orders ? t("rightRail.liveCapable") : t("rightRail.paperFirst")}
            tone={tradingStatus?.supports_real_orders ? "warn" : "ok"}
          />
        </div>
      </section>

      <div className="rounded border border-border bg-background p-2">
        <div className="flex items-center gap-2 text-xs font-semibold text-foreground">
          <ShieldCheck className="h-4 w-4 text-emerald-500" />
          <span>{t("rightRail.guardrails")}</span>
        </div>
        <div className="mt-2 space-y-1 text-[10px] leading-4 text-muted-foreground">
          <div className="flex items-center gap-2">
            <RailStatusDot tone="ok" />
            <span>{t("rightRail.localOnly")}</span>
          </div>
          <div className="flex items-center gap-2">
            <RailStatusDot tone="ok" />
            <span>{t("rightRail.agentNoRun")}</span>
          </div>
          <div className="flex items-center gap-2">
            <RailStatusDot tone={dataNeedsUpdate ? "warn" : "ok"} />
            <span>{dataNeedsUpdate ? t("rightRail.dataNeedsReview") : t("rightRail.dataReady")}</span>
          </div>
        </div>
      </div>

      <section className="space-y-2">
        <div className="flex items-center gap-2 text-xs font-semibold text-foreground">
          <Activity className="h-4 w-4 text-primary" />
          <span>{copy.activityTrail}</span>
        </div>
        <div className="space-y-1.5">
          {activityItems.length === 0 ? (
            <div className="rounded border border-border bg-background p-3 text-xs text-muted-foreground">
              {copy.noActivity}
            </div>
          ) : activityItems.map((item) => {
            const Icon = item.icon
            return (
              <div key={item.id} className="grid grid-cols-[42px_minmax(0,1fr)] gap-2 rounded border border-border bg-background p-2 text-xs">
                <div className="font-mono text-[10px] text-muted-foreground">{item.time}</div>
                <div className="min-w-0">
                  <div className="flex min-w-0 items-center gap-1.5">
                    <RailStatusDot tone={item.tone} />
                    <Icon className="h-3.5 w-3.5 shrink-0 text-primary" />
                    <span className="truncate font-semibold text-foreground">{item.title}</span>
                  </div>
                  <div className="mt-0.5 break-words text-[10px] leading-4 text-muted-foreground">{item.detail}</div>
                </div>
              </div>
            )
          })}
        </div>
      </section>
    </div>
  )
}

export function WorkspaceRightRail({
  collapsed,
  narrow,
  width,
  activeTab,
  onSelectTab,
  onToggleCollapsed,
  onWidthChange,
  onAddWidget,
  onOpenWidget,
  onOpenTask,
}: {
  collapsed: boolean
  narrow: boolean
  width: number
  activeTab: RightRailTab
  onSelectTab: (tab: RightRailTab) => void
  onToggleCollapsed: () => void
  onWidthChange: (width: number) => void
  onAddWidget: (widgetId: string) => void
  onOpenWidget?: (widgetId: string, title?: string, targetMode?: WorkspaceMode) => void
  onOpenTask: (task: WorkspaceTask) => void
}) {
  const workspace = useWorkspace()
  const { activeMode } = workspace
  const { language, t } = useLanguage()
  const { decisionNotebook } = useAgentPrompt()
  const copy = rightRailCopy(language)
  const [activityItems, setActivityItems] = useState<RailActivityItem[]>([])

  const startResize = useCallback((event: ReactPointerEvent<HTMLDivElement>) => {
    event.preventDefault()
    const startX = event.clientX
    const startWidth = event.currentTarget.parentElement?.getBoundingClientRect().width ?? width
    const previousCursor = document.body.style.cursor
    const previousUserSelect = document.body.style.userSelect
    document.body.style.cursor = "col-resize"
    document.body.style.userSelect = "none"

    const handleMove = (moveEvent: PointerEvent) => {
      onWidthChange(startWidth + startX - moveEvent.clientX)
    }
    const finish = () => {
      window.removeEventListener("pointermove", handleMove)
      window.removeEventListener("pointerup", finish)
      window.removeEventListener("pointercancel", finish)
      document.body.style.cursor = previousCursor
      document.body.style.userSelect = previousUserSelect
    }

    window.addEventListener("pointermove", handleMove)
    window.addEventListener("pointerup", finish)
    window.addEventListener("pointercancel", finish)
  }, [onWidthChange, width])
  const { data: tradingStatus } = useQuery({
    queryKey: ["trading", "status"],
    queryFn: () => api.get<TradingStatusSummary>("/trading/status"),
    refetchInterval: 5000,
  })
  const tradingConnected = tradingStatus?.connected === true
  const { data: asset } = useQuery({
    queryKey: ["trading", "asset"],
    queryFn: () => api.get<TradingAssetSummary>("/trading/asset"),
    enabled: tradingConnected,
    refetchInterval: tradingConnected ? 10000 : false,
  })
  const { data: dataStatus } = useQuery({
    queryKey: ["data-center", "status"],
    queryFn: () => api.get<DataStatusSummary>("/data/status"),
    refetchInterval: 15000,
  })
  const { data: agentConfig } = useQuery({
    queryKey: ["conexus", "status"],
    queryFn: () => api.get<ConexusStatus>("/conexus/status"),
    staleTime: 60_000,
    refetchInterval: 30_000,
  })
  const { data: backtestList } = useQuery({
    queryKey: ["v2", "backtests", "right-rail"],
    queryFn: () => api.get<BacktestListSummary>("/backtests?limit=3"),
    staleTime: 30_000,
  })

  const score = useMemo(() => buildRailScore({
    activeMode,
    selectedSymbol: workspace.selectedSymbol,
    selectedStrategy: workspace.selectedStrategy,
    selectedBacktest: workspace.selectedBacktest,
    tradingStatus,
    dataStatus,
    agentConfig,
  }), [
    activeMode,
    agentConfig,
    dataStatus,
    tradingStatus,
    workspace.selectedBacktest,
    workspace.selectedStrategy,
    workspace.selectedSymbol,
  ])

  const pushActivity = useCallback((title: string, detail: string, tone: RailTone = "muted", icon: LucideIcon = Activity) => {
    setActivityItems((prev) => [
      { id: `${Date.now()}-${Math.random().toString(36).slice(2)}`, time: formatClock(), title, detail, tone, icon },
      ...prev,
    ].slice(0, 8))
  }, [])

  useEffect(() => {
    if (!decisionNotebook) return
    pushActivity(
      copy.latestNotebook,
      decisionNotebook.baseCase || decisionNotebook.classification || copy.noNotebook,
      "ok",
      ClipboardList,
    )
  }, [copy.latestNotebook, copy.noNotebook, decisionNotebook, pushActivity])

  const selectTab = (tab: RightRailTab) => {
    onSelectTab(tab)
    pushActivity(copy.tabChanged, t(RIGHT_RAIL_TABS.find((item) => item.id === tab)?.labelKey ?? "rightRail.context"), "muted", Activity)
    if (collapsed) onToggleCollapsed()
  }

  const addWidgetWithActivity = useCallback((widgetId: string, label: string) => {
    const targetMode = widgetId.startsWith("research.") ? "research" : undefined
    if (targetMode && onOpenWidget) {
      onOpenWidget(widgetId, undefined, targetMode)
    } else {
      onAddWidget(widgetId)
    }
    pushActivity(copy.widgetOpened, label, "ok", Plus)
  }, [copy.widgetOpened, onAddWidget, onOpenWidget, pushActivity])

  const dataNeedsUpdate = Boolean(dataStatus?.runtime?.needs_update || dataStatus?.rq?.needs_update)
  const headerTone = scoreTone(score)
  const taskStatus = taskStatusLabel({
    selectedStrategy: workspace.selectedStrategy,
    selectedBacktest: workspace.selectedBacktest,
    dataStatus,
    copy,
  })
  const latestBacktests = backtestList ?? []

  const panel = (
    <div className="flex h-full min-h-0 flex-col bg-card">
      <div className="shrink-0 border-b border-border px-3 py-2">
        <div className="flex items-center gap-2">
        <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded border border-primary/25 bg-primary/10 text-primary">
            <Gauge className="h-4 w-4" />
        </div>
        <div className="min-w-0 flex-1">
          <div className="truncate text-sm font-semibold text-foreground">{t("rightRail.title")}</div>
          <div className="flex min-w-0 items-center gap-1.5 text-[10px] text-muted-foreground">
              <RailStatusDot tone={headerTone} />
            <span className="truncate">{t(MODE_CONFIG[activeMode].labelKey)}</span>
          </div>
        </div>
          <div className="shrink-0 text-right">
            <div
              className={cn(
                "rounded px-2 py-0.5 font-mono text-sm font-bold",
                headerTone === "ok" && "bg-emerald-500/10 text-emerald-700 dark:text-emerald-300",
                headerTone === "warn" && "bg-amber-500/10 text-amber-700 dark:text-amber-300",
                headerTone === "danger" && "bg-rose-500/10 text-rose-700 dark:text-rose-300",
              )}
            >
              {score}
            </div>
            <div className="mt-0.5 text-[9px] text-muted-foreground">{copy.score}</div>
          </div>
        <button
          className="flex h-8 w-8 shrink-0 items-center justify-center rounded text-muted-foreground transition-colors hover:bg-muted/50 hover:text-foreground"
          onClick={onToggleCollapsed}
          title={narrow ? t("rightRail.close") : t("rightRail.collapse")}
        >
          {narrow ? <X className="h-4 w-4" /> : <PanelRightClose className="h-4 w-4" />}
        </button>
        </div>
        <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-muted">
          <div
            className={cn(
              "h-full rounded-full transition-[width]",
              headerTone === "ok" && "bg-emerald-500",
              headerTone === "warn" && "bg-amber-500",
              headerTone === "danger" && "bg-rose-500",
            )}
            style={{ width: `${score}%` }}
          />
        </div>
        <div className="mt-1 flex items-center justify-between gap-2 text-[10px] text-muted-foreground">
          <span className="truncate">{taskStatus}</span>
          <span className="shrink-0">{tradingStatus?.supports_real_orders ? copy.live : copy.paper}</span>
        </div>
      </div>

      <div className="grid grid-cols-3 gap-1 border-b border-border p-2">
        {RIGHT_RAIL_TABS.map(({ id, icon: Icon, labelKey }) => (
          <button
            key={id}
            className={cn(
              "flex min-h-8 min-w-0 items-center justify-center gap-1 rounded px-1 text-[10px] text-muted-foreground transition-colors hover:bg-muted/40 hover:text-foreground",
              activeTab === id && "bg-primary/10 text-primary ring-1 ring-primary/20",
            )}
            onClick={() => selectTab(id)}
            title={t(labelKey)}
          >
            <Icon className="h-3.5 w-3.5 shrink-0" />
            <span className="truncate">{t(labelKey)}</span>
          </button>
        ))}
      </div>

      <div className={cn("min-h-0 flex-1", activeTab === "agent" ? "overflow-hidden" : "overflow-auto p-3")}>
        {activeTab === "context" && (
          <RightRailContextPanel
            tradingStatus={tradingStatus}
            asset={asset}
            dataStatus={dataStatus}
            decisionNotebook={decisionNotebook}
            latestBacktests={latestBacktests}
            onAddWidget={addWidgetWithActivity}
            onOpenTask={onOpenTask}
            onRecordActivity={pushActivity}
          />
        )}
        <div className={cn("h-full min-h-0", activeTab !== "agent" && "hidden")}>
          <ResearchAgentPanel />
        </div>
        {activeTab === "activity" && (
          <RightRailActivityPanel
            tradingStatus={tradingStatus}
            dataStatus={dataStatus}
            activityItems={activityItems}
            score={score}
            agentConfig={agentConfig}
          />
        )}
      </div>
    </div>
  )

  if (narrow) {
    if (collapsed) return null
    return (
      <>
        <button
          aria-label={t("rightRail.close")}
          className="fixed inset-0 z-40 bg-background/70 backdrop-blur-sm md:hidden"
          onClick={onToggleCollapsed}
        />
        <aside
          className="fixed inset-y-0 right-0 z-50 max-w-[calc(100vw-24px)] border-l border-border shadow-xl md:hidden"
          style={{ width }}
        >
          {panel}
        </aside>
      </>
    )
  }

  if (collapsed) {
    return (
      <aside className="hidden h-full min-h-0 w-14 shrink-0 flex-col border-l border-border bg-card p-2 md:flex">
        <div
          className={cn(
            "mb-2 flex h-10 w-10 items-center justify-center rounded border font-mono text-xs font-bold",
            headerTone === "ok" && "border-emerald-500/30 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300",
            headerTone === "warn" && "border-amber-500/30 bg-amber-500/10 text-amber-700 dark:text-amber-300",
            headerTone === "danger" && "border-rose-500/30 bg-rose-500/10 text-rose-700 dark:text-rose-300",
          )}
          title={`${copy.score}: ${score}`}
        >
          {score}
        </div>
        <div className="flex flex-1 flex-col items-center gap-1">
          {RIGHT_RAIL_TABS.map(({ id, icon, labelKey }) => (
            <RailIconButton
              key={id}
              icon={icon}
              active={activeTab === id}
              tone={id === "context" ? (dataNeedsUpdate ? "warn" : "ok") : id === "agent" ? (agentConfig?.available ? "ok" : "muted") : headerTone}
              label={t(labelKey)}
              onClick={() => selectTab(id)}
            />
          ))}
          <RailIconButton icon={Search} label={`${t("rightRail.addSearch")} (Ctrl+K)`} onClick={dispatchCommandPalette} />
        </div>
        <RailIconButton icon={PanelRightOpen} label={t("rightRail.expand")} onClick={onToggleCollapsed} />
      </aside>
    )
  }

  return (
    <aside
      className="relative hidden h-full min-h-0 shrink-0 border-l border-border md:block"
      style={{ width, maxWidth: "58vw" }}
    >
      <div
        role="separator"
        aria-label={copy.resizeRail}
        aria-orientation="vertical"
        aria-valuemin={RIGHT_RAIL_MIN_WIDTH}
        aria-valuemax={RIGHT_RAIL_MAX_WIDTH}
        aria-valuenow={Math.round(width)}
        tabIndex={0}
        className="group absolute inset-y-0 left-0 z-20 hidden w-2 -translate-x-1/2 cursor-col-resize touch-none outline-none md:block"
        onPointerDown={startResize}
        onKeyDown={(event) => {
          if (event.key === "ArrowLeft") {
            event.preventDefault()
            onWidthChange(width + 16)
          } else if (event.key === "ArrowRight") {
            event.preventDefault()
            onWidthChange(width - 16)
          }
        }}
        title={copy.resizeRail}
      >
        <span className="absolute inset-y-0 left-1/2 w-px -translate-x-1/2 bg-transparent transition-colors group-hover:bg-primary/60 group-focus:bg-primary" />
        <span className="absolute left-1/2 top-1/2 h-10 w-1 -translate-x-1/2 -translate-y-1/2 rounded-full bg-border transition-colors group-hover:bg-primary group-focus:bg-primary" />
      </div>
      {panel}
    </aside>
  )
}
