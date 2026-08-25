import {
  forwardRef,
  useEffect,
  useImperativeHandle,
  useMemo,
  useRef,
} from "react"
import {
  dispose,
  init,
  type Chart,
  type Crosshair,
  type DeepPartial,
  type KLineData,
  type Styles,
} from "klinecharts"

import { useTheme } from "@/contexts/ThemeContext"
import type { MarketBar } from "@/lib/api"

export type MarketChartType = "candle_solid" | "area"

export interface KLineTerminalChartHandle {
  createOverlay: (name: "segment" | "horizontalStraightLine") => void
  clearOverlays: () => void
  resetView: () => void
}

interface KLineTerminalChartProps {
  rows: MarketBar[]
  symbol: string
  chartType: MarketChartType
  indicators: string[]
  compact?: boolean
  onCrosshairChange?: (bar: MarketBar | null) => void
}

function timestampFor(date: string): number {
  return Date.parse(`${date}T00:00:00+08:00`)
}

function chartStyles(theme: "light" | "dark", chartType: MarketChartType, compact: boolean): DeepPartial<Styles> {
  const dark = theme === "dark"
  const text = dark ? "#919aaa" : "#667085"
  const border = dark ? "#2b313b" : "#dce1e8"
  const grid = dark ? "rgba(145,154,170,0.075)" : "rgba(102,112,133,0.11)"
  const chartTextSize = compact ? 9 : 10
  const tooltipMargins = compact
    ? { marginLeft: 3, marginTop: 2, marginRight: 3, marginBottom: 2 }
    : { marginLeft: 4, marginTop: 3, marginRight: 4, marginBottom: 3 }
  return {
    grid: {
      horizontal: { show: true, color: grid, size: 1, style: "dashed", dashedValue: [2, 2] },
      vertical: { show: true, color: grid, size: 1, style: "dashed", dashedValue: [2, 2] },
    },
    candle: {
      type: chartType,
      bar: {
        compareRule: "current_open",
        upColor: "#ef4444",
        downColor: "#16a34a",
        noChangeColor: text,
        upBorderColor: "#ef4444",
        downBorderColor: "#16a34a",
        noChangeBorderColor: text,
        upWickColor: "#ef4444",
        downWickColor: "#16a34a",
        noChangeWickColor: text,
      },
      area: {
        lineColor: "#4f7cff",
        backgroundColor: [
          { offset: 0, color: "rgba(79,124,255,0.30)" },
          { offset: 1, color: "rgba(79,124,255,0.02)" },
        ],
      },
      tooltip: {
        showRule: "follow_cross",
        showType: "standard",
        title: { size: chartTextSize, ...tooltipMargins },
        legend: { size: chartTextSize, ...tooltipMargins },
      },
      priceMark: {
        high: { textSize: chartTextSize },
        low: { textSize: chartTextSize },
        last: { text: { size: chartTextSize } },
      },
    },
    indicator: {
      ohlc: {
        compareRule: "current_open",
        upColor: "#ef4444",
        downColor: "#16a34a",
        noChangeColor: text,
      },
      tooltip: {
        showRule: "follow_cross",
        showType: "standard",
        title: { size: chartTextSize, ...tooltipMargins },
        legend: { size: chartTextSize, ...tooltipMargins },
      },
    },
    xAxis: {
      axisLine: { show: true, color: border, size: 1 },
      tickLine: { show: true, color: border, size: 1, length: 3 },
      tickText: { show: true, color: text, size: chartTextSize, family: "IBM Plex Mono" },
    },
    yAxis: {
      axisLine: { show: true, color: border, size: 1 },
      tickLine: { show: true, color: border, size: 1, length: 3 },
      tickText: { show: true, color: text, size: chartTextSize, family: "IBM Plex Mono" },
    },
    separator: {
      size: 1,
      color: border,
      fill: true,
      activeBackgroundColor: dark ? "#363e4b" : "#cdd4df",
    },
    crosshair: {
      horizontal: {
        line: { color: text, size: 1, style: "dashed", dashedValue: [4, 2] },
        text: { color: dark ? "#f8fafc" : "#ffffff", backgroundColor: dark ? "#465063" : "#58657a", size: chartTextSize },
      },
      vertical: {
        line: { color: text, size: 1, style: "dashed", dashedValue: [4, 2] },
        text: { color: dark ? "#f8fafc" : "#ffffff", backgroundColor: dark ? "#465063" : "#58657a", size: chartTextSize },
      },
    },
  }
}

