import assert from "node:assert/strict"
import { afterEach, beforeEach, test } from "node:test"
import React, { act } from "react"
import { createRoot } from "react-dom/client"
import { LanguageProvider } from "../src/contexts/LanguageContext.tsx"
import { ModelProviderSettings } from "../src/components/ModelProviderSettings.tsx"
import { ProfileSettings } from "../src/components/configuration/ProfileSettings.tsx"
import { ConfigurationTransfer } from "../src/components/configuration/ConfigurationTransfer.tsx"
import { configurationApi } from "../src/lib/configuration-api.ts"
import { usePublishedAgent } from "../src/hooks/usePublishedAgent.ts"
import { spawnSync } from "node:child_process"
import { fileURLToPath } from "node:url"

const SECRET = "ui-private-synthetic-credential-7391"
const revision = "1".repeat(32)
const receipt = "2".repeat(32)
const rows = {
  models: { id: "model", name: "Personal model", base_url: "https://openrouter.ai/api/v1", model: "provider/model", has_api_key: true },
  search: { id: "search", name: "Personal search", provider: "brave", model_profile_id: null, has_brave_api_key: true, configured: true },
  rqdata: { id: "rq", name: "Personal RQData", user: "synthetic-user", host: "rq.example", has_password: true },
}
let root, host, calls, handle
const reply = (value, status = 200) => new Response(JSON.stringify(value), { status, headers: { "Content-Type": "application/json" } })
const settings = service => ({ mode: "local", profiles: [rows[service]], active_id: rows[service].id, selection: { kind: "profile", id: rows[service].id }, revision, configured: true })
const basePreview = (overrides = {}) => ({
  kind: "import", backend: "sqlite", can_apply: false, revision, receipt: null,
  changes: Object.fromEntries(Object.keys(rows).map(service => [service, { added: [], changed: [rows[service].id], removed: [], unchanged: [], selection_before: { kind: "profile", id: rows[service].id }, selection_after: { kind: "none" } }])),
  configuration: Object.fromEntries(Object.keys(rows).map(service => [service, { profiles: [{ id: rows[service].id, name: rows[service].name }], selection: { kind: "none" } }])),
  credential_options: [{ service: "models", id: "model", retain_eligible: true }],
  missing_credentials: [{ service: "models", id: "model", code: "CREDENTIAL_REQUIRED" }],
  credential_actions: [], required_consents: ["APPLY_CONFIGURATION"], migrations: ["SEARCH_V1_TO_V2"], diagnostics: [],
  selection_explanations: Object.fromEntries(Object.keys(rows).map(key => [key, { kind: "none", message: "No fallback" }])), ...overrides,
})
async function flush() { await act(async () => { await new Promise(resolve => setTimeout(resolve, 0)) }) }
async function render(component) { await act(async () => root.render(React.createElement(LanguageProvider, null, component))); await flush() }
async function click(text) {
  const button = [...document.querySelectorAll("button")].find(item => item.textContent.trim() === text)
  assert.ok(button, `Missing button: ${text}`); assert.equal(button.disabled, false, `Disabled button: ${text}`)
  await act(async () => button.click()); await flush()
}
function field(label) { const element = [...document.querySelectorAll("label")].find(item => item.firstChild?.textContent === label); assert.ok(element, `Missing label: ${label}`); return element.querySelector("input,select") }
async function change(element, value) {
  await act(async () => {
    if (element.type === "checkbox") element.click()
    else { Object.getOwnPropertyDescriptor(element.tagName === "SELECT" ? window.HTMLSelectElement.prototype : window.HTMLInputElement.prototype, "value").set.call(element, value); element.dispatchEvent(new window.Event(element.tagName === "SELECT" ? "change" : "input", { bubbles: true })) }
  }); await flush()
}
async function pick(text = "{}", size = text.length) {
  const input = document.querySelector('input[type="file"]')
  Object.defineProperty(input, "files", { configurable: true, value: [{ size, text: async () => text }] })
  await act(async () => input.dispatchEvent(new window.Event("change", { bubbles: true }))); await flush()
}
beforeEach(() => {
  calls = []; localStorage.clear(); sessionStorage.clear()
  host = document.createElement("div"); document.body.append(host); root = createRoot(host)
  handle = async (url, init) => {
    const service = url.includes("model-providers") ? "models" : url.includes("search-providers") ? "search" : "rqdata"
    if (url.endsWith("/test")) return reply({ ok: true })
    if (url.endsWith("/connection-test")) return reply({ connected: true })
    if (url.endsWith("/preview")) return reply(basePreview())
    return reply(settings(service))
  }
  globalThis.fetch = async (url, init) => { calls.push({ url: String(url), ...init, body: init?.body ? JSON.parse(init.body) : undefined }); return handle(String(url), init) }
})
afterEach(async () => {
  assert.ok(!document.body.textContent.includes(SECRET))
  assert.ok(!JSON.stringify({ ...localStorage, ...sessionStorage }).includes(SECRET))
  await act(async () => root.unmount()); document.body.replaceChildren()
  window.history.replaceState({}, "", "/")
})

