import { useEffect, useRef, useState } from "react"
import { configurationApi, errorCode, services, type CredentialPolicy, type Preview, type Service } from "@/lib/configuration-api"
import { buttonClass, ConfigurationNotice, controlClass, Field, useConfigurationOperation, useConfigurationText, type ConfigurationOperation } from "./ConfigurationControls"
import { TransferPreview } from "./TransferPreview"

const MAX_BYTES = 256 * 1024
export function ConfigurationTransfer({ onApplied, coordinator }: { onApplied: () => void; coordinator?: ConfigurationOperation }) {
  const localOperation = useConfigurationOperation(onApplied)
  const tracking = coordinator ?? localOperation
  const { operation } = tracking
  const tr = useConfigurationText()
  const [file, setFile] = useState("")
  const [scope, setScope] = useState<Service[]>([...services])
  const [mode, setMode] = useState("merge")
  const [selection, setSelection] = useState(true)
  const [preview, setPreview] = useState<Preview | null>(null)
  const [policy, setPolicy] = useState<CredentialPolicy[]>([])
  const [privateValues, setPrivateValues] = useState<Record<string, string>>({})
  const [consents, setConsents] = useState<string[]>([])
  const [confirmed, setConfirmed] = useState(false)
  const [ready, setReady] = useState(false)
  const [working, setBusy] = useState(false)
  const busy = working || tracking.busy
  const [code, setCode] = useState("")
  const [rollback, setRollback] = useState<string | null>(null)
  const input = useRef<HTMLInputElement>(null)
  const pending = useRef(false)
  const alive = useRef(true)
  useEffect(() => { alive.current = true; return () => { alive.current = false } }, [])

  function invalidate() { setReady(false); setConsents([]); setConfirmed(false) }
  function reset() {
    setFile(""); setPreview(null); setPolicy([]); setPrivateValues({}); setRollback(null); setCode(""); invalidate()
    if (input.current) input.current.value = ""
  }
  async function run(action: () => Promise<void>) {
    if (pending.current || tracking.busy) return
    pending.current = true; setBusy(true); setCode("")
    try { await action() } catch (error) { if (alive.current) setCode(errorCode(error)) }
    finally { pending.current = false; if (alive.current) setBusy(false) }
  }
  async function pick(value?: File) {
    reset()
    if (!value) return
    if (value.size > MAX_BYTES) { setCode("FILE_TOO_LARGE"); return }
    await run(async () => {
      const text = await value.text()
      if (!alive.current) return
      if (new TextEncoder().encode(text).byteLength > MAX_BYTES) { setCode("FILE_TOO_LARGE"); return }
      setFile(text)
    })
  }
  async function makePreview(origin: string | null = rollback) {
    invalidate(); setPrivateValues({})
    await run(async () => {
      const result = origin ? await configurationApi.rollbackPreview({ operation: origin, credential_policy: policy })
        : await configurationApi.preview({ configuration: file, services: scope, mode, apply_selections: selection, credential_policy: policy })
      if (!alive.current) return
      setPreview(result); setReady(result.can_apply && Boolean(result.receipt)); setRollback(origin)
    })
  }
  function changePolicy(service: Service, id: string, action: CredentialPolicy["action"] | "") {
    setPolicy(rows => [...rows.filter(row => row.service !== service || row.id !== id), ...(action ? [{ service, id, action }] : [])])
    setPrivateValues({}); invalidate()
  }
  async function apply() {
    if (!preview?.receipt || !ready || !confirmed || pending.current || tracking.busy || operation?.state === "uncertain") return
    const receipt = preview.receipt
    const body = { receipt, expected_revision: preview.revision, confirm: true, consents, credentials: policy.filter(row => row.action === "replace").map(row => ({ service: row.service, id: row.id, value: privateValues[row.service + ":" + row.id] ?? "" })) }
    const kind = preview.kind
    // Discard every fresh input immediately. Only the bounded request holds its
    // private body; the dialog-independent coordinator stores no credentials.
    reset()
    await tracking.apply(body, kind)
  }
  const freshRequired = policy.filter(row => row.action === "replace")
  return <section className="grid gap-3" aria-busy={busy}>
    <p className="text-sm text-muted-foreground">{tr("Portable metadata only. API keys, passwords, research data, reports and private history are excluded. Transfer requires an authenticated local API origin.", "仅传输可移植元数据，不包含 API 密钥、密码、研究数据、报告或私有历史。需要经过验证的本地 API 来源。")}</p>
    {code && <ConfigurationNotice code={code} />}
    {tracking.code && <ConfigurationNotice code={tracking.code} />}
    {tracking.busy && <p role="status">{tr("Configuration operation pending. You may close this dialog; reopen it to inspect the outcome. Do not resubmit.", "配置操作正在进行。可以关闭此对话框，重新打开后检查结果。请勿重复提交。")}</p>}
    <button className={buttonClass + " justify-self-start"} disabled={busy} onClick={() => void run(async () => {
      const text = await configurationApi.export()
      if (!alive.current) return
      const url = URL.createObjectURL(new Blob([text], { type: "application/json" }))
      try { const link = document.createElement("a"); link.href = url; link.download = "alphalab-configuration.json"; document.body.append(link); link.click(); link.remove() }
      finally { window.setTimeout(() => URL.revokeObjectURL(url), 1000) }
    })}>{tr("Export sanitized JSON", "导出脱敏 JSON")}</button>
    <fieldset disabled={busy} className="grid gap-3">
      <Field label={tr("Import JSON file (maximum 256 KiB)", "导入 JSON 文件（最大 256 KiB）")}><input ref={input} type="file" accept=".json,application/json" onChange={event => void pick(event.target.files?.[0])} /></Field>
      <Field label={tr("Import mode", "导入模式")}><select className={controlClass} value={mode} onChange={event => { setMode(event.target.value); setPreview(null); setPolicy([]); setPrivateValues({}); invalidate() }}><option value="merge">{tr("Merge", "合并")}</option><option value="replace">{tr("Replace selected services", "替换所选服务")}</option></select></Field>
      <div className="flex flex-wrap gap-3" role="group" aria-label={tr("Service scope", "服务范围")}>{services.map(service => <label key={service} className="flex items-center gap-2 text-sm"><input type="checkbox" checked={scope.includes(service)} onChange={event => { setScope(items => event.target.checked ? [...items, service] : items.filter(item => item !== service)); setPreview(null); setPolicy([]); setPrivateValues({}); invalidate() }} />{service}</label>)}</div>
      <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={selection} onChange={event => { setSelection(event.target.checked); setPreview(null); setPolicy([]); setPrivateValues({}); invalidate() }} />{tr("Apply imported selections", "应用导入的选择")}</label>
      <button className={buttonClass} disabled={!file || !scope.length || Boolean(rollback)} onClick={() => void makePreview(null)}>{tr("Preview import", "预览导入")}</button>
    </fieldset>
    {preview && <>
      <TransferPreview preview={preview} />
      <fieldset disabled={busy} className="grid gap-3"><legend className="text-sm font-medium">{tr("Explicit credential policy for every imported profile", "每个导入配置的明确凭据策略")}</legend>
        {(preview.credential_options ?? []).map(row => <Field key={row.service + row.id} label={`${row.service} / ${row.id}`}><select className={controlClass} value={policy.find(item => item.service === row.service && item.id === row.id)?.action ?? ""} onChange={event => changePolicy(row.service, row.id, event.target.value as CredentialPolicy["action"])}>
          <option value="">{tr("Choose explicitly", "请明确选择")}</option>{row.retain_eligible && <option value="retain">{tr("Retain current same-profile credential", "保留当前同一配置的凭据")}</option>}<option value="clear">{tr("Clear / leave unconfigured", "清除 / 保持未配置")}</option><option value="replace">{tr("Supply fresh private credential", "提供新的私密凭据")}</option>
        </select></Field>)}
        <button className={buttonClass} onClick={() => void makePreview()}>{tr("Validate choices and refresh preview", "验证选择并刷新预览")}</button>
      </fieldset>
      {ready && <fieldset disabled={busy} className="grid gap-3">
        {freshRequired.map(row => <Field key={row.service + row.id} label={`${tr("Fresh credential", "新凭据")}: ${row.service} / ${row.id}`}><input type="password" autoComplete="new-password" className={controlClass} maxLength={8192} value={privateValues[row.service + ":" + row.id] ?? ""} onChange={event => setPrivateValues(values => ({ ...values, [row.service + ":" + row.id]: event.target.value }))} /></Field>)}
        {preview.required_consents.map(consent => <label key={consent} className="flex items-center gap-2 text-sm"><input type="checkbox" checked={consents.includes(consent)} onChange={event => setConsents(values => event.target.checked ? [...values, consent] : values.filter(value => value !== consent))} />{tr("I consent", "我同意")}: {consent}</label>)}
        <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={confirmed} onChange={event => setConfirmed(event.target.checked)} />{tr("I reviewed this diff, missing credentials and destructive changes. Apply exactly this preview.", "我已检查差异、缺失凭据及破坏性更改。仅应用此预览。")}</label>
        <button className={buttonClass} disabled={operation?.state === "uncertain" || !confirmed || consents.length !== preview.required_consents.length || freshRequired.some(row => !privateValues[row.service + ":" + row.id]?.trim())} onClick={() => void apply()}>{preview.kind === "rollback" ? tr("Confirm logical rollback", "确认逻辑回滚") : tr("Confirm import", "确认导入")}</button>
      </fieldset>}
    </>}
    <button className={buttonClass + " justify-self-start"} disabled={busy} onClick={reset}>{tr("Cancel import and clear private inputs", "取消导入并清除私密输入")}</button>
    {operation && <div className="grid gap-2 text-sm" role="status">
      <p>{tr("Operation", "操作")}: {operation.operation} — {operation.state === "committed" ? tr("Applied successfully", "已成功应用") : operation.state}</p>
      <button className={buttonClass} disabled={busy} onClick={() => { setCode(""); void tracking.inspect(operation.operation) }}>{tr("Inspect operation outcome", "检查操作结果")}</button>
      {operation.rollback_available === true && <button className={buttonClass} disabled={busy} onClick={() => { reset(); setRollback(operation.operation); void run(async () => { const result = await configurationApi.rollbackPreview({ operation: operation.operation, credential_policy: [] }); if (alive.current) { setPreview(result); setRollback(operation.operation); setReady(false) } }) }}>{tr("Preview logical rollback", "预览逻辑回滚")}</button>}
    </div>}
    <p className="text-xs text-muted-foreground">{tr("Logical rollback requires an eligible committed import with no intervening edits. It creates a new revision from sanitized metadata. It never restores historical keys or an old database; keys may need re-entry. JSON, recursive and uncertain rollback are unsupported.", "逻辑回滚需要合格的已提交导入且没有后续编辑。它基于脱敏元数据创建新修订，永不恢复历史密钥或旧数据库，可能需要重新输入密钥。不支持 JSON、递归或不确定结果的回滚。")}</p>
  </section>
}
