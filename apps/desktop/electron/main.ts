/**
 * AlphaLab desktop main process.
 */
import { app, BrowserWindow, ipcMain, Menu, shell } from 'electron'
import type { IpcMainInvokeEvent } from 'electron'
import { spawn, type ChildProcess } from 'child_process'
import path from 'path'
import fs from 'fs'
import { createServer } from 'net'
import type { AddressInfo } from 'net'
import { fileURLToPath } from 'url'

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const DIST = path.join(__dirname, '../web')
const VITE_DEV_SERVER_URL = process.env.VITE_DEV_SERVER_URL
const IS_DEV = !!VITE_DEV_SERVER_URL
const DEFAULT_BACKEND_PORT = 8000
const DEFAULT_ZOOM = 1.25
const TITLE_BAR_HEIGHT = 36
const WINDOW_COLORS = {
  light: { color: '#ffffff', symbolColor: '#171c26', background: '#f9f8f6' },
  dark: { color: '#151820', symbolColor: '#e0e6eb', background: '#0e1015' },
}

let mainWindow: BrowserWindow | null = null
let pythonProcess: ChildProcess | null = null
let backendPort = Number(process.env.ALPHALAB_BACKEND_PORT || DEFAULT_BACKEND_PORT)

function getProjectRoot(): string {
  if (process.env.ALPHALAB_PROJECT_ROOT && fs.existsSync(process.env.ALPHALAB_PROJECT_ROOT)) {
    return process.env.ALPHALAB_PROJECT_ROOT
  }
  return path.resolve(__dirname, '..', '..')
}

function findPython(): string {
  const root = getProjectRoot()
  const home = process.env.USERPROFILE || process.env.HOME || ''
  const candidates = [
    path.join(root, '.venv', 'Scripts', 'python.exe'),
    path.join(root, '.venv', 'bin', 'python3'),
    path.join(home, 'miniforge3', 'python.exe'),
    path.join(home, 'miniforge3', 'bin', 'python3'),
    'python',
  ]
  return candidates.find((candidate) => candidate === 'python' || fs.existsSync(candidate)) ?? 'python'
}

function isPortAvailable(port: number): Promise<boolean> {
  return new Promise((resolve) => {
    const server = createServer()
    server.once('error', () => resolve(false))
    server.once('listening', () => server.close(() => resolve(true)))
    server.listen(port, '127.0.0.1')
  })
}

function allocatePort(): Promise<number> {
  return new Promise((resolve, reject) => {
    const server = createServer()
    server.once('error', reject)
    server.once('listening', () => {
      const address = server.address() as AddressInfo
      server.close(() => resolve(address.port))
    })
    server.listen(0, '127.0.0.1')
  })
}

async function chooseBackendPort(): Promise<number> {
  if (await isPortAvailable(backendPort)) return backendPort
  return allocatePort()
}

async function startBackend(): Promise<void> {
  if (IS_DEV) return
  backendPort = await chooseBackendPort()
  const root = getProjectRoot()
  pythonProcess = spawn(
    findPython(),
    ['-m', 'uvicorn', 'apps.api.main:app', '--host', '127.0.0.1', '--port', String(backendPort)],
    { cwd: root, env: { ...process.env, PYTHONPATH: root }, stdio: 'pipe' },
  )
  pythonProcess.stdout?.on('data', (data: Buffer) => {
    mainWindow?.webContents.send('backend:stdout', data.toString())
  })
  pythonProcess.stderr?.on('data', (data: Buffer) => {
    mainWindow?.webContents.send('backend:stderr', data.toString())
  })
  pythonProcess.on('exit', () => {
    pythonProcess = null
  })
}

function stopBackend(): void {
  if (!pythonProcess) return
  pythonProcess.kill()
  pythonProcess = null
}

