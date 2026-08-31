import assert from "node:assert/strict"
import { describe, it } from "node:test"

import { bollinger, ema, macd, rsi, sma, toLineData } from "../src/lib/indicators.ts"

describe("technical indicator alignment", () => {
  it("keeps warm-up nulls aligned with the source values", () => {
    assert.deepEqual(sma([1, 2, 3, 4], 3), [null, null, 2, 3])
    assert.deepEqual(ema([1, 2, 3, 4], 3), [null, null, 2, 3])
    assert.deepEqual(toLineData(["a", "b", "c"], [null, Number.NaN, 2]), [
      { time: "c", value: 2 },
    ])
  })

  it("returns finite, aligned oscillator and band series", () => {
    const values = Array.from({ length: 60 }, (_, index) => 100 + index)
    const bands = bollinger(values, 20)
    const momentum = rsi(values, 14)
    const convergence = macd(values)

    for (const series of [
      bands.middle,
      bands.upper,
      bands.lower,
      momentum,
      convergence.macd,
      convergence.signal,
      convergence.histogram,
    ]) {
      assert.equal(series.length, values.length)
      assert.ok(series.filter((value) => value !== null).every(Number.isFinite))
    }
    assert.equal(momentum.at(-1), 100)
  })
})
