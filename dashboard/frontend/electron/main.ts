/**
 * AlphaLab desktop main process.
 */
import { app, BrowserWindow, dialog, Menu, shell } from 'electron'
import { spawn, type ChildProcess } from 'child_process'
import path from 'path'
import fs from 'fs'
import { createServer } from 'net'
import { fileURLToPath } from 'url'

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const VITE_DEV_SERVER_URL = process.env.VITE_DEV_SERVER_URL
const IS_DEV = !!VITE_DEV_SERVER_URL
const DEFAULT_BACKEND_PORT = 8000

let mainWindow: BrowserWindow | null = null
let pythonProcess: ChildProcess | null = null
const backendPort = DEFAULT_BACKEND_PORT

function getProjectRoot(): string {
  if (process.env.ALPHALAB_PROJECT_ROOT && fs.existsSync(process.env.ALPHALAB_PROJECT_ROOT)) {
    return process.env.ALPHALAB_PROJECT_ROOT
  }
  return path.resolve(__dirname, '..', '..', '..')
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

async function isAlphaLabBackend(port: number): Promise<boolean> {
  try {
    const response = await fetch(`http://127.0.0.1:${port}/`, { signal: AbortSignal.timeout(1_000) })
    if (!response.ok) return false
    const payload = await response.json() as { status?: unknown; name?: unknown }
    return payload.status === 'ok' && payload.name === 'AlphaLab Barebone API'
  } catch {
    return false
  }
}

async function startBackend(): Promise<void> {
  if (IS_DEV) return
  if (!(await isPortAvailable(backendPort))) {
    if (await isAlphaLabBackend(backendPort)) return
    throw new Error(`Port ${backendPort} is required by the local Conexus Harness but is already in use.`)
  }
  const root = getProjectRoot()
  pythonProcess = spawn(
    findPython(),
    ['-m', 'uvicorn', 'dashboard.backend.main:app', '--host', '127.0.0.1', '--port', String(backendPort)],
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

async function waitForBackend(timeoutMs = 20_000): Promise<void> {
  const deadline = Date.now() + timeoutMs
  const url = `http://127.0.0.1:${backendPort}/`
  while (Date.now() < deadline) {
    try {
      const response = await fetch(url, { signal: AbortSignal.timeout(1_000) })
      if (response.ok) return
    } catch {
      // The sidecar may still be importing pandas/pyarrow; retry until the deadline.
    }
    await new Promise((resolve) => setTimeout(resolve, 200))
  }
  throw new Error(`AlphaLab backend did not become ready within ${timeoutMs}ms`)
}

function stopBackend(): void {
  if (!pythonProcess) return
  pythonProcess.kill()
  pythonProcess = null
}

function buildMenu(): void {
  const root = getProjectRoot()
  Menu.setApplicationMenu(Menu.buildFromTemplate([
    {
      label: 'File',
      submenu: [
        { label: 'Open Project Folder', click: () => shell.openPath(root) },
        { label: 'Open Strategies Folder', click: () => shell.openPath(path.join(root, 'alphalab', 'strategies')) },
        { type: 'separator' },
        { role: 'quit' },
      ],
    },
    {
      label: 'View',
      submenu: [
        { role: 'reload' },
        { role: 'toggleDevTools' },
        { role: 'togglefullscreen' },
      ],
    },
    {
      label: 'Tools',
      submenu: [
        { label: 'API Docs', click: () => shell.openExternal(`http://127.0.0.1:${backendPort}/docs`) },
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
    title: 'AlphaLab Barebone',
    webPreferences: {
      preload: path.join(__dirname, 'preload.cjs'),
      contextIsolation: true,
      nodeIntegration: false,
      additionalArguments: [
        `--alphalab-api-origin=${backendOrigin}`,
        `--alphalab-api-base=${backendOrigin}/api`,
      ],
    },
  })

  if (VITE_DEV_SERVER_URL) {
    const url = new URL(VITE_DEV_SERVER_URL)
    url.searchParams.set('alphalabApiOrigin', backendOrigin)
    url.searchParams.set('alphalabApiBase', `${backendOrigin}/api`)
    mainWindow.loadURL(url.toString())
  } else {
    const url = new URL('/app/', backendOrigin)
    url.searchParams.set('alphalabApiOrigin', backendOrigin)
    url.searchParams.set('alphalabApiBase', `${backendOrigin}/api`)
    mainWindow.loadURL(url.toString())
  }
  mainWindow.on('closed', () => {
    mainWindow = null
  })
}

app.whenReady().then(async () => {
  await startBackend()
  await waitForBackend()
  buildMenu()
  createWindow()
  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow()
  })
}).catch((error) => {
  dialog.showErrorBox('AlphaLab could not start', error instanceof Error ? error.message : String(error))
  app.quit()
})

app.on('before-quit', stopBackend)
app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit()
})