function asMarketBar(data: KLineData | undefined): MarketBar | null {
  if (!data) return null
  const date = typeof data.date === "string"
    ? data.date
    : new Date(data.timestamp).toISOString().slice(0, 10)
  return {
    date,
    symbol: typeof data.symbol === "string" ? data.symbol : "",
    open: data.open,
    high: data.high,
    low: data.low,
    close: data.close,
    volume: Number(data.volume ?? 0),
    amount: Number(data.turnover ?? data.amount ?? 0),
  }
}

export const KLineTerminalChart = forwardRef<KLineTerminalChartHandle, KLineTerminalChartProps>(
  function KLineTerminalChart({ rows, symbol, chartType, indicators, compact = false, onCrosshairChange }, forwardedRef) {
    const { theme } = useTheme()
    const containerRef = useRef<HTMLDivElement>(null)
    const chartRef = useRef<Chart | null>(null)
    const crosshairCallbackRef = useRef(onCrosshairChange)
    const orderedData = useMemo<KLineData[]>(() => [...rows]
      .filter((row) => row.date && [row.open, row.high, row.low, row.close].every(Number.isFinite))
      .sort((left, right) => left.date.localeCompare(right.date))
      .map((row) => ({
        timestamp: timestampFor(row.date),
        open: row.open,
        high: row.high,
        low: row.low,
        close: row.close,
        volume: Number.isFinite(row.volume) ? row.volume : 0,
        turnover: Number.isFinite(row.amount) ? row.amount : 0,
        date: row.date,
        symbol: row.symbol || symbol,
      })), [rows, symbol])

    useEffect(() => {
      crosshairCallbackRef.current = onCrosshairChange
    }, [onCrosshairChange])

    useEffect(() => {
      const container = containerRef.current
      if (!container) return
      const chart = init(container, {
        locale: "zh-CN",
        timezone: "Asia/Shanghai",
        styles: chartStyles(theme, chartType, compact),
      })
      if (!chart) return
      chartRef.current = chart
      chart.setOffsetRightDistance(28)

      const handleCrosshair = (value?: unknown) => {
        const crosshair = value as Crosshair | undefined
        crosshairCallbackRef.current?.(asMarketBar(crosshair?.kLineData))
      }
      chart.subscribeAction("onCrosshairChange", handleCrosshair)

      const resizeObserver = new ResizeObserver(() => chart.resize())
      resizeObserver.observe(container)

      return () => {
        resizeObserver.disconnect()
        chart.unsubscribeAction("onCrosshairChange", handleCrosshair)
        chartRef.current = null
        dispose(chart)
      }
      // The chart instance is stable; theme and type are updated independently.
      // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [])

    useEffect(() => {
      chartRef.current?.setStyles(chartStyles(theme, chartType, compact))
    }, [chartType, compact, theme])

    useEffect(() => {
      const chart = chartRef.current
      if (!chart) return
      chart.setDataLoader({
        getBars: ({ type, callback }) => {
          callback(type === "init" ? orderedData : [], { backward: false, forward: false })
        },
      })
      chart.setSymbol({ ticker: symbol || "--", pricePrecision: 2, volumePrecision: 0 })
      chart.setPeriod({ type: "day", span: 1 })
      crosshairCallbackRef.current?.(null)
    }, [orderedData, symbol])

    useEffect(() => {
      const chart = chartRef.current
      if (!chart) return
      chart.removeIndicator()
      indicators.forEach((name) => {
        if (["MA", "EMA", "BOLL", "SAR"].includes(name)) {
          chart.createIndicator({ name, paneId: "candle_pane" }, true)
        } else {
          const paneId = chart.createIndicator(name, false)
          if (paneId) {
            const indicator = chart.getIndicators({ id: paneId })[0]
            if (indicator) chart.setPaneOptions({ id: indicator.paneId, minHeight: 72, height: name === "VOL" ? 92 : 112 })
          }
        }
      })
    }, [indicators])

    useImperativeHandle(forwardedRef, () => ({
      createOverlay(name) {
        chartRef.current?.createOverlay(name)
      },
      clearOverlays() {
        chartRef.current?.removeOverlay()
      },
      resetView() {
        const chart = chartRef.current
        if (!chart) return
        chart.setBarSpace(7)
        chart.scrollToRealTime()
      },
    }), [])

    return <div ref={containerRef} className="market-terminal-chart-canvas" />
  },
)
