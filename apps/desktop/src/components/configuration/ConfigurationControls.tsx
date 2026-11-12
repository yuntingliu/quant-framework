import { useLanguage } from "@/contexts/LanguageContext"
import type { ReactNode } from "react"
import { useEffect, useRef, useState } from "react"
import { configurationApi, errorCode, type Operation } from "@/lib/configuration-api"

// Owned by the always-mounted settings entry, not by a tab or dialog. Only
// public operation references/status survive dismissal; private form inputs do not.
export function useConfigurationOperation(onApplied: () => void) {
  const [operation, setOperation] = useState<Operation | null>(null)
  const [busy, setBusy] = useState(false)
  const [code, setCode] = useState("")
  const pending = useRef(false)
  const alive = useRef(true)
  const notified = useRef("")
  const callback = useRef(onApplied)
  useEffect(() => { callback.current = onApplied }, [onApplied])
  useEffect(() => { alive.current = true; return () => { alive.current = false } }, [])

  function accept(result: Operation) {
    if (!alive.current) return
    setOperation(result)
    const publication = `${result.kind}:${result.operation}:${result.revision}`
    if (result.state === "committed" && result.revision && notified.current !== publication) {
      notified.current = publication
      callback.current()
    }
  }
  async function inspect(identifier: string) {
    if (pending.current) return
    pending.current = true; setBusy(true); setCode("")
    try { accept(await configurationApi.operation(identifier)) }
    catch (error) { if (alive.current) setCode(errorCode(error)) }
    finally { pending.current = false; if (alive.current) setBusy(false) }
  }
  async function apply(body: { receipt: string }, kind: Operation["kind"]) {
    if (pending.current || operation?.state === "uncertain") return
    pending.current = true; setBusy(true); setCode("")
    setOperation({ operation: body.receipt, kind, state: "uncertain", revision: null })
    try {
      accept(await configurationApi.apply(body, kind === "rollback"))
      // Inspection determines current rollback eligibility. It never replays apply.
      accept(await configurationApi.operation(body.receipt))
    } catch (error) { if (alive.current) setCode(errorCode(error)) }
    finally { pending.current = false; if (alive.current) setBusy(false) }
  }
  return { operation, busy, code, apply, inspect }
}
export type ConfigurationOperation = ReturnType<typeof useConfigurationOperation>

export function useConfigurationText() {
  const { language } = useLanguage()
  return (en: string, zh: string) => language === "zh" ? zh : en
}
export const controlClass = "w-full rounded-md border border-input bg-background px-3 py-2 text-sm disabled:opacity-50"
export const buttonClass = "rounded-md border border-input px-3 py-2 text-sm hover:bg-accent focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50"
export function Field({ label, children }: { label: string; children: ReactNode }) {
  return <label className="grid gap-1 text-sm">{label}{children}</label>
}
export function ConfigurationNotice({ code }: { code: string }) {
  const tr = useConfigurationText()
  const messages: Record<string, [string, string]> = {
    REVISION_CONFLICT: ["Settings changed elsewhere. Reload, review your edits and save again.", "设置已在其他位置更改。请重新加载并检查后保存。"],
    STALE_PREVIEW: ["The preview is stale. Start a new preview; nothing will be retried automatically.", "预览已过期。请重新预览；系统不会自动重试。"],
    RECEIPT_EXPIRED: ["The receipt expired. Start a new preview.", "凭据已过期，请重新预览。"],
    RECEIPT_USED: ["This receipt was already used. Inspect the operation before starting again.", "此凭据已使用。请先检查操作结果。"],
    HISTORY_CAPACITY: ["Private history capacity reached. Reviewed offline maintenance is required.", "私有历史容量已满，需要经审核的离线维护。"],
    APPLICATION_UNCERTAIN: ["Outcome uncertain. Inspect the operation; do not retry this application.", "结果不确定。请检查操作，不要重试应用。"],
    UNSUPPORTED_JSON_APPLY: ["JSON installations support export and preview. Atomic import and rollback require an offline-prepared SQLite installation.", "JSON 安装支持导出和预览。原子导入与回滚需要离线准备的 SQLite 安装。"],
    ROLLBACK_CONFLICT: ["Rollback is unavailable after intervening edits or an uncertain operation.", "存在后续修改或操作结果不确定，无法回滚。"],
    LOCAL_AUTHORIZATION_REQUIRED: ["Use an authenticated local API session. This origin is not authorized for configuration transfer.", "请使用经过身份验证的本地 API 会话。此来源无权传输配置。"],
    INVALID_CONFIGURATION: ["Check the fields, references, credential policy and file format. Settings were not accepted.", "请检查字段、引用、凭据策略及文件格式。设置未被接受。"],
    MISSING_CONSENT: ["Review and accept every required consent before confirming.", "确认前请检查并同意所有必需事项。"],
    CREDENTIAL_DECISION_REQUIRED: ["Choose a credential policy for every imported profile, then refresh the preview.", "请为每个导入配置选择凭据策略，然后刷新预览。"],
    ENVIRONMENT_SELECTION_UNSUPPORTED: ["This SQLite import cannot select environment fallback. Use a portable profile or none selection.", "此 SQLite 导入不能选择环境回退。请使用可移植配置或 none 选择。"],
    UNSUPPORTED_NATIVE_FIELDS: ["This service has native extension fields that cannot be replaced through portable transfer.", "此服务包含无法通过可移植传输替换的原生扩展字段。"],
    FILE_TOO_LARGE: ["Choose a UTF-8 JSON file no larger than 256 KiB.", "请选择不超过 256 KiB 的 UTF-8 JSON 文件。"],
    UNAVAILABLE: ["Service or local Agent unavailable. Check startup and reload; connection has not been verified.", "服务或本地 Agent 不可用。请检查启动状态并重新加载；连接尚未验证。"],
  }
  const message = messages[code] ?? messages.INVALID_CONFIGURATION!
  return <p role="alert" className="text-sm text-destructive">{tr(...message)}</p>
}
