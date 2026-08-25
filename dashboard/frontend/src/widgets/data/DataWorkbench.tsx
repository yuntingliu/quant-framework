import { useEffect, useMemo, useState } from "react"
import { Database, Save, ShieldCheck } from "lucide-react"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Textarea } from "@/components/ui/textarea"
import { useStrategySdk } from "@/contexts/StrategySdkContext"
import { api } from "@/lib/api"
import { Widget } from "@/widgets/Widget"

interface FieldCatalog {
  profile: "demo" | "runtime"
  start_date: string
  end_date: string
  datasets: Record<string, Array<{ name: string; data_type: string; nullable: boolean }>>
}

export function DataWorkbenchWidget() {
  const sdk = useStrategySdk()
  const project = sdk.project
  const [source, setSource] = useState("")
  const [fields, setFields] = useState<FieldCatalog | null>(null)
  const [lookback, setLookback] = useState("260")
  const [maxWeight, setMaxWeight] = useState("1")
  const [maxGross, setMaxGross] = useState("1")
  const [portfolioValue, setPortfolioValue] = useState("1000000")
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState("")
  const universe = useMemo(
    () => project?.inspection.entrypoints.find((item) => item.kind === "universe"),
    [project?.inspection.entrypoints],
  )

  useEffect(() => {
    if (!project) return
    setSource(project.draft_source)
    setLookback(String(project.settings.lookback_days ?? 260))
    setMaxWeight(String(project.settings.max_weight ?? 1))
    setMaxGross(String(project.settings.max_gross_exposure ?? 1))
    setPortfolioValue(String(project.settings.portfolio_value ?? 1_000_000))
    void api.get<FieldCatalog>(`/strategy/fields?profile=${project.profile}`).then(setFields).catch((reason: Error) => setError(reason.message))
  }, [project?.id, project?.draft_source_sha256, project?.profile])

  async function saveDataContract() {
    if (!project?.editable) return
    setBusy(true); setError("")
    try {
      if (source !== project.draft_source) await sdk.updateDraft(source)
      await sdk.updateMetadata({
        name: project.name,
        description: project.description,
        profile: project.profile,
        settings: {
          ...project.settings,
          lookback_days: Number(lookback),
          max_weight: Number(maxWeight),
          max_gross_exposure: Number(maxGross),
          portfolio_value: Number(portfolioValue),
        },
      })
    } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)) }
    finally { setBusy(false) }
  }

  if (!project) return <Widget title="数据工作台" loading={sdk.loading} error={sdk.error}><span /></Widget>
  return (
    <Widget title="数据工作台" error={error} bodyClassName="overflow-auto" actions={<Button size="sm" disabled={!project.editable || busy} onClick={() => void saveDataContract()}><Save />保存数据契约</Button>}>
      <div className="space-y-4">
        <div className="flex flex-wrap items-center gap-2 text-xs">
          <Badge><Database />{project.profile}</Badge>
          <Badge variant="outline">{fields?.start_date ?? "—"} → {fields?.end_date ?? "—"}</Badge>
          <Badge variant="secondary"><ShieldCheck />Point-in-time</Badge>
          <span className="text-muted-foreground">运行时不会自动切换数据环境，也不会向 Context 暴露 provider 或文件路径。</span>
        </div>
        <div className="grid gap-3 lg:grid-cols-[minmax(0,1fr)_340px]">
          <section className="space-y-2">
            <div className="flex items-center justify-between"><strong className="text-sm">@universe Python</strong><code className="text-xs text-muted-foreground">{universe?.function ?? "missing"}</code></div>
            <Textarea className="min-h-[560px] resize-y font-mono text-xs leading-5" value={source} disabled={!project.editable} onChange={(event) => setSource(event.target.value)} />
            <p className="text-xs text-muted-foreground">这里编辑的是同一完整模块。Project/Data 关注 @universe 与 DATA_REQUIREMENTS；其他自定义代码会原样保留。</p>
          </section>
          <aside className="space-y-3">
            <div className="space-y-2 rounded border border-border p-3">
              <strong className="text-sm">核心不可绕过限制</strong>
              <label className="block text-xs">历史窗口<Input type="number" min={20} max={2000} value={lookback} onChange={(event) => setLookback(event.target.value)} /></label>
              <label className="block text-xs">单标的最大权重<Input type="number" min={0.01} max={1} step={0.01} value={maxWeight} onChange={(event) => setMaxWeight(event.target.value)} /></label>
              <label className="block text-xs">最大总敞口<Input type="number" min={0.01} max={1} step={0.01} value={maxGross} onChange={(event) => setMaxGross(event.target.value)} /></label>
              <label className="block text-xs">组合资金<Input type="number" min={1} value={portfolioValue} onChange={(event) => setPortfolioValue(event.target.value)} /></label>
            </div>
            {Object.entries(fields?.datasets ?? {}).map(([dataset, rows]) => (
              <div key={dataset} className="rounded border border-border p-3">
                <div className="mb-2 flex items-center justify-between text-sm"><strong>{dataset}</strong><Badge variant="outline">{rows.length} fields</Badge></div>
                <div className="flex flex-wrap gap-1">{rows.map((field) => <code key={field.name} className="rounded bg-muted px-1.5 py-1 text-[11px]">{field.name}: {field.data_type}</code>)}</div>
              </div>
            ))}
          </aside>
        </div>
      </div>
    </Widget>
  )
}
