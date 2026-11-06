import { useEffect, useState } from "react"
import { getApiBase } from "@/lib/api"
import { useLanguage } from "@/contexts/LanguageContext"

interface Profile { id: string; name: string; base_url: string }
interface SearchSettings {
  provider: "openrouter" | "brave" | "off"
  model_profile_id: string | null
  has_brave_api_key: boolean
  configured: boolean
}
interface Source { title: string; url: string }

export function SearchProviderSettings({ open, local, profiles }: { open: boolean; local: boolean; profiles: Profile[] }) {
  const { language } = useLanguage()
  const tr = (en: string, zh: string) => language === "zh" ? zh : en
  const [settings, setSettings] = useState<SearchSettings | null>(null)
  const [key, setKey] = useState("")
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState("")
  const [sources, setSources] = useState<Source[]>([])
  const inputClass = "mt-1 w-full rounded border border-border bg-background px-3 py-2 text-sm"

  useEffect(() => {
    if (!open) return
    let cancelled = false
    setBusy(true)
    setKey("")
    setMessage("")
    setSources([])
    fetch(getApiBase() + "/search-providers")
      .then(async response => {
        const data = await response.json()
        if (!response.ok) throw new Error(data.detail || "Could not load search settings.")
        if (!cancelled) setSettings(data)
      })
      .catch(error => { if (!cancelled) setMessage(error instanceof Error ? error.message : "Could not load search settings.") })
      .finally(() => { if (!cancelled) setBusy(false) })
    return () => { cancelled = true }
  }, [open])

  async function submit(test: boolean) {
    if (!settings) return
    setBusy(true)
    setMessage("")
    setSources([])
    try {
      const response = await fetch(getApiBase() + "/search-providers" + (test ? "/test" : ""), {
        method: test ? "POST" : "PUT", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ provider: settings.provider, model_profile_id: settings.model_profile_id, brave_api_key: key }),
      })
      const data = await response.json()
      if (!response.ok) throw new Error(data.detail || "Check the search settings.")
      if (test) {
        setMessage(data.ok ? (settings.provider === "off" ? tr("Web search is disabled. No request was sent.", "网页搜索已关闭，没有发送搜索请求。") : tr("Search connection succeeded.", "搜索连接成功。")) : data.message)
        setSources(data.results || [])
      } else {
        setSettings(data)
        setKey("")
        setMessage(tr("Saved. Each run keeps the settings selected at its first search.", "已保存。每个任务在首次搜索时选定配置，并在该任务中保持不变。"))
      }
    } catch (error) { setMessage(error instanceof Error ? error.message : "Search settings request failed.") }
    finally { setBusy(false) }
  }

  return (
    <section aria-label={tr("Web search settings", "网页搜索设置")}>
      <h3 className="text-sm font-semibold">{tr("Web search", "网页搜索")}</h3>
      <p className="mt-1 text-xs text-muted-foreground">{tr("Choose search independently of the research model. Provider search fees may apply.", "搜索服务可以独立于研究模型选择，服务商可能另收搜索费用。")}</p>
      {settings && <form className="mt-3 space-y-3" onSubmit={event => { event.preventDefault(); void submit(false) }}>
        <fieldset disabled={busy || !local} className="space-y-3 disabled:opacity-60">
          <label className="block text-sm">{tr("Search provider", "搜索服务")}
            <select className={inputClass} value={settings.provider} onChange={event => { setSettings({ ...settings, provider: event.target.value as SearchSettings["provider"] }); setKey(""); setMessage(""); setSources([]) }}>
              <option value="openrouter">OpenRouter</option>
              <option value="brave">Brave Search</option>
              <option value="off">{tr("Off", "关闭网页搜索")}</option>
            </select>
          </label>
          {settings.provider === "openrouter" && <>
            <label className="block text-sm">{tr("OpenRouter profile for search", "搜索使用的 OpenRouter 配置")}
              <select className={inputClass} value={settings.model_profile_id || ""} onChange={event => setSettings({ ...settings, model_profile_id: event.target.value || null })}>
                <option value="">{tr("Use the active model profile", "沿用当前模型配置")}</option>
                {profiles.filter(row => row.base_url.replace(/\/+$/, "") === "https://openrouter.ai/api/v1").map(row => <option key={row.id} value={row.id}>{row.name}</option>)}
              </select>
            </label>
            <p className="text-xs text-muted-foreground">{tr("Uses the selected profile's saved key. No Brave key is needed. Add an OpenRouter profile above to use a separate search account.", "复用所选配置中已保存的密钥，无需 Brave 密钥。若要单独使用一个搜索账户，可先在上方添加 OpenRouter 配置。")}</p>
          </>}
          {settings.provider === "brave" && <label className="block text-sm">Brave Search API key
            <input className={inputClass} type="password" autoComplete="new-password" value={key} onChange={event => setKey(event.target.value)}
              placeholder={settings.has_brave_api_key ? tr("Stored on this workstation; leave blank to keep it", "已保存在本工作站，留空保留") : tr("Enter a Brave Search key", "填写 Brave Search 密钥")} />
          </label>}
          {settings.provider === "off" && <p className="text-xs text-muted-foreground">{tr("The Agent can still use local research tools. No web search requests will be sent.", "Agent 仍可使用本地研究工具，不会发送网页搜索请求。")}</p>}
          <div className="flex flex-wrap gap-2">
            <button type="button" className="rounded border px-3 py-2 text-sm" onClick={() => void submit(true)}>{tr("Test search", "测试搜索")}</button>
            <button type="submit" className="rounded bg-primary px-3 py-2 text-sm text-primary-foreground">{tr("Save search", "保存搜索设置")}</button>
          </div>
          <p className="text-xs text-muted-foreground">{tr("Test search sends a public documentation query and may incur provider charges. It does not save changes.", "测试会搜索一条公共文档查询，可能产生服务商费用，不会保存设置。")}</p>
        </fieldset>
      </form>}
      <p role="status" aria-live="polite" className="mt-2 text-sm">{busy ? tr("Working…", "处理中…") : message}</p>
      {sources.length > 0 && <ul className="mt-2 space-y-1 text-xs">{sources.map(source => <li key={source.url}><a className="text-primary underline" href={source.url} target="_blank" rel="noreferrer">{source.title || source.url}</a></li>)}</ul>}
    </section>
  )
}
