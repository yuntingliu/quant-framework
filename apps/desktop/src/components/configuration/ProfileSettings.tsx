import { useEffect, useRef, useState } from "react"
import { configurationApi, errorCode, type Profile, type Service, type Settings } from "@/lib/configuration-api"
import { createModelProviderId } from "@/lib/modelProviderId"
import { buttonClass, ConfigurationNotice, controlClass, Field, useConfigurationText } from "./ConfigurationControls"

const fresh = (service: Service): Profile => ({ id: createModelProviderId(), name: "", ...(service === "models" ? { base_url: "", model: "" } : service === "search" ? { provider: "off", model_profile_id: null } : { user: "", host: "rqdatad-pro.ricequant.com" }) })

export function ProfileSettings({ service, onSaved }: { service: Service; onSaved: () => void }) {
  const tr = useConfigurationText()
  const [settings, setSettings] = useState<Settings | null>(null)
  const [models, setModels] = useState<Profile[]>([])
  const [profile, setProfile] = useState<Profile>(() => fresh(service))
  const [secret, setSecret] = useState("")
  const [clear, setClear] = useState(false)
  const [busy, setBusy] = useState(false)
  const [code, setCode] = useState("")
  const [saved, setSaved] = useState(false)
  const [test, setTest] = useState<"untested" | "passed" | "failed" | "disabled">("untested")
  const [environmentTest, setEnvironmentTest] = useState<"untested" | "passed" | "failed" | "disabled">("untested")
  const [dirty, setDirty] = useState(false)
  const alive = useRef(true)
  const pending = useRef(false)
  const sqlite = Boolean(settings?.revision)
  const editable = service === "rqdata" || settings?.mode === "local"
  const row = settings?.profiles.find(item => item.id === profile.id)
  const hasSecret = Boolean(row?.has_api_key || row?.has_password || row?.has_brave_api_key)
  const selected = settings?.selection ?? (settings?.source !== "environment" && settings?.profiles.some(item => item.id === settings.active_id) ? { kind: "profile", id: settings.active_id! } : { kind: "environment" })

  function choose(value: Profile) {
    setProfile(value); setSecret(""); setClear(false); setTest("untested"); setSaved(false); setDirty(false); setCode("")
  }
  async function load() {
    if (pending.current) return
    pending.current = true; setBusy(true); setCode("")
    try {
      const [value, modelSettings] = await Promise.all([configurationApi.read(service), service === "search" ? configurationApi.read("models") : Promise.resolve(null)])
      if (!alive.current) return
      setSettings(value); setModels(modelSettings?.profiles ?? []); setEnvironmentTest("untested")
      choose(value.source === "environment" ? fresh(service) : value.profiles.find(item => item.id === value.active_id) ?? fresh(service))
    } catch (error) { if (alive.current) setCode(errorCode(error)) }
    finally { pending.current = false; if (alive.current) setBusy(false) }
  }
  useEffect(() => {
    alive.current = true
    void load()
    return () => { alive.current = false }
    // Sections are mounted for one service; changing tabs discards private input.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [service])

  function edit(change: Partial<Profile>) {
    setProfile(value => ({ ...value, ...change })); setDirty(true); setTest("untested"); setSaved(false); setCode("")
    if ("base_url" in change || "model" in change || "provider" in change || "model_profile_id" in change || "user" in change || "host" in change) setSecret("")
  }
  function payload(activate: boolean) {
    const common = { id: profile.id, name: profile.name, activate, ...(sqlite ? { expected_revision: settings!.revision } : {}) }
    return service === "models" ? { ...common, base_url: profile.base_url, model: profile.model, api_key: secret, ...(sqlite ? { clear_api_key: clear } : {}) }
      : service === "search" ? { ...common, provider: profile.provider, model_profile_id: profile.provider === "openrouter" ? profile.model_profile_id ?? null : null, brave_api_key: profile.provider === "brave" ? secret : "", clear_brave_api_key: clear }
        : { ...common, user: profile.user, host: profile.host, password: secret, clear_password: clear }
  }
  async function perform(action: "save" | "test" | "select" | "environment-test", activate = false, kind: "profile" | "none" = "profile") {
    if (pending.current || !settings) return
    if (action === "environment-test" && (service !== "search" || sqlite || !editable || selected.kind !== "environment" || !settings.provider)) return
    pending.current = true; setBusy(true); setCode(""); setSaved(false)
    try {
      if (action === "test" || action === "environment-test") {
        const environment = action === "environment-test"
        // This legacy envelope authorizes testing the current environment only.
        // It must never be reused for creating or saving a named profile.
        const result = await configurationApi.test(service, environment ? { provider: settings.provider, model_profile_id: settings.model_profile_id ?? null, brave_api_key: "" } : payload(false))
        if (alive.current) {
          const status = result.ok || result.connected ? service === "search" && (environment ? settings.provider : profile.provider) === "off" ? "disabled" : "passed" : "failed"
          if (environment) setEnvironmentTest(status)
          else setTest(status)
        }
      } else {
        const result = action === "select"
          ? await configurationApi.select(service, { kind, ...(kind === "profile" ? { id: profile.id } : {}), expected_revision: settings.revision })
          : await configurationApi.save(service, payload(activate))
        if (!alive.current) return
        setSettings(result); setTest("untested"); setEnvironmentTest("untested")
        if (action === "save") {
          setProfile(result.profiles.find(item => item.id === profile.id) ?? profile)
          setSecret(""); setClear(false); setDirty(false); setSaved(true)
        }
        onSaved()
        if (service === "models") window.dispatchEvent(new Event("alphalab:model-provider-changed"))
      }
    } catch (error) { if (alive.current) { setCode(errorCode(error)); if (action === "test") setTest("failed"); if (action === "environment-test") setEnvironmentTest("failed") } }
    finally { pending.current = false; if (alive.current) setBusy(false) }
  }
  const secretNeeded = service !== "search" || profile.provider === "brave"
  const testRQ = service !== "rqdata" || (!dirty && !secret && !clear && (Boolean(row) && selected?.kind === "profile" && selected.id === profile.id || selected?.kind === "environment" && settings?.configured === true))
  return <section className="grid gap-3" aria-label={service} aria-busy={busy}>
    <p className="text-sm text-muted-foreground">{service === "models" ? tr("Models power Agent responses. Local endpoints may not need a key.", "模型用于 Agent 回复。本地端点可能不需要密钥。") : service === "search" ? tr("Choose the provider used by explicit web searches.", "选择网页搜索使用的服务商。") : tr("RQData supplies research datasets. Credentials are captured separately for each admitted job.", "RQData 提供研究数据集。每个已接纳任务独立捕获凭据。")}</p>
    {code && <ConfigurationNotice code={code} />}
    <button className={buttonClass + " justify-self-start"} onClick={() => void load()} disabled={busy}>{tr("Reload settings", "重新加载设置")}</button>
    {settings && <>
      <p role="status" className="text-sm">{tr("Current selection", "当前选择")}: {selected?.kind === "none" ? tr("Disabled / unselected; no environment fallback", "已禁用 / 未选择；不回退到环境") : selected?.kind === "environment" ? tr("Environment fallback (values remain private)", "环境回退（值保持私密）") : settings.profiles.find(item => item.id === selected?.id)?.name ?? selected?.id}. {settings.configured === false ? tr("Missing required settings", "缺少必需设置") : settings.configured === true ? tr("Configured; connectivity requires an explicit test.", "已配置；连接性需要明确请求测试。") : tr("Saved profiles loaded; connectivity is not verified.", "已加载保存的配置；连接性尚未验证。")}</p>
      {service === "search" && selected.kind === "environment" && <div className="grid gap-2 text-sm" role="group" aria-label={tr("Current environment search", "当前环境搜索")}>
        <p>{tr("Current environment provider", "当前环境服务商")}: {settings.provider ?? tr("Unavailable", "不可用")}. {tr("Testing does not save environment credentials or change the new-profile editor below.", "测试不会保存环境凭据，也不会更改下方的新配置编辑器。")}</p>
        <button className={buttonClass} disabled={busy || sqlite || !editable || !settings.provider || (!settings.configured && settings.provider !== "off")} onClick={() => void perform("environment-test")}>{tr("Test current environment", "测试当前环境")}</button>
        {sqlite && <p>{tr("Current-environment search testing is unavailable for this SQLite backend. No JSON fallback is used.", "此 SQLite 后端不支持当前环境搜索测试，不会回退到 JSON。")}</p>}
        <p role="status">{environmentTest === "untested" ? tr("Current environment has not been tested in this session.", "本次会话尚未测试当前环境。") : environmentTest === "disabled" ? tr("Current environment search is Off; no provider request was sent.", "当前环境搜索已关闭，未发送服务商请求。") : environmentTest === "passed" ? tr("Current environment search test passed.", "当前环境搜索测试通过。") : tr("Current environment search test failed.", "当前环境搜索测试失败。")}</p>
      </div>}
      {!editable && <ConfigurationNotice code="UNAVAILABLE" />}
      <Field label={tr("Saved profiles", "已保存的配置")}><select className={controlClass} value={row ? profile.id : ""} disabled={busy} onChange={event => choose(settings.profiles.find(item => item.id === event.target.value) ?? fresh(service))}>
        <option value="">{tr("New profile", "新建配置")}</option>
        {settings.profiles.map(item => <option key={item.id} value={item.id}>{item.name}{item.id === selected?.id ? tr(" (selected)", "（已选择）") : ""}</option>)}
      </select></Field>
      <fieldset disabled={busy || !editable} className="grid gap-3">
        <Field label={tr("Profile name", "配置名称")}><input className={controlClass} value={profile.name} maxLength={100} onChange={event => edit({ name: event.target.value })} /></Field>
        {service === "models" && <>
          <div className="flex flex-wrap gap-2"><button className={buttonClass} onClick={() => edit({ base_url: "https://openrouter.ai/api/v1" })}>OpenRouter</button><button className={buttonClass} onClick={() => edit({ base_url: "http://127.0.0.1:11434/v1" })}>{tr("Local endpoint", "本地端点")}</button></div>
          <Field label={tr("API base URL", "API 基础 URL")}><input className={controlClass} value={profile.base_url ?? ""} maxLength={2000} onChange={event => edit({ base_url: event.target.value })} /></Field>
          <Field label={tr("Model ID", "模型 ID")}><input className={controlClass} value={profile.model ?? ""} maxLength={200} onChange={event => edit({ model: event.target.value })} /></Field>
        </>}
        {service === "search" && <>
          <Field label={tr("Search provider", "搜索服务商")}><select className={controlClass} value={profile.provider} onChange={event => edit({ provider: event.target.value })}><option value="off">{tr("Off", "关闭")}</option><option value="openrouter">OpenRouter</option><option value="brave">Brave</option></select></Field>
          {profile.provider === "openrouter" && <Field label={tr("Search model profile", "搜索模型配置")}><select className={controlClass} value={profile.model_profile_id ?? ""} onChange={event => edit({ model_profile_id: event.target.value || null })}>
            <option value="">{tr("Selected model (authorized reference)", "所选模型（授权引用）")}</option>
            {profile.model_profile_id && !models.some(item => item.id === profile.model_profile_id) && <option value={profile.model_profile_id}>{tr("Missing reference", "引用缺失")}: {profile.model_profile_id}</option>}
            {models.map(item => <option key={item.id} value={item.id}>{item.name}{!item.has_api_key ? tr(" — missing key", " — 缺少密钥") : ""}</option>)}
          </select></Field>}
          {profile.provider === "openrouter" && <p className="text-xs text-muted-foreground">{tr("The referenced model must be OpenRouter-compatible. Invalid references are rejected; no alternative is selected automatically.", "引用的模型必须兼容 OpenRouter。无效引用会被拒绝，不会自动选择替代配置。")}</p>}
        </>}
        {service === "rqdata" && <>
          <Field label={tr("RQData username", "RQData 用户名")}><input className={controlClass} autoComplete="off" value={profile.user ?? ""} maxLength={200} onChange={event => edit({ user: event.target.value })} /></Field>
          <Field label={tr("RQData host", "RQData 主机")}><input className={controlClass} value={profile.host ?? ""} maxLength={255} onChange={event => edit({ host: event.target.value })} /></Field>
        </>}
        {secretNeeded && <>
          <Field label={service === "rqdata" ? tr("Password", "密码") : tr("API key", "API 密钥")}><input className={controlClass} type="password" autoComplete="new-password" value={secret} disabled={clear} maxLength={8192} onChange={event => { setSecret(event.target.value); setDirty(true); setSaved(false); setTest("untested") }} /></Field>
          <p className="text-xs text-muted-foreground">{hasSecret ? tr("A credential is saved; it is never displayed. Blank retains it only for the same identity allowed by the backend. Destination changes require review.", "已保存凭据，但永不显示。留空仅在后端允许的相同身份下保留。更改目标需要重新检查。") : tr("No saved credential for this profile. Supply one when required; another profile's key will not be borrowed.", "此配置没有保存的凭据。需要时请输入，不会借用其他配置的密钥。")}</p>
        </>}
          {(sqlite || service !== "models") ? <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={clear} onChange={event => { setClear(event.target.checked); setSecret(""); setDirty(true); setSaved(false); setTest("untested") }} />{tr("Explicitly clear saved credential", "明确清除已保存凭据")}</label> : <p className="text-xs">{tr("Explicit model key deletion is unsupported by the JSON backend.", "JSON 后端不支持明确删除模型密钥。")}</p>}
        <div className="flex flex-wrap gap-2">
          <button className={buttonClass} onClick={() => void perform("save")}>{tr("Save profile", "保存配置")}</button>
          <button className={buttonClass} onClick={() => void perform("save", true)}>{tr("Save and use", "保存并使用")}</button>
          {sqlite && <button className={buttonClass} disabled={!row || dirty} onClick={() => void perform("select")}>{tr("Use saved profile", "使用已保存配置")}</button>}
          {sqlite && <button className={buttonClass} onClick={() => void perform("select", false, "none")}>{tr("Disable / select none", "禁用 / 不选择")}</button>}
          <button className={buttonClass} disabled={!testRQ} onClick={() => void perform("test")}>{tr("Test connection", "测试连接")}</button>
        </div>
      </fieldset>
      {!sqlite && <p className="text-xs text-muted-foreground">{tr("JSON: use Save and use to select a profile. Explicit none/environment selection is unavailable; existing fallback rules remain in effect.", "JSON：使用“保存并使用”选择配置。无法明确选择 none/environment；保留现有回退规则。")}</p>}
      {service === "rqdata" && <p className="text-xs text-muted-foreground">{tr("RQData tests only the saved default profile, not unsaved fields. Save and use first, then press Test connection.", "RQData 仅测试已保存的默认配置，而非未保存字段。请先保存并使用，再点击测试连接。")}</p>}
      <p className="text-xs text-muted-foreground">{tr("Only explicit connection tests send provider requests and may incur charges. No test runs on save or import.", "仅明确请求的连接测试会发送服务商请求，可能产生费用。保存或导入不会触发测试。")}</p>
      {dirty && <p role="status" className="text-sm">{tr("Unsaved profile edits. Selection changes do not save these fields.", "配置修改尚未保存。更改选择不会保存这些字段。")}</p>}
      <p role="status" className="text-sm">{saved && tr("Saved. ", "已保存。")}{test === "untested" ? tr("Connection not tested in this session.", "本次会话尚未测试连接。") : test === "disabled" ? tr("Search Off verified; no provider request was sent.", "已验证搜索关闭；未发送服务商请求。") : test === "passed" ? tr("Explicit connection test passed; future availability is not guaranteed.", "明确请求的连接测试通过；不保证未来可用性。") : tr("Connection test failed.", "连接测试失败。")}</p>
    </>}
  </section>
}
