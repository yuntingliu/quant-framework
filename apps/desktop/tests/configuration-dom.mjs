// Use the repository's node:test runner and TypeScript compiler for real React
// interaction tests. jsdom is a DOM implementation, not another test framework.
import { registerHooks } from "node:module"
import { existsSync, readFileSync } from "node:fs"
import { fileURLToPath, pathToFileURL } from "node:url"
import path from "node:path"
import ts from "typescript"
import { JSDOM } from "jsdom"

const source = fileURLToPath(new URL("../src/", import.meta.url))
// Negative controls transform only a separately spawned test process's module
// source in memory. Development files and the normal runner are never changed.
function controlSource(url, original) {
  const mutation = process.env.ALPHALAB_CONFIGURATION_TEST_MUTATION
  const changes = {
    dirty: ["ProfileSettings.tsx", 'setSettings(result); setTest("untested");', 'setSettings(result); setDirty(false); setSaved(true); setTest("untested");'],
    clear: ["ProfileSettings.tsx", 'in change) setSecret("")', 'in change) { setSecret(""); setClear(false) }'],
    lifetime: ["ModelProviderSettings.tsx", "coordinator={transfer}", "coordinator={undefined}"],
    readiness: ["ModelProviderSettings.tsx", 'window.dispatchEvent(new Event("alphalab:model-provider-changed"))', "void 0"],
    environment: ["ProfileSettings.tsx", 'environment ? { provider: settings.provider, model_profile_id: settings.model_profile_id ?? null, brave_api_key: "" } : payload(false)', "payload(false)"],
  }
  const change = changes[mutation]
  if (!change || !url.endsWith("/" + change[0])) return original
  if (original.split(change[1]).length !== 2) throw new Error("TEST_MUTATION_NOT_APPLIED")
  return original.replace(change[1], change[2])
}
registerHooks({
  resolve(specifier, context, next) {
    const target = specifier.startsWith("@/") ? path.join(source, specifier.slice(2))
      : specifier.startsWith(".") && context.parentURL?.startsWith("file:") ? fileURLToPath(new URL(specifier, context.parentURL)) : null
    if (target && !path.extname(target)) {
      for (const ext of [".ts", ".tsx"]) if (existsSync(target + ext)) return { url: pathToFileURL(target + ext).href, shortCircuit: true }
    }
    return next(specifier, context)
  },
  load(url, context, next) {
    if (url.startsWith("file:") && /\.tsx?$/.test(url)) return { format: "module", shortCircuit: true, source: ts.transpileModule(controlSource(url, readFileSync(fileURLToPath(url), "utf8")), { fileName: fileURLToPath(url), compilerOptions: { jsx: ts.JsxEmit.ReactJSX, module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText }
    return next(url, context)
  },
})
const dom = new JSDOM("<!doctype html><html><body></body></html>", { url: "http://localhost/" })
for (const key of ["window", "document", "navigator", "HTMLElement", "HTMLInputElement", "HTMLSelectElement", "HTMLButtonElement", "Node", "NodeFilter", "MutationObserver", "Event", "MouseEvent", "KeyboardEvent", "CustomEvent", "localStorage", "sessionStorage", "getComputedStyle"]) {
  Object.defineProperty(globalThis, key, { configurable: true, value: typeof dom.window[key] === "function" && key === "getComputedStyle" ? dom.window[key].bind(dom.window) : dom.window[key] })
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true
globalThis.requestAnimationFrame = callback => setTimeout(callback, 0)
globalThis.cancelAnimationFrame = clearTimeout
dom.window.HTMLElement.prototype.scrollIntoView = () => {}
