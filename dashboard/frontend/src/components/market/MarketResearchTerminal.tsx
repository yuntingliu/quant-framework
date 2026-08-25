import { useMemo, useRef, useState, type ReactNode } from "react"
import { useVirtualizer } from "@tanstack/react-virtual"
import {
  AreaChart,
  CandlestickChart,
  Eraser,
  LineChart,
  Minus,
  RefreshCw,
  Search,
  Star,
} from "lucide-react"

import type { MarketBar, MarketInstrument } from "@/lib/api"

import {
  KLineTerminalChart,
  type KLineTerminalChartHandle,
  type MarketChartType,
} from "./KLineTerminalChart"

export type MarketRange = "3m" | "6m" | "1y" | "all"

interface MarketResearchTerminalProps {
  instruments: MarketInstrument[]
  rows: MarketBar[]
  symbol: string
  onSymbolChange: (symbol: string) => void
  range: MarketRange
  onRangeChange: (range: MarketRange) => void
  loading?: boolean
  error?: string
  onReload?: () => void
  watchlist: string[]
  onToggleWatchlist: (symbol: string) => void
  contextPanel?: ReactNode
  contextPanelLabel?: string
  dataLabel?: string
  emptyLabel?: string
}

const MAIN_INDICATORS = ["MA", "EMA", "BOLL", "SAR"]
const SUB_INDICATORS = ["VOL", "MACD", "RSI", "KDJ"]

function formatNumber(value: number | undefined, precision = 2): string {
  return value !== undefined && Number.isFinite(value)
    ? value.toLocaleString("zh-CN", { minimumFractionDigits: precision, maximumFractionDigits: precision })
    : "—"
}

function formatCompact(value: number | undefined): string {
  return value !== undefined && Number.isFinite(value)
    ? new Intl.NumberFormat("zh-CN", { notation: "compact", maximumFractionDigits: 2 }).format(value)
    : "—"
}

