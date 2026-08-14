import { useMemo, useRef, useState } from "react"
import { useVirtualizer } from "@tanstack/react-virtual"
import { Search } from "lucide-react"

import { useLanguage } from "@/contexts/LanguageContext"

interface SymbolComboboxProps {
  symbols: string[]
  value: string
  onChange: (value: string) => void
  ariaLabel?: string
}

export function SymbolCombobox({
  symbols,
  value,
  onChange,
  ariaLabel,
}: SymbolComboboxProps) {
  const { language } = useLanguage()
  const copy = language === "zh"
    ? { symbol: "证券代码", search: "搜索证券代码", empty: "没有匹配的证券代码" }
    : { symbol: "Symbol", search: "Search symbol", empty: "No symbols" }
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState("")
  const scrollRef = useRef<HTMLDivElement>(null)
  const filtered = useMemo(() => {
    const needle = query.trim().toUpperCase()
    return needle
      ? symbols.filter((symbol) => symbol.includes(needle))
      : symbols
  }, [query, symbols])
  const virtualizer = useVirtualizer({
    count: filtered.length,
    getScrollElement: () => scrollRef.current,
    estimateSize: () => 30,
    overscan: 6,
  })

  function select(symbol: string) {
    onChange(symbol)
    setQuery("")
    setOpen(false)
  }

  return (
    <div className="symbol-combobox">
      <Search aria-hidden="true" />
      <input
        aria-label={ariaLabel ?? copy.symbol}
        aria-expanded={open}
        aria-controls="symbol-combobox-list"
        role="combobox"
        autoComplete="off"
        placeholder={value || copy.search}
        value={open ? query : value}
        onChange={(event) => {
          setQuery(event.target.value)
          setOpen(true)
        }}
        onFocus={() => setOpen(true)}
        onBlur={() => window.setTimeout(() => setOpen(false), 100)}
        onKeyDown={(event) => {
          if (event.key === "Enter" && filtered[0]) {
            event.preventDefault()
            select(filtered[0])
          }
          if (event.key === "Escape") setOpen(false)
        }}
      />
      {open && (
        <div
          ref={scrollRef}
          id="symbol-combobox-list"
          className="symbol-combobox-list"
          role="listbox"
        >
          {filtered.length ? (
            <div
              className="symbol-combobox-virtual"
              style={{ height: `${virtualizer.getTotalSize()}px` }}
            >
              {virtualizer.getVirtualItems().map((row) => {
                const symbol = filtered[row.index]
                return (
                  <button
                    key={symbol}
                    type="button"
                    role="option"
                    aria-selected={symbol === value}
                    className={symbol === value ? "selected" : ""}
                    style={{
                      height: `${row.size}px`,
                      transform: `translateY(${row.start}px)`,
                    }}
                    onMouseDown={(event) => event.preventDefault()}
                    onClick={() => select(symbol)}
                  >
                    {symbol}
                  </button>
                )
              })}
            </div>
          ) : (
            <div className="symbol-combobox-empty">{copy.empty}</div>
          )}
        </div>
      )}
    </div>
  )
}
