import { useEffect, useMemo, useState } from "react"
import { Code2, Play, Save } from "lucide-react"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { Textarea } from "@/components/ui/textarea"
import { useStrategySdk, type SdkEntrypoint, type SdkParameter } from "@/contexts/StrategySdkContext"
import { useConfirm } from "@/hooks/useConfirm"
import { api } from "@/lib/api"
import { Widget } from "@/widgets/Widget"

function parameterValue(parameter: SdkParameter, raw: string): unknown {
  if (typeof parameter.default === "boolean") return raw === "true"
  if (typeof parameter.default === "number") return Number(raw)
  if (parameter.default === null && raw === "None") return null
  return raw
}

function ParameterEditor({ entrypoint, parameter }: { entrypoint: SdkEntrypoint; parameter: SdkParameter }) {
  const sdk = useStrategySdk()
  const [value, setValue] = useState(String(parameter.default))
  useEffect(() => setValue(String(parameter.default)), [parameter.default])
  return (
    <label className="grid grid-cols-[minmax(120px,1fr)_minmax(120px,1fr)_auto] items-center gap-2 text-xs">
      <span>
        <strong>{parameter.label || parameter.name}</strong>
        {parameter.label ? <code className="ml-1 text-muted-foreground">{parameter.name}</code> : null}
        {parameter.annotation ? <small className="ml-1 text-muted-foreground">{parameter.annotation}</small> : null}
        {parameter.description ? <small className="mt-1 block text-muted-foreground">{parameter.description}</small> : null}
      </span>
      {typeof parameter.default === "boolean" ? (
        <select className="h-8 rounded border border-input bg-background px-2" value={value} onChange={(event) => setValue(event.target.value)}>
          <option value="true">true</option><option value="false">false</option>
        </select>
      ) : (
        <Input
          className="h-8"
          type={typeof parameter.default === "number" ? "number" : "text"}
          min={parameter.minimum ?? undefined}
          max={parameter.maximum ?? undefined}
          step={parameter.step ?? undefined}
          value={value}
          onChange={(event) => setValue(event.target.value)}
        />
      )}
      <Button
        size="sm"
        variant="outline"
        disabled={!parameter.editable || !sdk.project?.editable}
        onClick={() => void sdk.structuredEdit({
          operation: "parameter",
          entrypoint_id: entrypoint.id,
          parameter: parameter.name,
          value: parameterValue(parameter, value),
        })}
      >应用</Button>
    </label>
  )
}

function EntrypointCard({ entrypoint }: { entrypoint: SdkEntrypoint }) {
  const sdk = useStrategySdk()
  const schedule = entrypoint.kind === "signal"
    ? entrypoint.metadata.schedule as Record<string, string> | undefined
    : undefined
  return (
    <div className="space-y-2 rounded-md border border-border p-3">
      <div className="flex flex-wrap items-center gap-2">
        <Badge variant="outline">@{entrypoint.kind}</Badge>
        <strong className="text-sm">{entrypoint.label || entrypoint.id}</strong>
        <code className="text-xs text-muted-foreground">{entrypoint.function} · L{entrypoint.line}</code>
        {entrypoint.event ? <Badge variant="secondary">{entrypoint.event}</Badge> : null}
      </div>
      {schedule ? (
        <div className="flex flex-wrap items-center gap-2 text-xs">
          <span>重新计算：</span>
          {schedule.mode === "structured" ? (
            <>
              <select
                className="h-8 rounded border border-input bg-background px-2"
                value={schedule.frequency}
                disabled={!sdk.project?.editable}
                onChange={(event) => void sdk.structuredEdit({
                  operation: "schedule",
                  entrypoint_id: entrypoint.id,
                  frequency: event.target.value,
                  selector: event.target.value === "daily" ? "every" : (schedule.selector === "every" ? "last_trading_day" : schedule.selector),
                  at: schedule.at,
                })}
              >
                <option value="daily">每日</option><option value="weekly">每周</option><option value="monthly">每月</option>
              </select>
              {schedule.frequency !== "daily" ? (
                <select
                  className="h-8 rounded border border-input bg-background px-2"
                  value={schedule.selector}
                  disabled={!sdk.project?.editable}
                  onChange={(event) => void sdk.structuredEdit({
                    operation: "schedule",
                    entrypoint_id: entrypoint.id,
                    frequency: schedule.frequency,
                    selector: event.target.value,
                    at: schedule.at,
                  })}
                >
                  <option value="first_trading_day">首个交易日</option>
                  <option value="last_trading_day">最后交易日</option>
                </select>
              ) : null}
              <select
                className="h-8 rounded border border-input bg-background px-2"
                value={schedule.at}
                disabled={!sdk.project?.editable}
                onChange={(event) => void sdk.structuredEdit({
                  operation: "schedule",
                  entrypoint_id: entrypoint.id,
                  frequency: schedule.frequency,
                  selector: schedule.selector,
                  at: event.target.value,
                })}
              ><option value="open">开盘</option><option value="close">收盘</option></select>
            </>
          ) : <Badge variant="secondary">自定义 Python 调度</Badge>}
        </div>
      ) : null}
      {entrypoint.parameters.length ? (
        <div className="space-y-2 border-t border-border pt-2">
          {entrypoint.parameters.map((parameter) => (
            <ParameterEditor key={parameter.name} entrypoint={entrypoint} parameter={parameter} />
          ))}
        </div>
      ) : <div className="text-xs text-muted-foreground">无可投影参数；逻辑仍可在源码中自由编辑。</div>}
    </div>
  )
}

