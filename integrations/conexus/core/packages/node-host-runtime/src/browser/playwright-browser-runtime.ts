import { access } from 'node:fs/promises'
import { constants as fsConstants } from 'node:fs'
import { chromium, type Browser, type BrowserContext, type Page } from 'playwright-core'
import {
  normalizeBrowserProfileChannel,
  normalizeBrowserUrl,
  type BrowserActionInput,
  type BrowserLoadError,
  type BrowserProfileChannel,
  type BrowserRuntimeActionResult,
  type BrowserRuntimePreviewResult,
  type BrowserRuntimeSnapshotResult,
  type SemanticSnapshot
} from '@conexus/runtime-core'

interface BrowserSession {
  channel: BrowserProfileChannel
  context: BrowserContext
  page: Page
  loadError: BrowserLoadError | null
  close: () => Promise<void>
}

export interface PlaywrightBrowserRuntimeOptions {
  executableCandidates: () => Promise<string[]> | string[]
  headless?: boolean
  profileDirectory?: (channel: BrowserProfileChannel, executablePath: string) => string
  launchArgs?: string[]
  environment?: Record<string, string | undefined>
  ignoreHTTPSErrors?: boolean
  viewport?: { width: number; height: number }
  navigationAllowed?: (url: URL) => Promise<void> | void
}

const SELECTOR = [
  'a[href]', 'button', 'input', 'textarea', 'select', '[role="button"]', '[role="link"]',
  '[role="checkbox"]', '[role="radio"]', '[role="tab"]', '[role="menuitem"]', '[role="option"]',
  '[contenteditable="true"]'
].join(',')

async function settlePage(page: Page, timeoutMs = 1_200): Promise<void> {
  await page.waitForTimeout(180)
  await page.waitForLoadState('domcontentloaded', { timeout: timeoutMs }).catch(() => {})
  await page.waitForLoadState('networkidle', { timeout: timeoutMs }).catch(() => {})
}

async function captureSnapshot(page: Page): Promise<SemanticSnapshot> {
  return page.evaluate(async (selector) => {
    const maxElements = 140
    const maxTextChars = 12_000
    const scroller = document.scrollingElement || document.documentElement
    const originalY = window.scrollY
    const textLines: string[] = []
    const textSeen = new Set<string>()
    const elements: Array<{ id: string; kind: string; label: string; value?: string; disabled?: boolean }> = []
    const elementSeen = new Set<string>()
    let nextId = 0
    const sleep = (ms: number) => new Promise((resolve) => window.setTimeout(resolve, ms))
    const isRendered = (element: HTMLElement) => {
      const style = window.getComputedStyle(element)
      const rect = element.getBoundingClientRect()
      return style.display !== 'none' && style.visibility !== 'hidden'
        && Number.parseFloat(style.opacity || '1') > 0 && rect.width > 0 && rect.height > 0
        && rect.bottom > -200 && rect.right > -200
    }
    const labelOf = (element: HTMLElement) => [
      element.getAttribute('aria-label'), element.getAttribute('title'), element.getAttribute('placeholder'),
      element.getAttribute('name'), (element.innerText || element.textContent || '').replace(/\s+/g, ' ').trim().slice(0, 120)
    ].find((value) => value?.trim()) || ''
    const kindOf = (element: HTMLElement) => {
      const role = (element.getAttribute('role') || '').toLowerCase()
      if (role) return role
      const tag = element.tagName.toLowerCase()
      if (tag === 'a') return 'link'
      if (tag === 'button') return 'button'
      if (tag === 'select') return 'select'
      if (tag === 'textarea') return 'textarea'
      if (tag === 'input') return element.getAttribute('type') || 'text'
      if (element.isContentEditable) return 'editable'
      return tag
    }
    const collect = () => {
      for (const line of (document.body?.innerText || '').split('\n')) {
        const normalized = line.replace(/\s+/g, ' ').trim()
        if (!normalized || textSeen.has(normalized)) continue
        textSeen.add(normalized)
        textLines.push(normalized)
        if (textLines.join('\n').length >= maxTextChars) break
      }
      for (const node of document.querySelectorAll(selector)) {
        if (!(node instanceof HTMLElement) || !isRendered(node)) continue
        let id = node.getAttribute('data-conexus-id')
        if (!id) {
          id = `e${nextId++}`
          node.setAttribute('data-conexus-id', id)
        }
        if (elementSeen.has(id)) continue
        elementSeen.add(id)
        const inputLike = node instanceof HTMLInputElement || node instanceof HTMLTextAreaElement || node instanceof HTMLSelectElement
        elements.push({
          id,
          kind: kindOf(node),
          label: labelOf(node),
          ...(inputLike ? { value: String(node.value || '').slice(0, 200) } : {}),
          ...(node.hasAttribute('disabled') || node.getAttribute('aria-disabled') === 'true' ? { disabled: true } : {})
        })
        if (elements.length >= maxElements) break
      }
    }
    const stepSize = Math.max(320, Math.floor(window.innerHeight * 0.85))
    let lastScrollTop = -1
    for (let step = 0; step < 10; step += 1) {
      collect()
      if (elements.length >= maxElements || textLines.join('\n').length >= maxTextChars) break
      const maxScrollTop = Math.max(0, scroller.scrollHeight - window.innerHeight)
      if (window.scrollY >= maxScrollTop || window.scrollY === lastScrollTop) break
      lastScrollTop = window.scrollY
      window.scrollTo({ top: Math.min(maxScrollTop, window.scrollY + stepSize), behavior: 'auto' })
      await sleep(220)
    }
    window.scrollTo({ top: originalY, behavior: 'auto' })
    await sleep(40)
    return {
      url: location.href,
      title: document.title || '',
      pageText: textLines.join('\n').slice(0, maxTextChars),
      elements
    }
  }, SELECTOR)
}