function buildMenu(language: 'zh' | 'en' = 'en'): void {
  const root = getProjectRoot()
  const zh = language === 'zh'
  Menu.setApplicationMenu(Menu.buildFromTemplate([
    {
      id: 'file',
      label: zh ? '文件' : 'File',
      submenu: [
        { label: zh ? '打开项目文件夹' : 'Open Project Folder', click: () => shell.openPath(root) },
        { label: zh ? '打开项目数据' : 'Open Project Data', click: () => shell.openPath(path.join(root, 'data', 'app')) },
        { type: 'separator' },
        { role: 'quit', label: zh ? '退出' : 'Quit' },
      ],
    },
    {
      id: 'view',
      label: zh ? '视图' : 'View',
      submenu: [
        { role: 'reload', label: zh ? '重新加载' : 'Reload' },
        { role: 'toggleDevTools', label: zh ? '开发者工具' : 'Toggle Developer Tools' },
        { role: 'togglefullscreen', label: zh ? '全屏' : 'Toggle Full Screen' },
      ],
    },
    {
      id: 'tools',
      label: zh ? '工具' : 'Tools',
      submenu: [
        { label: zh ? 'API 文档' : 'API Docs', click: () => shell.openExternal(`http://127.0.0.1:${backendPort}/docs`) },
      ],
    },
  ]))
}

function createWindow(): void {
  const backendOrigin = `http://127.0.0.1:${backendPort}`
  mainWindow = new BrowserWindow({
    width: 1440,
    height: 920,
    minWidth: 960,
    minHeight: 680,
    title: 'AlphaLab Quant Workstation',
    backgroundColor: WINDOW_COLORS.light.background,
    titleBarStyle: 'hidden',
    titleBarOverlay: {
      color: WINDOW_COLORS.light.color,
      symbolColor: WINDOW_COLORS.light.symbolColor,
      height: Math.round(TITLE_BAR_HEIGHT * DEFAULT_ZOOM),
    },
    webPreferences: {
      zoomFactor: DEFAULT_ZOOM,
      preload: path.join(__dirname, 'preload.cjs'),
      contextIsolation: true,
      nodeIntegration: false,
      additionalArguments: [
        `--alphalab-api-origin=${backendOrigin}`,
        `--alphalab-api-base=${backendOrigin}/api`,
      ],
    },
  })
  mainWindow.setMenuBarVisibility(false)

  if (VITE_DEV_SERVER_URL) {
    const url = new URL(VITE_DEV_SERVER_URL)
    url.searchParams.set('alphalabApiOrigin', backendOrigin)
    url.searchParams.set('alphalabApiBase', `${backendOrigin}/api`)
    mainWindow.loadURL(url.toString())
  } else {
    mainWindow.loadFile(path.join(DIST, 'index.html'), {
      query: {
        alphalabApiOrigin: backendOrigin,
        alphalabApiBase: `${backendOrigin}/api`,
      },
    })
  }
  mainWindow.on('closed', () => {
    mainWindow = null
  })
}

function senderWindow(event: IpcMainInvokeEvent): BrowserWindow {
  if (!mainWindow || event.sender !== mainWindow.webContents
    || event.senderFrame !== mainWindow.webContents.mainFrame) {
    throw new Error('Window controls are only available to the main window')
  }
  return mainWindow
}

ipcMain.handle('window:setTheme', (event, theme: unknown) => {
  const window = senderWindow(event)
  if (theme !== 'light' && theme !== 'dark') throw new Error('Unsupported theme')
  const colors = WINDOW_COLORS[theme]
  window.setBackgroundColor(colors.background)
  if (process.platform !== 'darwin') {
    window.setTitleBarOverlay({ color: colors.color, symbolColor: colors.symbolColor })
  }
})

ipcMain.handle('window:showMenu', (event, menu: unknown, x: unknown, y: unknown, language: unknown) => {
  const window = senderWindow(event)
  if (menu !== 'file' && menu !== 'view' && menu !== 'tools') throw new Error('Unsupported menu')
  if (language !== 'zh' && language !== 'en') throw new Error('Unsupported language')
  if (typeof x !== 'number' || typeof y !== 'number' || !Number.isFinite(x) || !Number.isFinite(y)) {
    throw new Error('Invalid menu position')
  }
  const zoom = window.webContents.getZoomFactor()
  const [width, height] = window.getContentSize()
  buildMenu(language)
  window.setMenuBarVisibility(false)
  Menu.getApplicationMenu()?.getMenuItemById(menu)?.submenu?.popup({
    window,
    x: Math.max(0, Math.min(width, Math.round(x * zoom))),
    y: Math.max(0, Math.min(height, Math.round(y * zoom))),
  })
})

app.whenReady().then(async () => {
  await startBackend()
  buildMenu()
  createWindow()
  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow()
  })
})

app.on('before-quit', stopBackend)
app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit()
})
