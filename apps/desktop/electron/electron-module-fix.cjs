/**
 * Windows fix: patch node_modules/electron/index.js to return the built-in
 * Electron API when running inside the Electron process.
 *
 * This runs as --require before the main process loads, so it must stay
 * CommonJS even though apps/desktop/package.json is type=module.
 */
'use strict'

const Module = require('module')
const path = require('path')

const orig = Module._resolveFilename
Module._resolveFilename = function (request, parent, isMain, options) {
  if (request === 'electron') {
    // Check if we're inside the Electron process
    if (process.versions.electron) {
      // Delete any cached npm package resolution
      const npmPath = path.join(__dirname, '..', 'node_modules', 'electron', 'index.js')
      delete Module._cache[npmPath]

      // Force Electron's built-in by preventing node_modules resolution
      // The trick: return a non-existent path that triggers Electron's
      // internal fallback. Actually, we inject the module directly.
      if (!Module._cache.electron) {
        // Create a virtual module that re-exports Electron's bindings
        const electronModule = new Module('electron')
        electronModule.loaded = true

        // Access Electron API via process._linkedBinding
        const { app } = process._linkedBinding('electron_browser_app')
        const exports = { app }

        // Lazy getters for other APIs to avoid early-init crashes
        const apis = {
          BrowserWindow: 'electron_browser_window',
          ipcMain: 'electron_browser_ipc_main',
          dialog: 'electron_browser_dialog',
          Menu: 'electron_browser_menu',
          Notification: 'electron_browser_notification',
          shell: 'electron_common_shell',
          clipboard: 'electron_common_clipboard',
          nativeImage: 'electron_common_native_image',
          net: 'electron_browser_net',
          protocol: 'electron_browser_protocol',
          session: 'electron_browser_session',
          Tray: 'electron_browser_tray',
          globalShortcut: 'electron_browser_global_shortcut',
          screen: 'electron_common_screen',
          powerMonitor: 'electron_browser_power_monitor',
          contentTracing: 'electron_browser_content_tracing',
          webContents: 'electron_browser_web_contents',
        }

        for (const [name, binding] of Object.entries(apis)) {
          Object.defineProperty(exports, name, {
            get() {
              try {
                const mod = process._linkedBinding(binding)
                return mod[name] || mod
              } catch {
                return undefined
              }
            },
            enumerable: true,
            configurable: true,
          })
        }

        electronModule.exports = exports
        Module._cache.electron = electronModule
      }

      return 'electron'
    }
  }
  return orig.call(this, request, parent, isMain, options)
}
