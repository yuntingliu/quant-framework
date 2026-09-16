import { useState } from "react"
import { Settings2 } from "lucide-react"

import { getApiBase } from "@/lib/api"
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogTrigger } from "@/components/ui/dialog"

interface Profile { id: string; name: string; base_url: string; model: string; has_api_key?: boolean }
interface Settings { mode: string; active_id: string | null; profiles: Profile[]; environment_configured: boolean }
const fresh = (): Profile => ({ id: "provider-" + crypto.randomUUID(), name: "", base_url: "", model: "" })

export function ModelProviderSettings({ collapsed = false }: { collapsed?: boolean }) {
  const [open, setOpen] = useState(false)
  const [settings, setSettings] = useState<Settings | null>(null)
  const [profile, setProfile] = useState<Profile>(fresh)
  const [key, setKey] = useState("")
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState("")
  const local = settings?.mode === "local"
  async function request(path = "", init?: RequestInit) {
    const response = await fetch(getApiBase() + "/model-providers" + path, init)
    const value = await response.json()
    if (!response.ok) throw new Error(typeof value.detail === "string" ? value.detail : "Check the provider settings.")
    return value
  }
  function select(row?: Profile) {
    setProfile(row || fresh())
    setKey("")
    setMessage("")
  }
  async function show(value: boolean) {
    setOpen(value)
    setKey("")
    if (!value) return
    setBusy(true)
    try {
      const data: Settings = await request()
      setSettings(data)
      select(data.profiles.find(row => row.id === data.active_id))
    } catch (error) { setMessage(error instanceof Error ? error.message : "Could not load settings.") }
    finally { setBusy(false) }
  }
  async function submit(test: boolean) {
    setBusy(true)
    setMessage("")
    try {
      const result = await request(test ? "/test" : "", {
        method: test ? "POST" : "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ id: profile.id, name: profile.name, base_url: profile.base_url, model: profile.model, api_key: key, activate: true }),
      })
      if (test) setMessage(result.message)
      else {
        setSettings(result)
        setProfile(result.profiles.find((row: Profile) => row.id === profile.id))
        setKey("")
        setMessage("Saved. New Agent runs use this provider; active runs keep their current provider.")
        window.dispatchEvent(new Event("alphalab:model-provider-changed"))
      }
    } catch (error) { setMessage(error instanceof Error ? error.message : "Provider request failed.") }
    finally { setBusy(false) }
  }
  const inputClass = "mt-1 w-full rounded border border-border bg-background px-3 py-2 text-sm"
  return (
    <Dialog open={open} onOpenChange={value => void show(value)}>
      <DialogTrigger asChild>
        <button type="button" title="Model providers" aria-label="Model providers"
          className={"flex h-9 w-full items-center gap-2 rounded text-xs text-muted-foreground hover:bg-muted/40 hover:text-foreground " + (collapsed ? "justify-center" : "px-2")}>
          <Settings2 className="h-4 w-4 shrink-0" />
          {!collapsed && <span>Model providers</span>}
        </button>
      </DialogTrigger>
      <DialogContent className="max-h-[90vh] max-w-xl overflow-y-auto">
        <DialogHeader><DialogTitle>Model providers</DialogTitle></DialogHeader>
        <p className="text-sm text-muted-foreground">Connect the local Agent to an OpenAI-compatible Chat Completions endpoint. API keys stay in the backend credential store.</p>
        {settings && !local && <p className="rounded border border-amber-500/30 p-3 text-sm">This workstation is using {settings.mode} Agent mode. Start AlphaLab with <code>--agent local</code> to manage its providers here.</p>}
        <label className="text-sm">Saved provider
          <select className={inputClass} value={settings?.profiles.some(row => row.id === profile.id) ? profile.id : ""}
            disabled={busy} onChange={event => select(settings?.profiles.find(row => row.id === event.target.value))}>
            <option value="">Add a provider</option>
            {settings?.profiles.map(row => <option key={row.id} value={row.id}>{row.name}{row.id === settings.active_id ? " (active)" : ""}</option>)}
          </select>
        </label>
        {settings?.active_id === "environment" && <p className="text-xs text-muted-foreground">The Agent currently uses the model configured in the process environment. Saving activates this profile instead.</p>}
        <form className="space-y-3" onSubmit={event => { event.preventDefault(); void submit(false) }}>
          <fieldset disabled={busy || !local} className="space-y-3 disabled:opacity-60">
            <label className="block text-sm">Display name
              <input className={inputClass} required maxLength={100} value={profile.name}
                onChange={event => setProfile({ ...profile, name: event.target.value })} placeholder="My research model" />
            </label>
            <div className="flex flex-wrap gap-2 text-xs">
              <button type="button" className="rounded border px-2 py-1" onClick={() => { setProfile({ ...profile, name: "OpenRouter", base_url: "https://openrouter.ai/api/v1" }); setKey("") }}>OpenRouter</button>
              <button type="button" className="rounded border px-2 py-1" onClick={() => { setProfile({ ...profile, name: "Local model", base_url: "http://127.0.0.1:11434/v1" }); setKey("") }}>Local endpoint</button>
              <span className="self-center text-muted-foreground">or enter another compatible provider below</span>
            </div>
            <label className="block text-sm">API base URL
              <input className={inputClass} type="url" required value={profile.base_url}
                onChange={event => { setProfile({ ...profile, base_url: event.target.value }); setKey("") }} placeholder="https://provider.example/v1" />
            </label>
            <label className="block text-sm">Model ID
              <input className={inputClass} required value={profile.model} onChange={event => setProfile({ ...profile, model: event.target.value })} placeholder="Exact model ID from your provider" />
            </label>
            <label className="block text-sm">API key
              <input className={inputClass} type="password" autoComplete="new-password" value={key}
                onChange={event => setKey(event.target.value)} placeholder={profile.has_api_key ? "Stored on the server; leave blank to keep it" : "Required for a remote provider"} />
            </label>
            <p className="text-xs text-muted-foreground">Use a model that supports tool calling. Native Anthropic and Gemini endpoints require a compatible gateway. Loopback model servers can omit an API key.</p>
            <div className="flex flex-wrap gap-2">
              <button type="button" className="rounded border px-3 py-2 text-sm" onClick={() => void submit(true)}>Test connection</button>
              <button type="submit" className="rounded bg-primary px-3 py-2 text-sm text-primary-foreground">Save and use</button>
            </div>
            <p className="text-xs text-muted-foreground">Test connection sends a short model request and may incur a small provider charge.</p>
          </fieldset>
        </form>
        <p role="status" aria-live="polite" className="text-sm">{busy ? "Working…" : message}</p>
      </DialogContent>
    </Dialog>
  )
}
