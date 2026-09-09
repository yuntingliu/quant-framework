/**
 * Technical indicator math for chart overlays. Pure functions over close
 * arrays; every result is aligned to the input length with `null` during the
 * warm-up window, so values zip 1:1 with bar times for lightweight-charts.
 */

export type IndicatorSeries = Array<number | null>

export function sma(values: number[], period: number): IndicatorSeries {
  const out: IndicatorSeries = new Array(values.length).fill(null)
  if (period <= 0) return out
  let sum = 0
  for (let i = 0; i < values.length; i++) {
    sum += values[i]
    if (i >= period) sum -= values[i - period]
    if (i >= period - 1) out[i] = sum / period
  }
  return out
}

export function ema(values: number[], period: number): IndicatorSeries {
  const out: IndicatorSeries = new Array(values.length).fill(null)
  if (period <= 0 || values.length < period) return out
  const k = 2 / (period + 1)
  let prev = values.slice(0, period).reduce((a, b) => a + b, 0) / period
  out[period - 1] = prev
  for (let i = period; i < values.length; i++) {
    prev = values[i] * k + prev * (1 - k)
    out[i] = prev
  }
  return out
}

export interface BollingerBands {
  middle: IndicatorSeries
  upper: IndicatorSeries
  lower: IndicatorSeries
}

export function bollinger(values: number[], period = 20, k = 2): BollingerBands {
  const middle = sma(values, period)
  const upper: IndicatorSeries = new Array(values.length).fill(null)
  const lower: IndicatorSeries = new Array(values.length).fill(null)
  for (let i = period - 1; i < values.length; i++) {
    const mean = middle[i]
    if (mean === null) continue
    let variance = 0
    for (let j = i - period + 1; j <= i; j++) {
      variance += (values[j] - mean) ** 2
    }
    const std = Math.sqrt(variance / period)
    upper[i] = mean + k * std
    lower[i] = mean - k * std
  }
  return { middle, upper, lower }
}

export function rsi(values: number[], period = 14): IndicatorSeries {
  const out: IndicatorSeries = new Array(values.length).fill(null)
  if (values.length <= period) return out
  let gain = 0
  let loss = 0
  for (let i = 1; i <= period; i++) {
    const change = values[i] - values[i - 1]
    if (change >= 0) gain += change
    else loss -= change
  }
  let avgGain = gain / period
  let avgLoss = loss / period
  out[period] = avgLoss === 0 ? 100 : 100 - 100 / (1 + avgGain / avgLoss)
  for (let i = period + 1; i < values.length; i++) {
    const change = values[i] - values[i - 1]
    avgGain = (avgGain * (period - 1) + Math.max(change, 0)) / period
    avgLoss = (avgLoss * (period - 1) + Math.max(-change, 0)) / period
    out[i] = avgLoss === 0 ? 100 : 100 - 100 / (1 + avgGain / avgLoss)
  }
  return out
}

export interface MacdSeries {
  macd: IndicatorSeries
  signal: IndicatorSeries
  histogram: IndicatorSeries
}

export function macd(values: number[], fast = 12, slow = 26, signalPeriod = 9): MacdSeries {
  const fastEma = ema(values, fast)
  const slowEma = ema(values, slow)
  const macdLine: IndicatorSeries = values.map((_, i) =>
    fastEma[i] !== null && slowEma[i] !== null ? (fastEma[i] as number) - (slowEma[i] as number) : null,
  )
  // Signal = EMA of the MACD line, starting where the MACD line begins.
  const start = macdLine.findIndex((value) => value !== null)
  const signal: IndicatorSeries = new Array(values.length).fill(null)
  const histogram: IndicatorSeries = new Array(values.length).fill(null)
  if (start >= 0) {
    const compact = macdLine.slice(start) as number[]
    const compactSignal = ema(compact, signalPeriod)
    for (let i = 0; i < compact.length; i++) {
      const sig = compactSignal[i]
      signal[start + i] = sig
      if (sig !== null) histogram[start + i] = compact[i] - sig
    }
  }
  return { macd: macdLine, signal, histogram }
}

/** Zip an indicator series with bar times into lightweight-charts line data. */
export function toLineData<T>(
  times: T[],
  series: IndicatorSeries,
): Array<{ time: T; value: number }> {
  const out: Array<{ time: T; value: number }> = []
  for (let i = 0; i < times.length; i++) {
    const value = series[i]
    if (value !== null && Number.isFinite(value)) {
      out.push({ time: times[i], value })
    }
  }
  return out
}
