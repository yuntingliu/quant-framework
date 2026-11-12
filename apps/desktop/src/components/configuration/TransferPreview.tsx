import type { Preview } from "@/lib/configuration-api"
import { services } from "@/lib/configuration-api"
import { ConfigurationNotice, useConfigurationText } from "./ConfigurationControls"

export function TransferPreview({ preview }: { preview: Preview }) {
  const tr = useConfigurationText()
  const groups = [["added", "Added", "新增"], ["changed", "Changed", "修改"], ["unchanged", "Unchanged", "不变"], ["removed", "Removed", "删除"]] as const
  return <div className="grid gap-3 rounded-md border border-border p-3" aria-label={tr("Server preview", "服务器预览")}>
    <p className="text-sm">{tr("Backend", "后端")}: {preview.backend}. {tr("Preview does not apply settings or test connections.", "预览不应用设置，也不测试连接。")}</p>
    {services.map(service => {
      const change = preview.changes[service]
      return <section key={service} className="grid gap-1 text-sm"><h3 className="font-medium">{service}</h3>
        {groups.map(([key, en, zh]) => <p key={key}>{tr(en, zh)}: {change[key].join(", ") || "—"}</p>)}
        <p>{tr("Selection", "选择")}: {change.selection_before.kind}{change.selection_before.id ? ` (${change.selection_before.id})` : ""} → {change.selection_after.kind}{change.selection_after.id ? ` (${change.selection_after.id})` : ""}</p>
        <p className="text-xs text-muted-foreground">{preview.selection_explanations[service].kind === "none" ? tr("No environment fallback.", "不回退到环境。") : preview.selection_explanations[service].kind === "environment" ? tr("Intent only; no environment values imported.", "仅选择意图；不导入环境值。") : tr("Saved profile only; missing keys require configuration.", "仅使用保存的配置；缺少密钥需要配置。")}</p>
        {preview.configuration[service].profiles.map(row => <dl key={row.id} className="grid grid-cols-[auto_1fr] gap-x-3 break-all rounded border border-border p-2 text-xs">{(["id", "name", "base_url", "model", "provider", "model_profile_id", "user", "host"] as const).filter(key => key in row).map(key => <div key={key} className="contents"><dt>{key}</dt><dd>{String(row[key] ?? "—")}</dd></div>)}</dl>)}
      </section>
    })}
    {preview.missing_credentials.length > 0 && <div role="status"><h3>{tr("Missing credentials / private input required", "缺少凭据 / 需要私密输入")}</h3>{preview.missing_credentials.map(row => <p key={row.service + row.id} className="text-sm">{row.service} / {row.id}: {row.code}</p>)}</div>}
    {preview.migrations.length > 0 && <p className="text-sm">{tr("Schema migrations", "结构迁移")}: {preview.migrations.join(", ")}</p>}
    {preview.diagnostics.map(row => <ConfigurationNotice key={row.code} code={row.code} />)}
  </div>
}
