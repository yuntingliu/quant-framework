import assert from "node:assert/strict"
import { readFile } from "node:fs/promises"
import { after, before, test } from "node:test"
import { fileURLToPath } from "node:url"
import { createElement, type ReactElement } from "react"
import { renderToStaticMarkup } from "react-dom/server"
import postcss from "postcss"
import { createServer, type ViteDevServer } from "vite"

let server: ViteDevServer
let SafeMarkdown: (props: { children: string; className?: string }) => ReactElement

before(async () => {
  server = await createServer({
    configFile: false,
    root: fileURLToPath(new URL("../", import.meta.url)),
    resolve: { alias: { "@": fileURLToPath(new URL("../src", import.meta.url)) } },
    esbuild: { jsx: "automatic" },
    optimizeDeps: { noDiscovery: true, include: [] },
    server: { middlewareMode: true, watch: null },
  })
  ;({ SafeMarkdown } = await server.ssrLoadModule("/src/components/shared/SafeMarkdown.tsx"))
})

after(async () => { await server?.close() })

const render = (children: string) => renderToStaticMarkup(createElement(SafeMarkdown, { children }))

test("report headings, paragraphs and lists retain their Markdown hierarchy", () => {
  const html = render("# 研究报告\n\n正文说明。\n\n## 绩效指标\n\n### 风险提示\n\n- 停牌\n- 涨跌停\n\n1. 检查数据\n2. 运行回测")
  assert.match(html, /class="safe-markdown /)
  assert.match(html, /<h1>研究报告<\/h1>/)
  assert.match(html, /<h2>绩效指标<\/h2>/)
  assert.match(html, /<h3>风险提示<\/h3>/)
  assert.match(html, /<p>正文说明。<\/p>/)
  assert.match(html, /<ul>\s*<li>停牌<\/li>/)
  assert.match(html, /<ol>\s*<li>检查数据<\/li>/)
})

test("report tables preserve numeric alignment in a horizontally scrollable grid", () => {
  const html = render("| 指标 | 数值 |\n| :--- | ---: |\n| 成功成交 | 68 |\n| 尝试成交 | 82 |")
  assert.match(html, /class="safe-markdown-table max-w-full overflow-x-auto"/)
  assert.match(html, /<table[^>]*>/)
  assert.match(html, /<th style="text-align:right">数值<\/th>/)
  assert.match(html, /<td style="text-align:right">68<\/td>/)
  assert.equal((html.match(/<td[ >]/g) ?? []).length, 4)
})

test("document formatting preserves code and safe HTML without enabling scripts", () => {
  const html = render([
    "`research_valid`\n\n```python\nif ready:\n    run()\n```",
    '<details><summary>详情</summary><p>证据</p></details>',
    '<script>alert("unsafe")</script><img src="/plot.png" onerror="alert(1)">',
    '<a href="javascript:alert(1)">无效链接</a>',
    '[文档](https://example.com/docs)',
  ].join("\n\n"))
  assert.match(html, /<code>research_valid<\/code>/)
  assert.match(html, /<pre><code class="language-python">if ready:\n {4}run\(\)/)
  assert.match(html, /<details><summary>详情<\/summary>/)
  assert.match(html, /target="_blank" rel="noreferrer"/)
  assert.doesNotMatch(html, /<script|onerror=|javascript:/)
})

test("document styles explicitly restore headings, list markers and every cell border", async () => {
  const css = postcss.parse(await readFile(new URL("../src/components/shared/SafeMarkdown.css", import.meta.url), "utf8"))
  const declarations = (selector: string) => {
    const values: Record<string, string> = {}
    css.walkRules(selector, (rule) => { rule.walkDecls((declaration) => { values[declaration.prop] = declaration.value }) })
    return values
  }
  for (const level of [1, 2, 3, 4, 5, 6]) {
    assert.ok(declarations(`.safe-markdown h${level}`)["font-size"])
  }
  assert.equal(declarations(".safe-markdown :where(h1, h2, h3, h4, h5, h6)")["font-weight"], "600")
  assert.equal(declarations(".safe-markdown :where(th, td)").border, "1px solid hsl(var(--border))")
  assert.equal(declarations(".safe-markdown table")["border-collapse"], "collapse")
  assert.equal(declarations(".safe-markdown th").background, "hsl(var(--muted))")
  assert.equal(declarations(".safe-markdown ul")["list-style-type"], "disc")
  assert.equal(declarations(".safe-markdown ol")["list-style-type"], "decimal")
  assert.equal(declarations(".safe-markdown pre")["overflow-x"], "auto")
  assert.equal(declarations(".safe-markdown pre")["white-space"], "pre")
})