test("unified workspace dialog opens all four keyboard-operable sections and clears secrets on closure", async () => {
  await render(React.createElement(ModelProviderSettings))
  await click("Personal configuration")
  assert.equal(document.querySelectorAll('[role="tab"]').length, 4)
  assert.match(document.body.textContent, /Personal model/)
  await change(field("API key"), SECRET)
  await act(async () => document.dispatchEvent(new window.KeyboardEvent("keydown", { key: "Escape", bubbles: true }))); await flush()
  assert.equal(document.querySelector('[role="dialog"]'), null)
  await click("Personal configuration")
  assert.equal(field("API key").value, "")
  assert.ok(calls.every(call => call.method === "GET"))
})
for (const service of Object.keys(rows)) {
  test(`${service}: saved profiles, private blank retention, explicit testing, clearing and revision conflicts`, async () => {
    await render(React.createElement(ProfileSettings, { service, onSaved() {} }))
    assert.match(document.body.textContent, new RegExp(rows[service].name))
    assert.match(document.body.textContent, /Connection not tested/)
    const password = document.querySelector('input[type="password"]'); assert.equal(password.value, "")
    await click("Save profile")
    const save = calls.find(call => call.method === "PUT")
    assert.equal(save.body.expected_revision, revision)
    assert.equal(save.body[{ models: "api_key", search: "brave_api_key", rqdata: "password" }[service]], "")
    assert.ok(!calls.some(call => call.url.endsWith("test")))
    await click("Test connection"); assert.match(document.body.textContent, /Explicit connection test passed/)
    if (service === "rqdata") assert.deepEqual(calls.at(-1).body, { template_id: "rq.a_share_research" })
    await change(document.querySelector('input[type="checkbox"]'), true)
    assert.equal(password.disabled, true)
    await click("Save profile")
    assert.equal(calls.at(-1).body[{ models: "clear_api_key", search: "clear_brave_api_key", rqdata: "clear_password" }[service]], true)
    handle = async () => reply({ detail: "a private server body " + SECRET }, 409)
    await click("Save profile")
    assert.match(document.body.textContent, /Reload, review your edits/)
  })
}
test("new profile and destination changes discard fresh inputs; none selection is explicit", async () => {
  await render(React.createElement(ProfileSettings, { service: "models", onSaved() {} }))
  await change(field("API key"), SECRET)
  await change(field("API base URL"), "http://127.0.0.1:11434/v1")
  assert.equal(field("API key").value, "")
  await click("Disable / select none")
  assert.deepEqual(calls.at(-1).body, { kind: "none", expected_revision: revision })
  await change(field("Saved profiles"), "")
  assert.equal(field("Profile name").value, "")
  assert.equal(field("API key").value, "")
})
test("missing search reference remains selected and is never silently replaced", async () => {
  handle = async url => reply(url.includes("search") ? { ...settings("search"), profiles: [{ ...rows.search, provider: "openrouter", model_profile_id: "missing" }] } : settings("models"))
  await render(React.createElement(ProfileSettings, { service: "search", onSaved() {} }))
  assert.equal(field("Search model profile").value, "missing")
  await click("Save profile")
  assert.equal(calls.at(-1).body.model_profile_id, "missing")
})
test("JSON feature gates do not offer unsupported model clear or explicit none", async () => {
  handle = async () => reply({ ...settings("models"), revision: undefined, selection: undefined })
  await render(React.createElement(ProfileSettings, { service: "models", onSaved() {} }))
  assert.match(document.body.textContent, /deletion is unsupported/)
  assert.ok(![...document.querySelectorAll("button")].some(button => button.textContent.includes("select none")))
})
test("RQData edits cannot test unsaved passwords or a nondefault profile", async () => {
  await render(React.createElement(ProfileSettings, { service: "rqdata", onSaved() {} }))
  await change(field("Password"), SECRET)
  assert.equal([...document.querySelectorAll("button")].find(button => button.textContent === "Test connection").disabled, true)
})

for (const service of Object.keys(rows)) test(`${service}: a JSON saved profile named environment remains a saved selection`, async () => {
  handle = async () => reply({ ...settings(service), revision: undefined, selection: undefined, source: "saved", profiles: [{ ...rows[service], id: "environment" }], active_id: "environment" })
  await render(React.createElement(ProfileSettings, { service, onSaved() {} }))
  assert.equal(field("Saved profiles").value, "environment")
  assert.ok(!document.body.textContent.includes("Environment fallback (values remain private)"))
  assert.match(document.body.textContent, /Configured; connectivity requires an explicit test/)
})