async function capturePreview(page: Page): Promise<string | null> {
  try {
    const buffer = await page.screenshot({ type: 'jpeg', quality: 65, fullPage: false, animations: 'disabled' })
    return `data:image/jpeg;base64,${buffer.toString('base64')}`
  } catch {
    return null
  }
}

async function clickElement(page: Page, elementId: string): Promise<{ ok: boolean; message: string }> {
  const locator = page.locator(`[data-conexus-id="${elementId}"]`).first()
  try {
    await locator.scrollIntoViewIfNeeded()
    await locator.click({ timeout: 5_000 })
    return { ok: true, message: `clicked ${elementId}` }
  } catch {
    return page.evaluate((id) => {
      const element = document.querySelector(`[data-conexus-id="${id}"]`)
      if (!(element instanceof HTMLElement)) return { ok: false, message: `element not found: ${id}` }
      element.scrollIntoView({ block: 'center', inline: 'center' })
      element.click()
      return { ok: true, message: `clicked ${id}` }
    }, elementId)
  }
}

async function typeIntoElement(page: Page, elementId: string, text: string): Promise<{ ok: boolean; message: string }> {
  return page.evaluate(({ id, value }) => {
    const element = document.querySelector(`[data-conexus-id="${id}"]`)
    if (!(element instanceof HTMLElement)) return { ok: false, message: `element not found: ${id}` }
    element.scrollIntoView({ block: 'center', inline: 'center' })
    if (element instanceof HTMLInputElement || element instanceof HTMLTextAreaElement) {
      element.focus()
      const descriptor = Object.getOwnPropertyDescriptor(Object.getPrototypeOf(element) as object, 'value')
      if (descriptor?.set) descriptor.set.call(element, value)
      else element.value = value
      element.dispatchEvent(new Event('input', { bubbles: true }))
      element.dispatchEvent(new Event('change', { bubbles: true }))
      return { ok: true, message: `typed into ${id}` }
    }
    if (element instanceof HTMLSelectElement) {
      element.value = value
      element.dispatchEvent(new Event('change', { bubbles: true }))
      return { ok: true, message: `selected ${id}` }
    }
    if (element.isContentEditable) {
      element.focus()
      document.execCommand('selectAll', false)
      document.execCommand('insertText', false, value)
      element.dispatchEvent(new Event('input', { bubbles: true }))
      return { ok: true, message: `typed into ${id}` }
    }
    return { ok: false, message: `element not typeable: ${id}` }
  }, { id: elementId, value: text })
}

export class PlaywrightBrowserRuntime {
  private readonly sessions = new Map<string, BrowserSession>()
  private readonly persistentContexts = new Map<BrowserProfileChannel, Promise<BrowserContext>>()
  private browserPromise: Promise<Browser> | null = null

  constructor(private readonly options: PlaywrightBrowserRuntimeOptions) {}