export function MarketResearchTerminal({
  instruments,
  rows,
  symbol,
  onSymbolChange,
  range,
  onRangeChange,
  loading = false,
  error = "",
  onReload,
  watchlist,
  onToggleWatchlist,
  contextPanel,
  contextPanelLabel = "研究字段",
  dataLabel = "日线 · 前复权",
  emptyLabel = "当前证券没有可用行情。",
}: MarketResearchTerminalProps) {
  const [universeView, setUniverseView] = useState<"all" | "watchlist">("all")
  const [search, setSearch] = useState("")
  const [chartType, setChartType] = useState<MarketChartType>("candle_solid")
  const [indicators, setIndicators] = useState<string[]>(["MA", "VOL"])
  const [crosshairBar, setCrosshairBar] = useState<MarketBar | null>(null)
  const listRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<KLineTerminalChartHandle>(null)

  const instrumentBySymbol = useMemo(
    () => new Map(instruments.map((instrument) => [instrument.symbol, instrument])),
    [instruments],
  )
  const filteredInstruments = useMemo(() => {
    const normalized = search.trim().toLowerCase()
    const source = universeView === "watchlist"
      ? watchlist.map((item) => instrumentBySymbol.get(item)).filter((item): item is MarketInstrument => Boolean(item))
      : instruments
    if (!normalized) return source
    return source.filter((item) => (
      item.symbol.toLowerCase().includes(normalized)
      || (item.name ?? "").toLowerCase().includes(normalized)
    ))
  }, [instrumentBySymbol, instruments, search, universeView, watchlist])

  const rowVirtualizer = useVirtualizer({
    count: filteredInstruments.length,
    getScrollElement: () => listRef.current,
    estimateSize: () => 42,
    overscan: 8,
  })

  const selectedInstrument = instrumentBySymbol.get(symbol)
  const displayedBar = crosshairBar ?? rows.at(-1)
  const displayedIndex = displayedBar
    ? rows.findIndex((row) => row.date === displayedBar.date)
    : -1
  const previousBar = displayedIndex > 0 ? rows[displayedIndex - 1] : undefined
  const change = displayedBar && previousBar?.close
    ? displayedBar.close / previousBar.close - 1
    : null
  const positive = change !== null && change >= 0

  function toggleIndicator(name: string) {
    setIndicators((current) => {
      if (current.includes(name)) return current.filter((item) => item !== name)
      if (SUB_INDICATORS.includes(name)) {
        return [...current.filter((item) => !SUB_INDICATORS.includes(item)), name]
      }
      return [...current, name]
    })
  }

  return (
    <section className={`market-terminal ${contextPanel ? "with-context" : ""}`} aria-label="行情研究终端">
      <aside className="market-terminal-universe">
        <div className="market-terminal-universe-tabs" role="tablist" aria-label="证券列表">
          <button type="button" role="tab" aria-selected={universeView === "all"} onClick={() => setUniverseView("all")}>全部 <span>{instruments.length}</span></button>
          <button type="button" role="tab" aria-selected={universeView === "watchlist"} onClick={() => setUniverseView("watchlist")}>自选 <span>{watchlist.length}</span></button>
        </div>
        <label className="market-terminal-search">
          <Search size={13} />
          <input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="代码 / 名称" aria-label="搜索证券" />
        </label>
        <div className="market-terminal-list-heading"><span>证券</span><span>状态</span></div>
        <div className="market-terminal-list" ref={listRef}>
          {filteredInstruments.length ? (
            <div style={{ height: rowVirtualizer.getTotalSize(), position: "relative" }}>
              {rowVirtualizer.getVirtualItems().map((virtualRow) => {
                const instrument = filteredInstruments[virtualRow.index]
                const active = instrument.symbol === symbol
                const watched = watchlist.includes(instrument.symbol)
                return (
                  <div
                    className={`market-terminal-symbol ${active ? "active" : ""}`}
                    key={instrument.symbol}
                    style={{ position: "absolute", top: 0, left: 0, width: "100%", height: virtualRow.size, transform: `translateY(${virtualRow.start}px)` }}
                  >
                    <button type="button" className="market-terminal-symbol-main" onClick={() => onSymbolChange(instrument.symbol)} title={`${instrument.symbol} ${instrument.name ?? ""}`}>
                      <strong>{instrument.name || instrument.symbol}</strong>
                      <small>{instrument.symbol}</small>
                    </button>
                    <button type="button" className={`market-terminal-star ${watched ? "active" : ""}`} onClick={() => onToggleWatchlist(instrument.symbol)} aria-label={`${watched ? "移出" : "加入"}自选 ${instrument.symbol}`}>
                      <Star size={12} fill={watched ? "currentColor" : "none"} />
                    </button>
                  </div>
                )
              })}
            </div>
          ) : <div className="market-terminal-list-empty">{universeView === "watchlist" ? "暂无自选证券" : "没有匹配的证券"}</div>}
        </div>
      </aside>

      <div className="market-terminal-main">
        <header className="market-terminal-quote">
          <div className="market-terminal-identity">
            <span><strong>{selectedInstrument?.name || symbol || "选择证券"}</strong><small>{symbol || "—"}</small></span>
            <button type="button" className={watchlist.includes(symbol) ? "active" : ""} onClick={() => onToggleWatchlist(symbol)} disabled={!symbol} title="切换自选">
              <Star size={14} fill={watchlist.includes(symbol) ? "currentColor" : "none"} />
            </button>
          </div>
          <div className={`market-terminal-last ${change === null ? "neutral" : positive ? "rise" : "fall"}`}>
            <strong>{formatNumber(displayedBar?.close)}</strong>
            <span>{change === null ? "—" : `${positive ? "+" : ""}${formatNumber(displayedBar!.close - previousBar!.close)}  ${positive ? "+" : ""}${(change * 100).toFixed(2)}%`}</span>
          </div>
          <dl className="market-terminal-ohlcv">
            <div><dt>开</dt><dd>{formatNumber(displayedBar?.open)}</dd></div>
            <div><dt>高</dt><dd className="rise">{formatNumber(displayedBar?.high)}</dd></div>
            <div><dt>低</dt><dd className="fall">{formatNumber(displayedBar?.low)}</dd></div>
            <div><dt>收</dt><dd>{formatNumber(displayedBar?.close)}</dd></div>
            <div><dt>量</dt><dd>{formatCompact(displayedBar?.volume)}</dd></div>
            <div><dt>额</dt><dd>{formatCompact(displayedBar?.amount)}</dd></div>
          </dl>
          <span className="market-terminal-date">{displayedBar?.date ?? dataLabel}</span>
        </header>

        <div className="market-terminal-toolbar">
          <div className="market-terminal-tool-group" aria-label="图表类型">
            <button type="button" className={chartType === "candle_solid" ? "active" : ""} onClick={() => setChartType("candle_solid")} title="K 线"><CandlestickChart size={13} />K线</button>
            <button type="button" className={chartType === "area" ? "active" : ""} onClick={() => setChartType("area")} title="面积图"><AreaChart size={13} />走势</button>
          </div>
          <div className="market-terminal-tool-group market-terminal-ranges" aria-label="行情区间">
            {(["3m", "6m", "1y", "all"] as MarketRange[]).map((item) => (
              <button type="button" className={range === item ? "active" : ""} key={item} onClick={() => onRangeChange(item)}>{item === "all" ? "全部" : item.toUpperCase()}</button>
            ))}
          </div>
          <div className="market-terminal-tool-divider" />
          <div className="market-terminal-indicators" aria-label="技术指标">
            <span>主图</span>
            {MAIN_INDICATORS.map((name) => <button type="button" className={indicators.includes(name) ? "active" : ""} key={name} onClick={() => toggleIndicator(name)}>{name}</button>)}
            <span>副图</span>
            {SUB_INDICATORS.map((name) => <button type="button" className={indicators.includes(name) ? "active" : ""} key={name} onClick={() => toggleIndicator(name)}>{name}</button>)}
          </div>
          <div className="market-terminal-tool-spacer" />
          <div className="market-terminal-tool-group" aria-label="画图工具">
            <button type="button" onClick={() => chartRef.current?.createOverlay("segment")} title="趋势线"><LineChart size={13} /></button>
            <button type="button" onClick={() => chartRef.current?.createOverlay("horizontalStraightLine")} title="水平线"><Minus size={13} /></button>
            <button type="button" onClick={() => chartRef.current?.clearOverlays()} title="清除画线"><Eraser size={13} /></button>
            {onReload ? <button type="button" onClick={onReload} disabled={loading} title="刷新行情"><RefreshCw className={loading ? "spin" : ""} size={13} /></button> : null}
          </div>
        </div>

        <div className="market-terminal-chart">
          {rows.length ? (
            <KLineTerminalChart ref={chartRef} rows={rows} symbol={symbol} chartType={chartType} indicators={indicators} onCrosshairChange={setCrosshairBar} />
          ) : (
            <div className={`market-terminal-empty ${error ? "error" : ""}`}>
              {error || (loading ? "正在加载行情…" : emptyLabel)}
            </div>
          )}
          {loading && rows.length ? <div className="market-terminal-loading"><RefreshCw className="spin" size={13} />更新中</div> : null}
        </div>
      </div>

      {contextPanel ? (
        <aside className="market-terminal-context" aria-label={contextPanelLabel}>
          {contextPanel}
        </aside>
      ) : null}
    </section>
  )
}