test("an incomplete saved selection reports missing settings without pretending connectivity", async () => {
  handle = async () => reply({ ...settings("models"), configured: false, profiles: [{ ...rows.models, has_api_key: false }] })
  await render(React.createElement(ProfileSettings, { service: "models", onSaved() {} }))
  assert.match(document.body.textContent, /Missing required settings/)
  assert.match(document.body.textContent, /No saved credential for this profile/)
  assert.ok(calls.every(call => call.method === "GET"))
})

test("JSON environment fallback never selects an inactive profile with the same environment label", async () => {
  handle = async () => reply({ ...settings("models"), revision: undefined, selection: undefined, source: "environment", profiles: [{ ...rows.models, id: "environment" }], active_id: "environment" })
  await render(React.createElement(ProfileSettings, { service: "models", onSaved() {} }))
  assert.match(document.body.textContent, /Environment fallback \(values remain private\)/)
  assert.equal(field("Saved profiles").value, "")
  assert.equal(field("API key").value, "")
})
test("transfer header is scoped, safe failures redact bodies and preserve API origin", async () => {
  await configurationApi.read("models"); await configurationApi.export()
  assert.equal(calls[0].headers["x-alphalab-configuration-consent"], undefined)
  assert.equal(calls[1].headers["x-alphalab-configuration-consent"], "local-configuration-transfer")
  assert.equal(calls[1].url, "/api/configuration-transfer/export")
  handle = async () => { throw new Error(SECRET) }
  await assert.rejects(configurationApi.export(), error => !String(error).includes(SECRET) && !error.cause)
})

test("transfer refuses nonlocal or credential-bearing API origins before fetching", async () => {
  for (const base of ["https://example.org/api", "http://invalid:blocked@localhost:8000/api", "http://localhost:8000/api?other=1", "javascript:invalid"]) {
    window.history.replaceState({}, "", "/?alphalabApiBase=" + encodeURIComponent(base))
    await assert.rejects(configurationApi.preview({ configuration: "{}" }), error => error.code === "LOCAL_AUTHORIZATION_REQUIRED" && !error.cause)
  }
  assert.equal(calls.length, 0)
})
test("export downloads exact canonical server content; failure never creates an empty file", async () => {
  let downloaded, revoked = 0
  const canonical = '{"format":"portable-metadata"}\n'
  URL.createObjectURL = blob => { downloaded = blob; return "blob:synthetic" }; URL.revokeObjectURL = () => { revoked++ }
  const original = window.HTMLAnchorElement.prototype.click
  window.HTMLAnchorElement.prototype.click = function () { assert.equal(this.download, "alphalab-configuration.json") }
  try {
    handle = async () => new Response(canonical)
    await render(React.createElement(ConfigurationTransfer, { onApplied() {} }))
    await click("Export sanitized JSON"); assert.equal(await downloaded.text(), canonical)
    downloaded = null; handle = async () => reply({ detail: SECRET }, 503)
    await click("Export sanitized JSON"); assert.equal(downloaded, null)
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 1050)) }); assert.equal(revoked, 1)
  } finally { window.HTMLAnchorElement.prototype.click = original }
})
test("invalid and oversized import stays in memory and cannot be applied", async () => {
  await render(React.createElement(ConfigurationTransfer, { onApplied() {} }))
  await pick("{}", 262145); assert.match(document.body.textContent, /256 KiB/); assert.equal(calls.length, 0)
  handle = async () => reply({ detail: { code: "INVALID_CONFIGURATION", message: SECRET } }, 422)
  await pick("{bad JSON"); await click("Preview import")
  assert.equal(calls.at(-1).body.configuration, "{bad JSON")
  assert.match(document.body.textContent, /not accepted/)
  assert.ok(!document.body.textContent.includes("Confirm import"))
})
test("actual server diff, credential eligibility, explicit consent and one-shot private apply", async () => {
  let applied = 0
  handle = async (url, init) => {
    if (url.endsWith("/preview")) return reply(basePreview(init.body.includes('"action"') ? { can_apply: true, receipt } : {}))
    if (url.endsWith("/apply")) { applied++; return reply({ operation: receipt, state: "committed", revision, kind: "import" }) }
    return reply({ operation: receipt, state: "committed", revision, current_revision: revision, kind: "import", rollback_available: true })
  }
  await render(React.createElement(ConfigurationTransfer, { onApplied() {} }))
  await pick(); await click("Preview import")
  assert.match(document.body.textContent, /SEARCH_V1_TO_V2/); assert.match(document.body.textContent, /CREDENTIAL_REQUIRED/)
  assert.match(document.body.textContent, /Changed: model/)
  assert.ok([...field("models / model").options].some(option => option.value === "retain"))
  await change(field("models / model"), "replace")
  await click("Validate choices and refresh preview")
  await change(field("Fresh credential: models / model"), SECRET)
  const confirm = [...document.querySelectorAll("button")].find(button => button.textContent === "Confirm import")
  assert.equal(confirm.disabled, true)
  await change([...document.querySelectorAll("label")].find(label => label.textContent.includes("I consent")).querySelector("input"), true)
  await change([...document.querySelectorAll("label")].find(label => label.textContent.includes("I reviewed")).querySelector("input"), true)
  await act(async () => { confirm.click(); confirm.click() }); await flush()
  assert.equal(applied, 1)
  const body = calls.find(call => call.url.endsWith("/apply")).body
  assert.deepEqual(body.credentials, [{ service: "models", id: "model", value: SECRET }]); assert.equal(body.expected_revision, revision)
  assert.match(document.body.textContent, /Applied successfully/)
  assert.ok(document.body.textContent.includes("Preview logical rollback"))
  assert.equal(document.querySelector('input[type="password"]'), null)
  assert.ok(calls.filter(call => call.url.endsWith("preview")).every(call => !JSON.stringify(call.body).includes(SECRET)))
})
for (const code of ["STALE_PREVIEW", "RECEIPT_EXPIRED", "RECEIPT_USED", "APPLICATION_UNCERTAIN", "HISTORY_CAPACITY"]) {
  test(`apply ${code} clears private values and never retries or claims success`, async () => {
    handle = async url => url.endsWith("/apply") ? reply({ detail: { code, message: SECRET } }, 409) : reply(basePreview({ can_apply: true, receipt, required_consents: [] }))
    await render(React.createElement(ConfigurationTransfer, { onApplied() {} })); await pick(); await click("Preview import")
    await change([...document.querySelectorAll("label")].find(label => label.textContent.includes("I reviewed")).querySelector("input"), true)
    await click("Confirm import")
    assert.equal(calls.filter(call => call.url.endsWith("/apply")).length, 1)
    assert.ok(!document.body.textContent.includes("Applied successfully"))
    assert.equal(document.querySelector('input[type="password"]'), null)
    assert.match(document.body.textContent, /Inspect operation outcome/)
  })
}
test("retention unavailable without backend eligibility; JSON apply stays gated", async () => {
  handle = async () => reply(basePreview({ backend: "json", credential_options: [{ service: "models", id: "model", retain_eligible: false }], diagnostics: [{ code: "UNSUPPORTED_JSON_APPLY", message: "unsupported" }] }))
  await render(React.createElement(ConfigurationTransfer, { onApplied() {} })); await pick(); await click("Preview import")
  assert.ok(![...field("models / model").options].some(option => option.value === "retain"))
  assert.match(document.body.textContent, /JSON installations support export and preview/)
  assert.ok(!document.body.textContent.includes("Confirm import"))
})