  async snapshot(sessionId: string, urlHint?: string, channel?: BrowserProfileChannel): Promise<BrowserRuntimeSnapshotResult> {
    const session = await this.ensureSession(sessionId, channel)
    if (urlHint && session.page.url() === 'about:blank') await this.navigate(session, urlHint)
    return this.snapshotSession(session)
  }

  async preview(sessionId: string, urlHint?: string, channel?: BrowserProfileChannel): Promise<BrowserRuntimePreviewResult> {
    const session = await this.ensureSession(sessionId, channel)
    if (urlHint && session.page.url() === 'about:blank') await this.navigate(session, urlHint)
    return {
      url: session.page.url(),
      title: await session.page.title().catch(() => ''),
      loadError: session.loadError,
      previewDataUrl: await capturePreview(session.page)
    }
  }

  async action(sessionId: string, action: BrowserActionInput, urlHint?: string, channel?: BrowserProfileChannel): Promise<BrowserRuntimeActionResult> {
    const session = await this.ensureSession(sessionId, channel)
    if (urlHint && session.page.url() === 'about:blank' && action.kind !== 'navigate') await this.navigate(session, urlHint)
    try {
      if (action.kind === 'refresh') {
        await session.page.reload({ waitUntil: 'domcontentloaded', timeout: 20_000 }).catch((error: unknown) => {
          session.loadError = this.loadError(error, session.page.url())
        })
        const result = await this.snapshotSession(session)
        return this.actionSnapshot(result, result.loadError?.description ?? `snapshot: ${result.snapshot.elements.length} elements`)
      }
      if (action.kind === 'focus') {
        await session.page.bringToFront()
        const result = await this.snapshotSession(session)
        return this.actionSnapshot(result, result.loadError?.description ?? 'focused browser window')
      }
      if (action.kind === 'navigate') {
        await this.navigate(session, action.url)
        const result = await this.snapshotSession(session)
        return this.actionSnapshot(result, result.loadError?.description ?? `navigated to ${result.snapshot.url}`)
      }
      let operation: { ok: boolean; message: string }
      if (action.kind === 'scroll') {
        const deltaY = action.deltaY ?? 400
        operation = await session.page.evaluate((delta) => {
          window.scrollBy(0, delta)
          return { ok: true, message: `scrolled by ${delta}px` }
        }, deltaY)
        await settlePage(session.page, 800)
      } else if (action.kind === 'click') {
        operation = await clickElement(session.page, action.elementId)
        await settlePage(session.page, 1_500)
      } else {
        operation = await typeIntoElement(session.page, action.elementId, action.text)
        await settlePage(session.page, 900)
      }
      const result = await this.snapshotSession(session)
      return { ...this.actionSnapshot(result, result.loadError?.description ?? operation.message), success: operation.ok && !result.loadError }
    } catch (error) {
      return {
        success: false,
        message: error instanceof Error ? error.message : String(error),
        loadError: session.loadError,
        previewDataUrl: await capturePreview(session.page)
      }
    }
  }

  async disposeSession(sessionId: string): Promise<void> {
    const session = this.sessions.get(sessionId)
    if (!session) return
    this.sessions.delete(sessionId)
    await session.close()
  }

  async disposeSessionsWithPrefix(prefix: string): Promise<void> {
    await Promise.all([...this.sessions.keys()].filter((id) => id.startsWith(prefix)).map((id) => this.disposeSession(id)))
  }

  async dispose(): Promise<void> {
    await Promise.all([...this.sessions.keys()].map((id) => this.disposeSession(id)))
    const persistent = await Promise.all([...this.persistentContexts.values()].map((promise) => promise.catch(() => null)))
    this.persistentContexts.clear()
    await Promise.all(persistent.map((context) => context?.close().catch(() => {})))
    const browser = await this.browserPromise?.catch(() => null)
    this.browserPromise = null
    await browser?.close().catch(() => {})
  }

  private actionSnapshot(result: BrowserRuntimeSnapshotResult, message: string): BrowserRuntimeActionResult {
    return { success: !result.loadError, message, snapshot: result.snapshot, loadError: result.loadError, previewDataUrl: result.previewDataUrl }
  }

  private async resolveExecutablePath(): Promise<string> {
    const candidates = [...new Set((await this.options.executableCandidates()).filter(Boolean))]
    for (const candidate of candidates) {
      try {
        await access(candidate, fsConstants.F_OK)
        return candidate
      } catch {}
    }
    throw new Error('No Chromium-compatible browser was found. Configure a Chromium executable path.')
  }

