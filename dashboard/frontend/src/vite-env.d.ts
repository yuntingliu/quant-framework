/// <reference types="vite/client" />

interface Window {
  api?: {
    getApiBase?: () => string
    onBackendStdout?: (cb: (data: string) => void) => () => void
    onBackendStderr?: (cb: (data: string) => void) => () => void
  }
}