for (const conflict of [false, true]) test(`logical rollback uses its own preview, policy and confirmation (conflict=${conflict})`, async () => {
  let rolledBack = false
  handle = async (url, init) => {
    if (url.endsWith("/operation")) return reply({ operation: receipt, state: "committed", revision, current_revision: revision, kind: rolledBack && !conflict ? "rollback" : "import", rollback_available: !rolledBack })
    if (url.endsWith("/rollback/preview")) return reply(basePreview({ kind: "rollback", can_apply: init.body.includes('"action"'), receipt }))
    if (url.endsWith("/rollback/apply")) { rolledBack = true; return conflict ? reply({ detail: { code: "ROLLBACK_CONFLICT", message: SECRET } }, 409) : reply({ operation: receipt, state: "committed", revision, kind: "rollback" }) }
    if (url.endsWith("/apply")) return reply({ operation: receipt, state: "committed", revision, kind: "import" })
    return reply(basePreview({ can_apply: true, receipt, required_consents: [] }))
  }
  await render(React.createElement(ConfigurationTransfer, { onApplied() {} })); await pick(); await click("Preview import")
  await change([...document.querySelectorAll("label")].find(label => label.textContent.includes("I reviewed")).querySelector("input"), true)
  await click("Confirm import"); await click("Preview logical rollback")
  assert.equal(rolledBack, false)
  await change(field("models / model"), "clear"); await click("Validate choices and refresh preview")
  await change([...document.querySelectorAll("label")].find(label => label.textContent.includes("I consent")).querySelector("input"), true)
  await change([...document.querySelectorAll("label")].find(label => label.textContent.includes("I reviewed")).querySelector("input"), true)
  await click("Confirm logical rollback")
  assert.equal(rolledBack, true)
  if (conflict) assert.match(document.body.textContent, /Rollback is unavailable after intervening edits/)
  else { assert.match(document.body.textContent, /Applied successfully/); assert.ok(!document.body.textContent.includes("Preview logical rollback")) }
})

