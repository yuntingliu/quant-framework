/**
 * Preload — expose safe IPC bridge to renderer via contextBridge.
 */
import { contextBridge, ipcRenderer } from 'electron'
import type { IpcRendererEvent } from 'electron'

function getArgumentValue(name: string, fallback: string): string {
  const prefix = `--${name}=`
  const arg = process.argv.find((item) => item.startsWith(prefix))
  return arg ? arg.slice(prefix.length) : fallback
}

const apiOrigin = getArgumentValue('alphalab-api-origin', 'http://127.0.0.1:8000')
const apiBase = getArgumentValue('alphalab-api-base', `${apiOrigin}/api`)

contextBridge.exposeInMainWorld('desktop', {
  setTheme: (theme: 'light' | 'dark') => ipcRenderer.invoke('window:setTheme', theme),
  showMenu: (menu: 'file' | 'view' | 'tools', x: number, y: number, language: 'zh' | 'en') =>
    ipcRenderer.invoke('window:showMenu', menu, x, y, language),
})

contextBridge.exposeInMainWorld('api', {
  // App config
  getConfig: () => ipcRenderer.invoke('app:getConfig'),
  getBackendStatus: () => ipcRenderer.invoke('app:getBackendStatus'),
  getApiOrigin: () => apiOrigin,
  getApiBase: () => apiBase,
  getLanguage: () => ipcRenderer.invoke('app:getLanguage'),
  setLanguage: (language: string) => ipcRenderer.invoke('app:setLanguage', language),

  // Run commands (for scripts, Python calls)
  run: (cmd: string, args: string[], options?: { cwd?: string; timeout?: number }) =>
    ipcRenderer.invoke('backend:run', cmd, args, options),

  // Backend log streaming
  onBackendStdout: (cb: (data: string) => void) => {
    const handler = (_e: IpcRendererEvent, d: string) => cb(d)
    ipcRenderer.on('backend:stdout', handler)
    return () => { ipcRenderer.removeListener('backend:stdout', handler) }
  },
  onBackendStderr: (cb: (data: string) => void) => {
    const handler = (_e: IpcRendererEvent, d: string) => cb(d)
    ipcRenderer.on('backend:stderr', handler)
    return () => { ipcRenderer.removeListener('backend:stderr', handler) }
  },

  // Menu event listeners (main process → renderer)
  onMenuSwitchMode: (cb: (mode: string) => void) => {
    const handler = (_e: IpcRendererEvent, mode: string) => cb(mode)
    ipcRenderer.on('menu:switchMode', handler)
    return () => { ipcRenderer.removeListener('menu:switchMode', handler) }
  },
  onMenuResetLayout: (cb: () => void) => {
    const handler = () => cb()
    ipcRenderer.on('menu:resetLayout', handler)
    return () => { ipcRenderer.removeListener('menu:resetLayout', handler) }
  },
  onMenuOpenWidget: (cb: (widgetId: string, title: string, targetMode?: string) => void) => {
    const handler = (_e: IpcRendererEvent, widgetId: string, title: string, targetMode?: string) => cb(widgetId, title, targetMode)
    ipcRenderer.on('menu:openWidget', handler)
    return () => { ipcRenderer.removeListener('menu:openWidget', handler) }
  },
  onMenuClearLayouts: (cb: () => void) => {
    const handler = () => cb()
    ipcRenderer.on('menu:clearLayouts', handler)
    return () => { ipcRenderer.removeListener('menu:clearLayouts', handler) }
  },
  onMenuSetLanguage: (cb: (language: string) => void) => {
    const handler = (_e: IpcRendererEvent, language: string) => cb(language)
    ipcRenderer.on('menu:setLanguage', handler)
    return () => { ipcRenderer.removeListener('menu:setLanguage', handler) }
  },
})
