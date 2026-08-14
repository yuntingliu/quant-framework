/**
 * Bridge between the pure indicator math (lib/indicators) and
 * lightweight-charts v5: overlays MA/EMA/BOLL on the candle pane and renders
 * RSI/MACD in sub-panes.
 */
import {
  HistogramSeries,
  LineSeries,
  type HistogramData,
  type IChartApi,
  type LineData,
  type Time,
} from "lightweight-charts"

import type { IndicatorId } from "@/hooks/useIndicatorSelection"
import {
  bollinger,
  ema,
  macd,
  rsi,
  sma,
  toLineData,
  type IndicatorSeries,
} from "@/lib/indicators"

export function addIndicatorSeries(
  chart: IChartApi,
  bars: Array<{ time: string; close: number }>,
  selected: IndicatorId[],
): void {
  if (!selected.length || bars.length < 2) return
  const closes = bars.map((bar) => bar.close)
  const times = bars.map((bar) => bar.time as Time)
  const overlay = (series: IndicatorSeries, color: string) => {
    const line = chart.addSeries(LineSeries, {
      color,
      lineWidth: 1,
      priceLineVisible: false,
      lastValueVisible: false,
      crosshairMarkerVisible: false,
    })
    line.setData(toLineData(times, series) as LineData[])
  }
  if (selected.includes("ma20")) overlay(sma(closes, 20), "#fbbf24")
  if (selected.includes("ma60")) overlay(sma(closes, 60), "#a78bfa")
  if (selected.includes("ema12")) overlay(ema(closes, 12), "#38bdf8")
  if (selected.includes("boll")) {
    const bands = bollinger(closes, 20, 2)
    overlay(bands.upper, "rgba(148,163,184,0.55)")
    overlay(bands.middle, "rgba(148,163,184,0.3)")
    overlay(bands.lower, "rgba(148,163,184,0.55)")
  }
  let paneIndex = 1
  if (selected.includes("rsi")) {
    const line = chart.addSeries(LineSeries, {
      color: "#fb7185",
      lineWidth: 1,
      priceLineVisible: false,
      lastValueVisible: true,
    }, paneIndex)
    line.setData(toLineData(times, rsi(closes, 14)) as LineData[])
    paneIndex += 1
  }
  if (selected.includes("macd")) {
    const { macd: macdLine, signal, histogram } = macd(closes)
    const hist = chart.addSeries(HistogramSeries, {
      priceFormat: { type: "price", precision: 3, minMove: 0.001 },
      priceLineVisible: false,
      lastValueVisible: false,
    }, paneIndex)
    hist.setData(
      toLineData(times, histogram).map((point) => ({
        ...point,
        color: point.value >= 0 ? "rgba(34,197,94,0.5)" : "rgba(239,68,68,0.5)",
      })) as HistogramData[],
    )
    const macdSeries = chart.addSeries(LineSeries, {
      color: "#38bdf8", lineWidth: 1, priceLineVisible: false, lastValueVisible: false,
    }, paneIndex)
    macdSeries.setData(toLineData(times, macdLine) as LineData[])
    const signalSeries = chart.addSeries(LineSeries, {
      color: "#fbbf24", lineWidth: 1, priceLineVisible: false, lastValueVisible: false,
    }, paneIndex)
    signalSeries.setData(toLineData(times, signal) as LineData[])
  }
}