test("Chinese localization and keyboard tab navigation reach data sources without automatic probes", async () => {
  localStorage.setItem("alphalab-language", "zh")
  await render(React.createElement(ModelProviderSettings)); await click("个人配置")
  const tab = [...document.querySelectorAll('[role="tab"]')].find(item => item.textContent === "数据源")
  await act(async () => { tab.focus(); tab.dispatchEvent(new window.KeyboardEvent("keydown", { key: "Enter", bubbles: true })) }); await flush()
  assert.equal(tab.getAttribute("aria-selected"), "true")
  assert.match(document.body.textContent, /Personal RQData/)
  assert.ok(calls.every(call => call.method === "GET"))
  await change(field("RQData 用户名"), "unsaved-localized-user")
  assert.match(document.body.textContent, /配置修改尚未保存。更改选择不会保存这些字段。/)
})

test("cancel import clears fresh values and invalidates the receipt without applying", async () => {
  handle = async () => reply(basePreview({ can_apply: true, receipt }))
  await render(React.createElement(ConfigurationTransfer, { onApplied() {} })); await pick(); await click("Preview import")
  await change(field("models / model"), "replace"); await click("Validate choices and refresh preview")
  await change(field("Fresh credential: models / model"), SECRET)
  await click("Cancel import and clear private inputs")
  assert.equal(document.querySelector('input[type="password"]'), null)
  assert.ok(!calls.some(call => call.url.endsWith("/apply")))
})

async function tab(text) {
  const target = [...document.querySelectorAll('[role="tab"]')].find(item => item.textContent === text)
  assert.ok(target)
  await act(async () => { target.focus(); target.dispatchEvent(new window.KeyboardEvent("keydown", { key: "Enter", bubbles: true })) }); await flush()
}
async function closeSettings() {
  await act(async () => document.dispatchEvent(new window.KeyboardEvent("keydown", { key: "Escape", bubbles: true }))); await flush()
  assert.equal(document.querySelector('[role="dialog"]'), null)
}
// A valid merge with no imported profiles needs no credential decisions. It
// changes model selection while preserving the current native profile rows.
const selectionPreview = () => basePreview({
  can_apply: true, receipt, credential_options: [], credential_actions: [], missing_credentials: [], migrations: [],
  required_consents: ["APPLY_CONFIGURATION", "CHANGE_SELECTION"],
  changes: Object.fromEntries(Object.keys(rows).map(service => [service, { added: [], changed: [], removed: [], unchanged: [rows[service].id], selection_before: { kind: "profile", id: rows[service].id }, selection_after: { kind: "none" } }])),
  configuration: Object.fromEntries(Object.keys(rows).map(service => [service, { profiles: [Object.fromEntries(Object.entries(rows[service]).filter(([key]) => !key.startsWith("has_") && key !== "configured"))], selection: { kind: "none" } }])),
})
const emptyDocument = JSON.stringify({ format: "alphalab.service-configuration", version: 1, ...Object.fromEntries(Object.keys(rows).map(service => [service, { version: service === "search" ? 2 : 1, profiles: [], selection: { kind: "none" } }])) })
async function readyTransfer(documentText = emptyDocument, credentialAction) {
  await click("Personal configuration"); await tab("Import / Export"); await pick(documentText); await click("Preview import")
  if (credentialAction) { await change(field("models / model"), credentialAction); await click("Validate choices and refresh preview") }
  for (const label of [...document.querySelectorAll("label")].filter(item => item.textContent.includes("I consent"))) await change(label.querySelector("input"), true)
  await change([...document.querySelectorAll("label")].find(item => item.textContent.includes("I reviewed")).querySelector("input"), true)
}
const committed = (kind = "import") => ({ operation: receipt, state: "committed", kind, revision, current_revision: revision, rollback_available: kind === "import" })

test("correction: selection changes preserve dirty RQData edits and cannot enable a misleading test", async () => {
  let selected = settings("rqdata").selection
  handle = async (url, init) => {
    if (url.endsWith("/selection")) { const body = JSON.parse(init.body); selected = { kind: body.kind, ...(body.id ? { id: body.id } : {}) } }
    return reply({ ...settings("rqdata"), selection: selected, active_id: selected.id ?? null, configured: selected.kind === "profile" })
  }
  await render(React.createElement(ProfileSettings, { service: "rqdata", onSaved() {} }))
  await change(field("RQData username"), "edited-not-saved")
  await click("Disable / select none")
  assert.match(document.body.textContent, /Unsaved profile edits/, "DIRTY_SELECTION_GUARD")
  assert.ok(!document.body.textContent.includes("Saved."), "DIRTY_SELECTION_GUARD")
  assert.equal(field("RQData username").value, "edited-not-saved")
  for (const text of ["Use saved profile", "Test connection"]) assert.equal([...document.querySelectorAll("button")].find(item => item.textContent === text).disabled, true)
  assert.ok(calls.filter(call => call.method === "PUT").every(call => call.url.endsWith("/selection")))
  await click("Reload settings"); assert.equal(field("RQData username").value, "") // none opens a fresh editor
})

