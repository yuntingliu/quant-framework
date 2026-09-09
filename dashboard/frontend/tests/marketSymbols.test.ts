import assert from "node:assert/strict"
import { it } from "node:test"

import { normalizeMarketSymbol } from "../src/lib/marketSymbols.ts"

it("matches RQData searches to the canonical securities list", () => {
  const instruments = ["000001.SZ", "600000.SH", "920001.BJ"]
  for (const [query, expected] of [
    [" 000001.xshe ", "000001.SZ"],
    ["600000.XSHG", "600000.SH"],
    ["920001.XBEI", "920001.BJ"],
    ["000001.SZ", "000001.SZ"],
  ]) {
    assert.deepEqual(instruments.filter((symbol) => symbol.includes(normalizeMarketSymbol(query))), [expected])
  }
  assert.equal(normalizeMarketSymbol("平安银行"), "平安银行")
  assert.equal(normalizeMarketSymbol("000001"), "000001")
  assert.equal(normalizeMarketSymbol("000001.BAD"), "000001.BAD")
})
