/**
 * AlphaLab desktop main process.
 */
import { app, BrowserWindow, Menu, shell } from 'electron'
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

function buildMenu(): void {
  const root = getProjectRoot()
  Menu.setApplicationMenu(Menu.buildFromTemplate([
    {
      label: 'File',
      submenu: [
        { label: 'Open Project Folder', click: () => shell.openPath(root) },
        { label: 'Open Project Data', click: () => shell.openPath(path.join(root, 'data', 'app')) },
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
      zoomFactor: 1.25,
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