for (const provider of ["off", "openrouter"]) test(`correction: explicit Brave deletion survives provider change to ${provider}`, async () => {
  await render(React.createElement(ProfileSettings, { service: "search", onSaved() {} }))
  await change(document.querySelector('input[type="checkbox"]'), true)
  await change(field("Search provider"), provider)
  assert.equal(document.querySelector('input[type="checkbox"]').checked, true, "CLEAR_INTENT_GUARD")
  await click("Save profile")
  assert.equal(calls.at(-1).body.clear_brave_api_key, true, "CLEAR_INTENT_GUARD")
  assert.equal(calls.at(-1).body.brave_api_key, "")
  await change(field("Saved profiles"), ""); assert.equal(document.querySelector('input[type="checkbox"]').checked, false)
})

for (const transition of ["escape", "close-button", "tab"]) test(`correction: pending import survives ${transition} and settles once`, async () => {
  let release, applies = 0, notifications = 0
  const wait = new Promise(resolve => { release = resolve })
  const changed = () => { notifications++ }; window.addEventListener("alphalab:model-provider-changed", changed)
  handle = async url => {
    if (url.endsWith("/preview")) return reply(selectionPreview())
    if (url.endsWith("/apply")) { applies++; return wait }
    if (url.endsWith("/operation")) return reply(committed())
    return reply(settings(url.includes("model-providers") ? "models" : url.includes("search-providers") ? "search" : "rqdata"))
  }
  try {
    await render(React.createElement(ModelProviderSettings)); await readyTransfer()
    const button = [...document.querySelectorAll("button")].find(item => item.textContent === "Confirm import")
    await act(async () => { button.click(); button.click() }); await flush()
    assert.equal(applies, 1); assert.match(document.body.textContent, /operation pending/)
    if (transition === "tab") await tab("Models")
    else if (transition === "close-button") { await click("Close"); assert.equal(document.querySelector('[role="dialog"]'), null) }
    else await closeSettings()
    if (transition !== "tab") await click("Personal configuration")
    await tab("Import / Export")
    assert.ok(document.body.textContent.includes(receipt), "OPERATION_LIFETIME_GUARD")
    assert.ok(!document.body.textContent.includes("Applied successfully"))
    await act(async () => release(reply(committed()))); await flush()
    assert.match(document.body.textContent, /Applied successfully/)
    assert.equal(notifications, 1)
    assert.equal(calls.filter(call => call.url.endsWith("/operation")).length, 1)
    for (const text of ["Models", "Web Search", "Data Sources"]) await tab(text)
    for (const path of ["model-providers", "search-providers", "data-source-profiles"]) assert.ok(calls.some(call => call.url.endsWith(path) && call.method === "GET"))
  } finally { window.removeEventListener("alphalab:model-provider-changed", changed); release(reply(committed())); await flush() }
})

test("correction: lost publication response survives reopen and inspection without replay", async () => {
  let notifications = 0
  const changed = () => { notifications++ }; window.addEventListener("alphalab:model-provider-changed", changed)
  handle = async url => url.endsWith("/preview") ? reply(selectionPreview()) : url.endsWith("/apply") ? Promise.reject(new Error(SECRET)) : url.endsWith("/operation") ? reply(committed()) : reply(settings("models"))
  try {
    await render(React.createElement(ModelProviderSettings)); await readyTransfer(); await click("Confirm import")
    assert.equal(notifications, 0); assert.ok(!document.body.textContent.includes("Applied successfully"))
    await closeSettings(); await click("Personal configuration"); await tab("Import / Export")
    assert.ok(document.body.textContent.includes(receipt), "OPERATION_LIFETIME_GUARD")
    await click("Inspect operation outcome")
    assert.equal(notifications, 1, "READINESS_INVALIDATION_GUARD"); assert.match(document.body.textContent, /Applied successfully/)
    await click("Inspect operation outcome"); assert.equal(notifications, 1)
    assert.equal(calls.filter(call => call.url.endsWith("/apply")).length, 1)
  } finally { window.removeEventListener("alphalab:model-provider-changed", changed) }
})