export function StrategyWorkbenchWidget() {
  const sdk = useStrategySdk()
  const confirm = useConfirm()
  const project = sdk.project
  const [source, setSource] = useState("")
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState("")
  const [preview, setPreview] = useState<Record<string, unknown> | null>(null)
  const strategyEntrypoints = useMemo(
    () => project?.inspection.entrypoints.filter((item) => item.kind !== "factor" && item.kind !== "universe") ?? [],
    [project?.inspection.entrypoints],
  )

  useEffect(() => setSource(project?.draft_source ?? ""), [project?.id, project?.draft_source_sha256])

  async function saveDraft() {
    if (!project?.editable) return
    setBusy(true); setError("")
    try { await sdk.updateDraft(source) }
    catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)) }
    finally { setBusy(false) }
  }

  async function freezeRevision() {
    if (!project?.editable) return
    if (!await confirm({
      title: "冻结新的策略修订",
      description: "系统将执行完整源码的合同探针。成功后形成不可变 StrategySourcePackage；后续检验和回测只运行这个 revision。",
      confirmText: "验证并冻结",
    })) return
    setBusy(true); setError("")
    try { await sdk.saveRevision() }
    catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)) }
    finally { setBusy(false) }
  }

  async function runPreview(operation: "signal" | "portfolio" | "execution") {
    if (!project || project.dirty) return
    if (!await confirm({
      title: "运行受信任本机 Python",
      description: `将运行 ${project.id}@${project.current_revision}，源码哈希 ${project.current_package.source_sha256.slice(0, 16)}。本机 Python 不是安全沙箱。`,
      confirmText: "运行预览",
    })) return
    setBusy(true); setError("")
    try {
      const value = await api.post<Record<string, unknown>>(`/strategy/projects/${project.id}/preview`, {
        operation,
        profile: project.profile,
        revision: project.current_revision,
        confirm_python_execution: true,
      })
      setPreview(value)
    } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)) }
    finally { setBusy(false) }
  }

  if (!project) return <Widget title="策略工作台" loading={sdk.loading} error={sdk.error}><span /></Widget>
  return (
    <Widget
      title="策略工作台"
      error={error}
      bodyClassName="overflow-auto"
      actions={(
        <div className="flex gap-1">
          <Button size="sm" variant="outline" disabled={!project.editable || busy} onClick={() => void saveDraft()}><Save />保存草稿</Button>
          <Button size="sm" disabled={!project.editable || busy || !project.dirty} onClick={() => void freezeRevision()}>冻结 revision</Button>
        </div>
      )}
    >
      <div className="mb-3 flex flex-wrap items-center gap-2 text-xs">
        <Badge>SDK v1</Badge>
        <Badge variant="outline">r{project.current_revision}</Badge>
        <Badge variant={project.dirty ? "destructive" : "secondary"}>{project.dirty ? "预览失效：请冻结草稿" : "可复现"}</Badge>
        <code>{project.draft_source_sha256.slice(0, 20)}</code>
        <span className="text-muted-foreground">表单和源码编辑的是同一棵 Python CST。</span>
      </div>
      <Tabs defaultValue="model" className="min-h-0">
        <TabsList><TabsTrigger value="model">结构化投影</TabsTrigger><TabsTrigger value="source">完整 Python</TabsTrigger><TabsTrigger value="preview">预览</TabsTrigger></TabsList>
        <TabsContent value="model" className="space-y-3">
          {strategyEntrypoints.map((entrypoint) => <EntrypointCard key={`${entrypoint.kind}:${entrypoint.id}`} entrypoint={entrypoint} />)}
        </TabsContent>
        <TabsContent value="source" className="space-y-2">
          <div className="flex items-center gap-2 text-xs text-muted-foreground"><Code2 />这里就是最终运行的完整模块；核心成交和账户引擎不拼入可编辑源码。</div>
          <Textarea
            className="min-h-[620px] resize-y font-mono text-xs leading-5"
            spellCheck={false}
            value={source}
            disabled={!project.editable}
            onChange={(event) => setSource(event.target.value)}
          />
          {project.inspection.warnings.map((warning) => <div key={`${warning.line}-${warning.message}`} className="text-xs text-amber-600">L{warning.line}: {warning.message}</div>)}
        </TabsContent>
        <TabsContent value="preview" className="space-y-3">
          <div className="flex flex-wrap gap-2">
            {(["signal", "portfolio", "execution"] as const).map((operation) => (
              <Button key={operation} variant="outline" disabled={busy || project.dirty} onClick={() => void runPreview(operation)}><Play />{operation}</Button>
            ))}
          </div>
          {preview ? <pre className="max-h-[620px] overflow-auto rounded bg-muted p-3 text-xs">{JSON.stringify(preview, null, 2)}</pre> : <p className="text-sm text-muted-foreground">预览与回测调用同一冻结函数；不会调用 mock 或表达式替代实现。</p>}
        </TabsContent>
      </Tabs>
    </Widget>
  )
}
