import { useEffect, useMemo, useRef, useState } from "react"
import { Braces, Database, FlaskConical, Play, Plus, Save } from "lucide-react"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { Textarea } from "@/components/ui/textarea"
import { useStrategySdk, type SdkEntrypoint } from "@/contexts/StrategySdkContext"
import { useConfirm } from "@/hooks/useConfirm"
import { api } from "@/lib/api"
import { Widget } from "@/widgets/Widget"

interface FieldCatalog {
  profile: "demo" | "runtime"
  start_date: string
  end_date: string
  datasets: Record<string, Array<{ name: string; data_type: string; nullable: boolean }>>
}

function factorSnippet(id = "new_factor") {
  return `\n\n@factor(id="${id}", inputs=["close"])\ndef ${id}(context, *, window: int = 20):\n    close = context.history("close", window=window)\n    return close.iloc[-1] / close.iloc[0] - 1.0\n`
}

export function FactorWorkbenchWidget() {
  const sdk = useStrategySdk()
  const confirm = useConfirm()
  const project = sdk.project
  const editor = useRef<HTMLTextAreaElement | null>(null)
  const [source, setSource] = useState("")
  const [selectedFactor, setSelectedFactor] = useState("")
  const [fields, setFields] = useState<FieldCatalog | null>(null)
  const [startDate, setStartDate] = useState("")
  const [endDate, setEndDate] = useState("")
  const [result, setResult] = useState<Record<string, unknown> | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState("")
  const factors = useMemo(
    () => project?.inspection.entrypoints.filter((item) => item.kind === "factor") ?? [],
    [project?.inspection.entrypoints],
  )
  const activeFactor = factors.find((item) => item.id === selectedFactor) ?? factors[0]

  useEffect(() => {
    setSource(project?.draft_source ?? "")
    if (project && !factors.some((item) => item.id === selectedFactor)) setSelectedFactor(factors[0]?.id ?? "")
  }, [project?.id, project?.draft_source_sha256, factors, selectedFactor])

  useEffect(() => {
    if (!project) return
    void api.get<FieldCatalog>(`/strategy/fields?profile=${project.profile}`).then((value) => {
      setFields(value); setStartDate(value.start_date); setEndDate(value.end_date)
    }).catch((reason: Error) => setError(reason.message))
  }, [project?.profile])

  function insert(text: string) {
    const node = editor.current
    const cursor = node?.selectionStart ?? source.indexOf("@signal")
    const position = cursor >= 0 ? cursor : source.length
    const lineStart = source.lastIndexOf("\n", Math.max(0, position - 1)) + 1
    const indent = source.slice(lineStart, position).match(/^\s*/)?.[0] ?? ""
    const normalized = text.split("\n").map((line, index) => index === 0 ? line : `${indent}${line}`).join("\n")
    const updated = source.slice(0, position) + normalized + source.slice(position)
    setSource(updated)
    requestAnimationFrame(() => {
      node?.focus(); node?.setSelectionRange(position + normalized.length, position + normalized.length)
    })
  }

  function insertFactorStatement(text: string) {
    const node = editor.current
    if (!activeFactor) return
    const definition = source.indexOf(`def ${activeFactor.function}(`)
    const blockEnd = source.indexOf("\n@", Math.max(0, definition))
    const boundedEnd = blockEnd >= 0 ? blockEnd : source.length
    const caret = node?.selectionStart ?? -1
    if (node && document.activeElement === node && caret > definition && caret < boundedEnd) {
      insert(text)
      return
    }
    const returnLine = source.indexOf("\n    return", Math.max(0, definition))
    const position = returnLine >= 0 && returnLine < boundedEnd ? returnLine + 5 : boundedEnd
    const updated = `${source.slice(0, position)}${text}\n    ${source.slice(position)}`
    setSource(updated)
    requestAnimationFrame(() => {
      node?.focus(); node?.setSelectionRange(position + text.length, position + text.length)
    })
  }

  function insertField(field: string, dataset: string) {
    const code = dataset === "market_bars"
      ? `${field} = context.history("${field}", window=20)`
      : `${field} = context.fundamental("${field}")`
    insertFactorStatement(code)
  }

  function insertFactorDependency(factor: SdkEntrypoint) {
    insertFactorStatement(`${factor.id.replace(/-/g, "_")}_value = context.factor("${factor.id}")`)
  }

  async function saveDraft() {
    if (!project?.editable) return
    setBusy(true); setError("")
    try { await sdk.updateDraft(source) }
    catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)) }
    finally { setBusy(false) }
  }

  async function evaluate(history: boolean) {
    if (!project || !activeFactor || project.dirty) return
    if (!await confirm({
      title: history ? "运行单因子历史检验" : "运行因子截面",
      description: `将直接调用 ${project.id}@${project.current_revision} 中保存的 @factor(${activeFactor.id})。本机 Python 不是安全沙箱。`,
      confirmText: "运行",
    })) return
    setBusy(true); setError("")
    try {
      const endpoint = `/strategy/projects/${project.id}/factors/${activeFactor.id}/${history ? "history" : "snapshot"}`
      const body = history
        ? { profile: project.profile, start_date: startDate, end_date: endDate, revision: project.current_revision, frequency: "monthly", parameters: {}, confirm_python_execution: true }
        : { profile: project.profile, as_of_date: endDate, revision: project.current_revision, parameters: {}, confirm_python_execution: true }
      setResult(await api.post<Record<string, unknown>>(endpoint, body))
    } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)) }
    finally { setBusy(false) }
  }

  if (!project) return <Widget title="因子研究" loading={sdk.loading} error={sdk.error}><span /></Widget>
  return (
    <Widget
      title="因子研究"
      error={error}
      bodyClassName="overflow-auto"
      actions={<Button size="sm" disabled={!project.editable || busy} onClick={() => void saveDraft()}><Save />保存同一草稿</Button>}
    >
      <div className="mb-3 flex flex-wrap items-center gap-2 text-xs">
        <Badge>Python 因子函数</Badge><Badge variant="outline">r{project.current_revision}</Badge>
        <code>{project.draft_source_sha256.slice(0, 18)}</code>
        <span className="text-muted-foreground">字段、因子依赖、参数表单都直接修改这份源码。</span>
      </div>
      <div className="grid gap-3 xl:grid-cols-[240px_minmax(0,1fr)_300px]">
        <aside className="space-y-3 rounded-md border border-border p-3">
          <div className="flex items-center gap-2 text-sm font-medium"><FlaskConical />已注册因子</div>
          {factors.map((factor) => (
            <button key={factor.id} className={`w-full rounded border p-2 text-left text-xs ${activeFactor?.id === factor.id ? "border-primary bg-primary/10" : "border-border"}`} onClick={() => setSelectedFactor(factor.id)}>
              <strong>{factor.label || factor.id}</strong><br /><code>{factor.function}</code>
            </button>
          ))}
          <Button variant="outline" className="w-full" disabled={!project.editable} onClick={() => {
            const marker = source.indexOf("\n@signal")
            const position = marker >= 0 ? marker : source.length
            setSource(`${source.slice(0, position)}${factorSnippet(`factor_${factors.length + 1}`)}${source.slice(position)}`)
          }}><Plus />插入新 @factor</Button>
          {activeFactor?.parameters.map((parameter) => (
            <div key={parameter.name} className="rounded bg-muted p-2 text-xs">
              <div className="font-medium">{parameter.label || parameter.name}{parameter.label ? <code className="ml-1 text-muted-foreground">{parameter.name}</code> : null}</div>
              <div className="text-muted-foreground">
                默认值 {String(parameter.default)} · {parameter.editable ? "表单可编辑" : "custom"}
                {parameter.minimum !== null || parameter.maximum !== null ? ` · 范围 ${parameter.minimum ?? "−∞"}…${parameter.maximum ?? "+∞"}` : ""}
              </div>
              {parameter.description ? <div className="mt-1 text-muted-foreground">{parameter.description}</div> : null}
            </div>
          ))}
        </aside>

        <section className="min-w-0 space-y-2">
          <div className="flex items-center justify-between text-sm"><span className="flex items-center gap-2"><Braces />完整 canonical module</span><Badge variant={project.dirty ? "destructive" : "secondary"}>{project.dirty ? "未冻结" : "已冻结"}</Badge></div>
          <Textarea ref={editor} className="min-h-[650px] resize-y font-mono text-xs leading-5" spellCheck={false} value={source} disabled={!project.editable} onChange={(event) => setSource(event.target.value)} />
        </section>

        <aside className="min-w-0 space-y-3">
          <Tabs defaultValue="fields">
            <TabsList><TabsTrigger value="fields"><Database />字段</TabsTrigger><TabsTrigger value="factors">因子</TabsTrigger><TabsTrigger value="test">检验</TabsTrigger></TabsList>
            <TabsContent value="fields" className="max-h-[620px] space-y-3 overflow-auto">
              {Object.entries(fields?.datasets ?? {}).map(([dataset, rows]) => (
                <div key={dataset} className="space-y-1">
                  <div className="text-xs font-medium">{dataset}</div>
                  <div className="flex flex-wrap gap-1">
                    {rows.map((field) => <Button key={field.name} size="sm" variant="outline" onClick={() => insertField(field.name, dataset)}>{field.name}</Button>)}
                  </div>
                </div>
              ))}
            </TabsContent>
            <TabsContent value="factors" className="space-y-1">
              {factors.filter((item) => item.id !== activeFactor?.id).map((factor) => <Button key={factor.id} className="w-full justify-start" variant="outline" onClick={() => insertFactorDependency(factor)}>{factor.id}</Button>)}
            </TabsContent>
            <TabsContent value="test" className="space-y-3">
              <label className="block text-xs">开始日期<Input type="date" value={startDate} onChange={(event) => setStartDate(event.target.value)} /></label>
              <label className="block text-xs">结束日期<Input type="date" value={endDate} onChange={(event) => setEndDate(event.target.value)} /></label>
              <div className="flex gap-2"><Button variant="outline" disabled={busy || project.dirty || !activeFactor} onClick={() => void evaluate(false)}><Play />截面</Button><Button disabled={busy || project.dirty || !activeFactor} onClick={() => void evaluate(true)}><Play />历史</Button></div>
              {project.dirty ? <p className="text-xs text-amber-600">先冻结 revision；检验不运行可变草稿。</p> : null}
              {result ? <pre className="max-h-[420px] overflow-auto rounded bg-muted p-2 text-[11px]">{JSON.stringify(result, null, 2)}</pre> : null}
            </TabsContent>
          </Tabs>
        </aside>
      </div>
    </Widget>
  )
}
