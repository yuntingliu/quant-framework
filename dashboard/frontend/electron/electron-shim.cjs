/**
 * Electron API shim for Windows.
 *
 * Replaces require('electron') at build time (via vite.config.ts writeBundle).
 * Tries multiple strategies to get the real Electron API:
 * 1. Module._cache lookup (if browser_init.js ran)
 * 2. Hide npm package + re-require (forces Electron's internal resolver)
 * 3. Fallback to safe _linkedBinding + stubs
 */
'use strict'

const Module = require('module')
const fs = require('fs')
const path = require('path')

const LOG = path.join(__dirname, '..', '_electron_shim.log')
const log = (m) => { try { fs.appendFileSync(LOG, m + '\n') } catch {} }
log('=== electron-shim loaded at ' + new Date().toISOString() + ' ===')
log('process.type: ' + process.type)
log('process.versions.electron: ' + process.versions.electron)

// Strategy 1: Check Module._cache
let real = null
if (Module._cache['electron']?.exports?.app) {
  real = Module._cache['electron'].exports
  log('Strategy 1: found in Module._cache["electron"]')
}
if (!real) {
  for (const k of Object.keys(Module._cache)) {
    const m = Module._cache[k]
    if (m?.exports?.app && typeof m.exports.app.whenReady === 'function') {
      real = m.exports
      log('Strategy 1: found in cache key: ' + k)
      break
    }
  }
}

// Strategy 2: Hide npm package, retry require('electron')
if (!real) {
  const npmIdx = path.join(__dirname, '..', 'node_modules', 'electron', 'index.js')
  const npmBak = npmIdx + '.__shim_bak'
  log('Strategy 2: trying hide-and-require, npmIdx=' + npmIdx)

  let hidden = false
  try {
    if (fs.existsSync(npmIdx)) {
      fs.renameSync(npmIdx, npmBak)
      hidden = true
      log('Strategy 2: renamed index.js')
    }
  } catch (e) { log('Strategy 2: rename failed: ' + e.message) }

  if (hidden) {
    // Also clear any cached resolution
    for (const k of Object.keys(Module._cache)) {
      if (k.includes('node_modules') && k.includes('electron')) {
        delete Module._cache[k]
      }
    }

    try {
      // Force fresh resolution
      delete require.cache[require.resolve('electron')]
    } catch {}

    try {
      const e = require('electron')
      log('Strategy 2: require returned type=' + typeof e)
      if (typeof e === 'object' && e !== null && e.app) {
        real = e
        log('Strategy 2: SUCCESS — got real electron module!')
      } else {
        log('Strategy 2: got ' + (typeof e === 'string' ? 'string: ' + e.substring(0,50) : typeof e))
      }
    } catch (err) {
      log('Strategy 2: require threw: ' + err.message)
    }

    // Restore npm package
    try {
      if (fs.existsSync(npmBak)) {
        try { fs.unlinkSync(npmIdx) } catch {}
        fs.renameSync(npmBak, npmIdx)
        log('Strategy 2: restored index.js')
      }
    } catch (e) { log('Strategy 2: restore failed: ' + e.message) }
  }
}

// Strategy 3: stubs
if (real && typeof real === 'object' && real.app) {
  log('Using REAL electron module')
  module.exports = real
} else {
  log('Using STUB electron module (window creation will not work)')
  const EventEmitter = require('events')
  const safeBind = (n) => { try { return process._linkedBinding(n) } catch { return null } }

  const bw = safeBind('electron_browser_window')
  const dlg = safeBind('electron_browser_dialog')
  const sh = safeBind('electron_common_shell')

  const app = new EventEmitter()
  app.whenReady = () => Promise.resolve()
  app.isPackaged = false
  app.isReady = () => true
  app.getName = () => 'AlphaLab'
  app.getVersion = () => '0.1.0'
  app.quit = () => process.exit(0)
  app.exit = (c) => process.exit(c || 0)
  app.getPath = (name) => {
    const os = require('os'), p = require('path'), h = os.homedir()
    return ({ home: h, appData: p.join(h,'AppData','Roaming'),
      userData: p.join(h,'AppData','Roaming','AlphaLab'),
      temp: os.tmpdir(), desktop: p.join(h,'Desktop'),
      documents: p.join(h,'Documents'),
    })[name] || h
  }
  app.commandLine = { appendSwitch() {}, getSwitchValue() { return '' } }
  app.setAboutPanelOptions = () => {}
  app.on('window-all-closed', () => {}) // prevent default exit

  const ipcMain = new EventEmitter()
  ipcMain.handle = () => {}
  ipcMain.handleOnce = () => {}
  ipcMain.removeHandler = () => {}

  function Menu() {}
  Menu.setApplicationMenu = () => {}
  Menu.buildFromTemplate = (t) => ({ items: t })
  Menu.getApplicationMenu = () => null
  function MenuItem(o) { Object.assign(this, o) }

  module.exports = {
    app,
    BrowserWindow: bw?.BrowserWindow || function() {},
    ipcMain,
    dialog: dlg || { showErrorBox(t,m) { console.error(`[${t}] ${m}`) }, showMessageBox: () => Promise.resolve({response:0}), showMessageBoxSync: () => 0 },
    Menu, MenuItem,
    shell: sh || { openPath: () => Promise.resolve(''), openExternal: () => Promise.resolve() },
    nativeImage: safeBind('electron_common_native_image')?.nativeImage || {},
    clipboard: safeBind('electron_common_clipboard') || {},
    globalShortcut: safeBind('electron_browser_global_shortcut')?.globalShortcut || {},
    protocol: safeBind('electron_browser_protocol') || { registerSchemesAsPrivileged() {} },
    session: { defaultSession: { webRequest: { onBeforeSendHeaders() {} } } },
    Tray: function() {},
    screen: { getPrimaryDisplay: () => ({ workAreaSize: { width: 1920, height: 1080 } }) },
    powerMonitor: safeBind('electron_browser_power_monitor') || new EventEmitter(),
    contentTracing: safeBind('electron_browser_content_tracing') || {},
    webContents: { getAllWebContents: () => [] },
    net: { request() {} },
    Notification: function() {},
  }
}
