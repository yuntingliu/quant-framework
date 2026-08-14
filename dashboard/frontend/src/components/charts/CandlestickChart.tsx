import { useEffect, useMemo, useRef } from "react"
import {
  CandlestickSeries,
  ColorType,
  CrosshairMode,
  HistogramSeries,
  createChart,
  type CandlestickData,
  type HistogramData,
  type Time,
} from "lightweight-charts"

import type { MarketBar } from "@/lib/api"
import type { IndicatorId } from "@/hooks/useIndicatorSelection"
import { addIndicatorSeries } from "@/lib/chartIndicators"

interface CandlestickChartProps {
  rows: MarketBar[]
  selectedIndicators: IndicatorId[]
  height?: number
}

export function CandlestickChart({ rows, selectedIndicators, height = 420 }: CandlestickChartProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const orderedRows = useMemo(
    () => [...rows]
      .filter((row) => (
        row.date
        && Number.isFinite(row.open)
        && Number.isFinite(row.high)
        && Number.isFinite(row.low)
        && Number.isFinite(row.close)
      ))
      .sort((left, right) => left.date.localeCompare(right.date)),
    [rows],
  )

  useEffect(() => {
    const container = containerRef.current
    if (!container || orderedRows.length === 0) return

    const chart = createChart(container, {
      width: container.clientWidth,
      height,
      layout: {
        background: { type: ColorType.Solid, color: "#101114" },
        textColor: "#8f99a6",
        fontFamily: '"IBM Plex Mono", ui-monospace, monospace',
        fontSize: 11,
      },
      grid: {
        vertLines: { color: "rgba(143, 153, 166, 0.08)" },
        horzLines: { color: "rgba(143, 153, 166, 0.08)" },
      },
      crosshair: { mode: CrosshairMode.Normal },
      rightPriceScale: {
        borderColor: "#2f333b",
        scaleMargins: { top: 0.08, bottom: 0.25 },
      },
      timeScale: {
        borderColor: "#2f333b",
        timeVisible: false,
        rightOffset: 2,
      },
      handleScroll: true,
      handleScale: true,
    })

    const candles = chart.addSeries(CandlestickSeries, {
      upColor: "#4dbd8b",
      downColor: "#ef6b73",
      borderUpColor: "#4dbd8b",
      borderDownColor: "#ef6b73",
      wickUpColor: "#72d9aa",
      wickDownColor: "#ff8a91",
      priceLineVisible: false,
    })
    candles.setData(orderedRows.map((row) => ({
      time: row.date as Time,
      open: row.open,
      high: row.high,
      low: row.low,
      close: row.close,
    })) as CandlestickData<Time>[])

    addIndicatorSeries(
      chart,
      orderedRows.map((row) => ({ time: row.date, close: row.close })),
      selectedIndicators,
    )

    const volume = chart.addSeries(HistogramSeries, {
      priceFormat: { type: "volume" },
      priceScaleId: "volume",
      priceLineVisible: false,
      lastValueVisible: false,
    })
    volume.priceScale().applyOptions({
      scaleMargins: { top: 0.78, bottom: 0 },
    })
    volume.setData(orderedRows.map((row) => ({
      time: row.date as Time,
      value: Number.isFinite(row.volume) ? row.volume : 0,
      color: row.close >= row.open ? "rgba(77, 189, 139, 0.38)" : "rgba(239, 107, 115, 0.38)",
    })) as HistogramData<Time>[])

    chart.timeScale().fitContent()
    const resizeObserver = new ResizeObserver((entries) => {
      const width = entries[0]?.contentRect.width
      if (width) chart.applyOptions({ width, height })
    })
    resizeObserver.observe(container)

    return () => {
      resizeObserver.disconnect()
      chart.remove()
    }
  }, [height, orderedRows, selectedIndicators])

  return <div ref={containerRef} className="candlestick-chart" style={{ height }} />
}
