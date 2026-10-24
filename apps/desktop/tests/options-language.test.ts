import assert from "node:assert/strict"
import test from "node:test"
import fs from "node:fs"
import ts from "typescript"
import { optionStrings, translateOptions } from "../src/lib/optionsText.ts"

test("option language switch translates retained dynamic labels and diagnostic dates", () => {
  assert.equal(translateOptions("上证50ETF 2026-11-25 沽 2.900", "en"), "SSE 50 ETF 2026-11-25 Put 2.900")
  assert.equal(translateOptions("2026-10-28: fit quality or strike-arbitrage diagnostic requires review", "zh"), "2026-10-28: 拟合质量或执行价套利诊断需要复核")
  assert.equal(translateOptions("ETF 期权", "en"), "ETF options")
  assert.equal(translateOptions("Vega / 波动率 +1pp", "en"), "Vega / IV +1pp")
  for (const [zh,en] of Object.entries(optionStrings)) {
    assert.ok(!/[\u4e00-\u9fff]/.test(en), `untranslated English copy: ${zh}`)
    assert.equal(translateOptions(zh,"en"),en)
  }
})

test("option views do not render raw Chinese JSX text outside localization", () => {
  const source=fs.readFileSync(new URL("../src/widgets/data/OptionsWorkbench.tsx", import.meta.url),"utf8")
  const parsed=ts.createSourceFile("OptionsWorkbench.tsx",source,ts.ScriptTarget.Latest,true,ts.ScriptKind.TSX)
  const missing: string[]=[]
  function walk(node: ts.Node) { if(ts.isJsxText(node)&&/[\u4e00-\u9fff]/.test(node.text)) missing.push(node.text); ts.forEachChild(node,walk) }
  walk(parsed)
  assert.deepEqual(missing,[])
  assert.ok(source.includes("useOptionsI18n"))
})
