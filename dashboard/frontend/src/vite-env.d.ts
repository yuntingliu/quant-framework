/// <reference types="vite/client" />

declare module "monaco-editor/esm/vs/basic-languages/python/python.js" {
  import type { languages } from "@codingame/monaco-vscode-editor-api"

  export const conf: languages.LanguageConfiguration
  export const language: languages.IMonarchLanguage
}

interface Window {
  api?: {
    getApiBase?: () => string
    onBackendStdout?: (cb: (data: string) => void) => () => void
    onBackendStderr?: (cb: (data: string) => void) => () => void
  }
}

