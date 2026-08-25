import * as monaco from "@codingame/monaco-vscode-editor-api"
import getBaseServiceOverride from "@codingame/monaco-vscode-base-service-override"
import {
  conf as pythonLanguageConfiguration,
  language as pythonLanguage,
} from "monaco-editor/esm/vs/basic-languages/python/python.js"
import { LanguageClientWrapper } from "monaco-languageclient/lcwrapper"
import { MonacoVscodeApiWrapper } from "monaco-languageclient/vscodeApiWrapper"
import { configureDefaultWorkerFactory } from "monaco-languageclient/workerFactory"
import * as vscode from "vscode"

import { api, getApiOrigin } from "@/lib/api"

export interface EditorServerCapability {
  id: "pyrefly" | "ruff"
  available: boolean
  version: string | null
  command: string[] | null
}

export interface PythonEditorCapabilities {
  python_executable: string
  workspace_uri: string
  mirror_root: string
  rqdata_operations: Array<{ name: string; signature: string; documentation: string }>
  servers: Record<"pyrefly" | "ruff", EditorServerCapability>
  ready: boolean
  install_hint: string
}

export interface PythonDocument {
  kind: "strategy" | "data"
  document_id: string
  uri: string
  workspace_uri: string
}

export type EditorServerStatus = "starting" | "ready" | "missing" | "error"

export interface PythonEditorRuntimeSnapshot {
  initialized: boolean
  pyrefly: EditorServerStatus
  ruff: EditorServerStatus
  capabilities: PythonEditorCapabilities | null
  error: string | null
}

const fallbackSnapshot: PythonEditorRuntimeSnapshot = {
  initialized: false,
  pyrefly: "starting",
  ruff: "starting",
  capabilities: null,
  error: null,
}

let snapshot = fallbackSnapshot
let startPromise: Promise<PythonEditorRuntimeSnapshot> | null = null
const listeners = new Set<() => void>()
const clients = new Map<string, LanguageClientWrapper>()

export function subscribePythonEditorRuntime(listener: () => void) {
  listeners.add(listener)
  return () => listeners.delete(listener)
}

export function getPythonEditorRuntimeSnapshot() {
  return snapshot
}

function updateSnapshot(value: Partial<PythonEditorRuntimeSnapshot>) {
  snapshot = { ...snapshot, ...value }
  listeners.forEach((listener) => listener())
}

function webSocketUrl(server: string) {
  const configuredOrigin = getApiOrigin()
  const origin = configuredOrigin || window.location.origin
  const url = new URL(`/api/python-editor/lsp/${server}`, origin)
  url.protocol = url.protocol === "https:" ? "wss:" : "ws:"
  return url.toString()
}

async function startClient(
  server: "pyrefly" | "ruff",
  capabilities: PythonEditorCapabilities,
) {
  if (!capabilities.servers[server].available) {
    updateSnapshot({ [server]: "missing" })
    return
  }
  const initializationOptions = server === "pyrefly"
    ? {
        pythonPath: capabilities.python_executable,
        analysis: {
          diagnosticMode: "openFilesOnly",
          autoImportCompletions: true,
          completeFunctionParens: true,
        },
        pyrefly: { typeCheckingMode: "auto" },
      }
    : {
        settings: {
          configuration: "pyproject.toml",
          organizeImports: true,
          fixAll: true,
        },
      }
  const client = new LanguageClientWrapper({
    // The wrapper also uses languageId as the VS Code language-client ID.
    // Keep the two clients distinct or Ruff can replace Pyrefly's completion
    // feature after it has briefly appeared.
    languageId: `python-${server}`,
    connection: {
      options: {
        $type: "WebSocketUrl",
        url: webSocketUrl(server),
      },
    },
    clientOptions: {
      documentSelector: [{ language: "python", scheme: "file" }],
      workspaceFolder: {
        index: 0,
        name: "AlphaLab",
        uri: vscode.Uri.parse(capabilities.workspace_uri),
      },
      initializationOptions,
    },
    restartOptions: { retries: 2, timeout: 1000 },
  })
  clients.set(server, client)
  try {
    await client.start()
    updateSnapshot({ [server]: "ready" })
  } catch {
    updateSnapshot({ [server]: "error" })
  }
}

export function startPythonEditorRuntime() {
  if (startPromise) return startPromise
  startPromise = (async () => {
    const wrapper = new MonacoVscodeApiWrapper({
      $type: "classic",
      viewsConfig: { $type: "EditorService" },
      serviceOverrides: {
        ...getBaseServiceOverride(),
      },
      monacoWorkerFactory: configureDefaultWorkerFactory,
      userConfiguration: {
        json: JSON.stringify({
          "editor.wordBasedSuggestions": "off",
          "editor.semanticHighlighting.enabled": true,
          "editor.formatOnPaste": false,
          "editor.formatOnSave": false,
          "files.eol": "\n",
        }),
      },
      advanced: {
        enableExtHostWorker: false,
        enforceSemanticHighlighting: true,
        loadExtensionServices: true,
        loadThemes: true,
      },
    })
    await wrapper.start({ caller: "AlphaLab PythonEditor" })
    if (!monaco.languages.getLanguages().some((language) => language.id === "python")) {
      monaco.languages.register({
        id: "python",
        extensions: [".py", ".pyi", ".pyw"],
        aliases: ["Python", "py"],
      })
    }
    // The local extension host stays disabled. Monarch supplies immediate syntax
    // colors; Pyrefly layers semantic tokens, navigation and completion on top.
    monaco.languages.setLanguageConfiguration("python", pythonLanguageConfiguration)
    monaco.languages.setMonarchTokensProvider("python", pythonLanguage)
    let capabilities: PythonEditorCapabilities | null = null
    try {
      capabilities = await api.get<PythonEditorCapabilities>("/python-editor/capabilities")
    } catch {
      updateSnapshot({ initialized: true, pyrefly: "error", ruff: "error" })
      return snapshot
    }
    updateSnapshot({ initialized: true, capabilities })
    void Promise.all([
      startClient("pyrefly", capabilities),
      startClient("ruff", capabilities),
    ])
    return snapshot
  })().catch((error: unknown) => {
    const message = error instanceof Error ? error.message : String(error)
    console.error("AlphaLab Python editor runtime failed to initialize", error)
    updateSnapshot({ initialized: false, pyrefly: "error", ruff: "error", error: message })
    return snapshot
  })
  return startPromise
}

export async function preparePythonDocument(
  kind: "strategy" | "data",
  documentId: string,
  source: string,
): Promise<PythonDocument> {
  return api.post<PythonDocument>("/python-editor/documents", {
    kind,
    document_id: documentId,
    source,
  })
}

export { monaco }