test("correction: dismissal clears fresh transfer credentials while retaining only operation tracking", async () => {
  let release
  const wait = new Promise(resolve => { release = resolve })
  const preview = { ...selectionPreview(), credential_options: [{ service: "models", id: "model", retain_eligible: true }] }
  const portable = JSON.parse(emptyDocument)
  portable.models.profiles = [{ id: "model", name: "Personal model", base_url: "https://openrouter.ai/api/v1", model: "provider/model" }]
  handle = async (url, init) => {
    if (url.endsWith("/preview")) return reply({ ...preview, can_apply: JSON.parse(init.body).credential_policy.length > 0 })
    if (url.endsWith("/apply")) return wait
    if (url.endsWith("/operation")) return reply(committed())
    return reply(settings("models"))
  }
  try {
    await render(React.createElement(ModelProviderSettings)); await click("Personal configuration"); await tab("Import / Export"); await pick(JSON.stringify(portable)); await click("Preview import")
    await change(field("models / model"), "replace"); await click("Validate choices and refresh preview")
    await change(field("Fresh credential: models / model"), SECRET)
    for (const label of [...document.querySelectorAll("label")].filter(item => item.textContent.includes("I consent"))) await change(label.querySelector("input"), true)
    await change([...document.querySelectorAll("label")].find(item => item.textContent.includes("I reviewed")).querySelector("input"), true)
    await click("Confirm import"); assert.equal(document.querySelector('input[type="password"]'), null)
    await closeSettings(); await click("Personal configuration"); await tab("Import / Export")
    assert.equal(document.querySelector('input[type="password"]'), null)
    assert.ok(document.body.textContent.includes(receipt))
    assert.deepEqual(calls.find(call => call.url.endsWith("/apply")).body.credentials, [{ service: "models", id: "model", value: SECRET }])
    await act(async () => release(reply(committed()))); await flush()
    assert.equal(document.querySelector('input[type="password"]'), null)
  } finally { release(reply(committed())); await flush() }
})

function AgentReadiness() {
  const agent = usePublishedAgent()
  return React.createElement("output", { "data-testid": "agent-readiness" }, agent.status?.available ? "ready" : "not-ready")
}
for (const outcome of ["configured-model", "none", "cleared-key", "rollback", "rejected"]) test(`correction: real Agent readiness subscriber refreshes after ${outcome}`, async () => {
  let available = outcome === "none" || outcome === "cleared-key" || outcome === "rollback", notifications = 0, statusReads = 0
  let selectionKind = available ? "profile" : "none", hasKey = true
  const importedModel = outcome === "configured-model" || outcome === "cleared-key"
  const importedDocument = JSON.parse(emptyDocument)
  const metadata = { id: "model", name: "Personal model", base_url: rows.models.base_url, model: rows.models.model }
  if (importedModel) importedDocument.models = { version: 1, profiles: [metadata], selection: { kind: "profile", id: "model" } }
  function modelPreview(kind, policy = []) {
    const value = selectionPreview()
    value.kind = kind
    value.changes.models.selection_before = { kind: selectionKind, ...(selectionKind === "profile" ? { id: "model" } : {}) }
    if (importedModel || kind === "rollback") {
      value.configuration.models = { profiles: [metadata], selection: { kind: "profile", id: "model" } }
      value.changes.models.selection_after = { kind: "profile", id: "model" }
      value.credential_options = [{ service: "models", id: "model", retain_eligible: true }]
      value.credential_actions = policy
      value.can_apply = policy.length === 1
      value.receipt = value.can_apply ? receipt : null
      value.missing_credentials = outcome === "cleared-key" ? [{ service: "models", id: "model", code: "CREDENTIAL_REQUIRED" }] : []
      if (policy.some(row => row.action === "clear")) value.required_consents.push("CLEAR_CREDENTIALS")
    }
    return value
  }
  const changed = () => { notifications++ }; window.addEventListener("alphalab:model-provider-changed", changed)
  handle = async (url, init) => {
    if (url.endsWith("/conversations")) return reply({ conversations: [], runs: [] })
    if (url.endsWith("/status")) { statusReads++; return reply({ available, mode: "local_harness", model_configured: available }) }
    if (url.endsWith("/manifest")) return reply({ defaultExposureId: "agent", exposures: [{ id: "agent", nodeType: "agent", surfaces: ["api"] }] })
    if (url.endsWith("/rollback/preview")) return reply(modelPreview("rollback", JSON.parse(init.body).credential_policy))
    if (url.endsWith("/preview")) return reply(modelPreview("import", JSON.parse(init.body).credential_policy))
    if (url.endsWith("/apply")) {
      if (outcome === "rejected") return reply({ detail: { code: "STALE_PREVIEW" } }, 409)
      available = outcome === "configured-model" || url.includes("/rollback/")
      selectionKind = importedModel || url.includes("/rollback/") ? "profile" : "none"
      if (outcome === "cleared-key") hasKey = false
      return reply(committed(url.includes("/rollback/") ? "rollback" : "import"))
    }
    if (url.endsWith("/operation")) return reply(committed(outcome === "rollback" && notifications > 1 ? "rollback" : "import"))
    return reply({ ...settings("models"), active_id: selectionKind === "profile" ? "model" : null, selection: { kind: selectionKind, ...(selectionKind === "profile" ? { id: "model" } : {}) }, configured: available, profiles: [{ ...rows.models, has_api_key: hasKey }] })
  }
  try {
    await render(React.createElement(React.Fragment, null, React.createElement(AgentReadiness), React.createElement(ModelProviderSettings)))
    const initialReads = statusReads
    await readyTransfer(JSON.stringify(importedDocument), importedModel ? outcome === "cleared-key" ? "clear" : "retain" : undefined)
    if (importedModel) assert.deepEqual(calls.filter(call => call.url.endsWith("/preview")).at(-1).body.credential_policy, [{ service: "models", id: "model", action: outcome === "cleared-key" ? "clear" : "retain" }])
    assert.equal(statusReads, initialReads); await click("Confirm import"); await flush()
    assert.equal(notifications, outcome === "rejected" ? 0 : 1, "READINESS_INVALIDATION_GUARD")
    assert.equal(statusReads, initialReads + (outcome === "rejected" ? 0 : 1), "READINESS_INVALIDATION_GUARD")
    assert.equal(document.querySelector('[data-testid="agent-readiness"]').textContent, available ? "ready" : "not-ready", "READINESS_INVALIDATION_GUARD")
    if (outcome === "rollback") {
      await click("Preview logical rollback"); await change(field("models / model"), "retain"); await click("Validate choices and refresh preview")
      for (const label of [...globalThis.document.querySelectorAll("label")].filter(item => item.textContent.includes("I consent"))) await change(label.querySelector("input"), true)
      await change([...globalThis.document.querySelectorAll("label")].find(item => item.textContent.includes("I reviewed")).querySelector("input"), true)
      await click("Confirm logical rollback"); assert.equal(notifications, 2); assert.equal(statusReads, initialReads + 2)
      assert.equal(document.querySelector('[data-testid="agent-readiness"]').textContent, "ready")
    }
  } finally { window.removeEventListener("alphalab:model-provider-changed", changed) }
})