  private async browser(): Promise<Browser> {
    if (!this.browserPromise) {
      this.browserPromise = this.resolveExecutablePath()
        .then((executablePath) => chromium.launch({
          executablePath,
          headless: true,
          env: this.options.environment
            ? { ...process.env, ...this.options.environment }
            : undefined,
          args: ['--no-sandbox', '--disable-dev-shm-usage', ...(this.options.launchArgs ?? [])]
        }))
        .catch((error) => {
          this.browserPromise = null
          throw error
        })
    }
    return this.browserPromise
  }

  private async persistentContext(channel: BrowserProfileChannel): Promise<BrowserContext> {
    let promise = this.persistentContexts.get(channel)
    if (!promise) {
      promise = (async () => {
        const executablePath = await this.resolveExecutablePath()
        const directory = this.options.profileDirectory?.(channel, executablePath)
        if (!directory) throw new Error(`No browser profile directory is configured for channel: ${channel}.`)
        const context = await chromium.launchPersistentContext(directory, {
          executablePath,
          headless: false,
          env: this.options.environment
            ? { ...process.env, ...this.options.environment }
            : undefined,
          viewport: this.options.viewport ?? { width: 1280, height: 920 },
          ignoreHTTPSErrors: this.options.ignoreHTTPSErrors ?? true,
          args: ['--no-first-run', '--no-default-browser-check', ...(this.options.launchArgs ?? [])]
        })
        await this.configureContext(context)
        return context
      })().catch((error) => {
        this.persistentContexts.delete(channel)
        throw error
      })
      this.persistentContexts.set(channel, promise)
    }
    return promise
  }

  private async ensureSession(sessionId: string, rawChannel?: BrowserProfileChannel): Promise<BrowserSession> {
    const channel = normalizeBrowserProfileChannel(rawChannel)
    const existing = this.sessions.get(sessionId)
    if (existing?.channel === channel) return existing
    if (existing) await this.disposeSession(sessionId)
    let context: BrowserContext
    let ownsContext = false
    if (this.options.headless ?? true) {
      context = await (await this.browser()).newContext({
        viewport: this.options.viewport ?? { width: 1280, height: 920 },
        ignoreHTTPSErrors: this.options.ignoreHTTPSErrors ?? true
      })
      await this.configureContext(context)
      ownsContext = true
    } else {
      context = await this.persistentContext(channel)
    }
    const page = await context.newPage()
    page.setDefaultTimeout(15_000)
    const session: BrowserSession = {
      channel,
      context,
      page,
      loadError: null,
      close: ownsContext
        ? async () => context.close().catch(() => {})
        : async () => page.close().catch(() => {})
    }
    this.sessions.set(sessionId, session)
    return session
  }

  private async navigate(session: BrowserSession, rawUrl: string): Promise<void> {
    const normalized = normalizeBrowserUrl(rawUrl)
    const url = new URL(normalized)
    if (url.protocol !== 'http:' && url.protocol !== 'https:') throw new Error('Browser navigation only supports HTTP and HTTPS URLs.')
    await this.options.navigationAllowed?.(url)
    try {
      await session.page.goto(url.href, { waitUntil: 'domcontentloaded', timeout: 20_000 })
      session.loadError = null
      await settlePage(session.page, 2_200)
    } catch (error) {
      session.loadError = this.loadError(error, url.href)
    }
  }

  private async configureContext(context: BrowserContext): Promise<void> {
    if (!this.options.navigationAllowed) return
    await context.route('**/*', async (route) => {
      try {
        const requestUrl = new URL(route.request().url())
        if (requestUrl.protocol === 'http:' || requestUrl.protocol === 'https:') {
          await this.options.navigationAllowed?.(requestUrl)
        } else if (!['about:', 'blob:', 'data:'].includes(requestUrl.protocol)) {
          await route.abort('blockedbyclient')
          return
        }
        await route.continue()
      } catch {
        await route.abort('blockedbyclient').catch(() => {})
      }
    })
  }

  private loadError(error: unknown, url: string): BrowserLoadError {
    return { code: null, description: error instanceof Error ? error.message : String(error), url }
  }

  private async snapshotSession(session: BrowserSession): Promise<BrowserRuntimeSnapshotResult> {
    await settlePage(session.page, 1_800)
    return { snapshot: await captureSnapshot(session.page), loadError: session.loadError, previewDataUrl: await capturePreview(session.page) }
  }
}