for (const provider of ["brave", "off"]) test(`correction: current JSON environment ${provider} uses the legacy test envelope only`, async () => {
  const environment = { mode: "local", active_id: null, profiles: [], provider, model_profile_id: null, has_brave_api_key: provider === "brave", configured: provider === "brave", source: "environment" }
  handle = async url => url.endsWith("/test") ? reply({ ok: true }) : url.includes("model-providers") ? reply(settings("models")) : reply(environment)
  await render(React.createElement(ProfileSettings, { service: "search", onSaved() {} }))
  assert.ok(calls.every(call => call.method === "GET"))
  assert.equal(field("Profile name").value, ""); assert.equal(field("Search provider").value, "off")
  await click("Test current environment")
  assert.deepEqual(calls.at(-1).body, { provider, model_profile_id: null, brave_api_key: "" }, "ENVIRONMENT_ENVELOPE_GUARD")
  assert.ok(calls.at(-1).url.endsWith("/search-providers/test")); assert.ok(!calls.some(call => call.method === "PUT"))
  assert.equal(field("Profile name").value, ""); assert.equal(document.querySelector('input[type="password"]'), null)
  assert.match(document.body.textContent, provider === "off" ? /Current environment search is Off/ : /Current environment search test passed/)
  assert.match(document.body.textContent, /Connection not tested in this session/)
})

test("correction: SQLite environment search remains unavailable without JSON fallback", async () => {
  handle = async url => reply(url.includes("model-providers") ? settings("models") : { ...settings("search"), source: "environment", selection: { kind: "environment" }, provider: "brave", active_id: null })
  await render(React.createElement(ProfileSettings, { service: "search", onSaved() {} }))
  assert.equal([...document.querySelectorAll("button")].find(item => item.textContent === "Test current environment").disabled, true)
  assert.match(document.body.textContent, /unavailable for this SQLite backend/)
  assert.ok(calls.every(call => call.method === "GET"))
})

for (const [mutation, pattern, marker] of [
  ["dirty", "correction: selection changes preserve dirty", "DIRTY_SELECTION_GUARD"],
  ["clear", "correction: explicit Brave deletion survives provider change to off", "CLEAR_INTENT_GUARD"],
  ["lifetime", "correction: pending import survives escape", "OPERATION_LIFETIME_GUARD"],
  ["readiness", "correction: real Agent readiness subscriber refreshes after configured-model", "READINESS_INVALIDATION_GUARD"],
  ["environment", "correction: current JSON environment brave", "ENVIRONMENT_ENVELOPE_GUARD"],
]) test(`negative control: ${mutation} regression detects original behavior in a separate process`, () => {
  const env = { ...process.env, ALPHALAB_CONFIGURATION_TEST_MUTATION: mutation }
  delete env.NODE_TEST_CONTEXT // Start an independent runner, not a nested IPC child.
  const result = spawnSync(process.execPath, ["--import", new URL("./configuration-dom.mjs", import.meta.url).href, "--test", "--test-name-pattern", pattern, fileURLToPath(import.meta.url)], { env, encoding: "utf8", timeout: 30_000, maxBuffer: 1024 * 1024 })
  assert.equal(result.status, 1, `${mutation} must fail the intended regression`)
  assert.ok(result.stdout.includes(marker), `${mutation} must fail at ${marker}, not an unrelated validation path`)
  assert.ok(!result.stdout.includes("TEST_MUTATION_NOT_APPLIED"))
})
