import { useId, useMemo, useRef, useState } from "react"
import { useVirtualizer } from "@tanstack/react-virtual"
import { Search } from "lucide-react"

import { useLanguage } from "@/contexts/LanguageContext"

export interface SymbolOption {
  symbol: string
  name?: string | null
}

interface SymbolComboboxProps {
  symbols: Array<string | SymbolOption>
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
    ? { symbol: "证券代码或名称", search: "搜索代码或名称", empty: "没有匹配的证券" }
    : { symbol: "Symbol or name", search: "Search symbol or name", empty: "No matching symbols" }
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState("")
  const scrollRef = useRef<HTMLDivElement>(null)
  const listId = useId()
  const options = useMemo(() => {
    const unique = new Map<string, SymbolOption>()
    for (const item of symbols) {
      const option = typeof item === "string" ? { symbol: item } : item
      const symbol = option.symbol.trim().toUpperCase()
      if (!symbol) continue
      unique.set(symbol, { symbol, name: option.name?.trim() || null })
    }
    return [...unique.values()]
  }, [symbols])
  const filtered = useMemo(() => {
    const needle = query.trim().toUpperCase()
    return needle
      ? options.filter((option) => (
          option.symbol.includes(needle)
          || option.name?.toUpperCase().includes(needle)
        ))
      : options
  }, [options, query])
  const selected = options.find((option) => option.symbol === value)
  const selectedLabel = selected?.name ? `${selected.symbol}  ${selected.name}` : value
  const virtualizer = useVirtualizer({
    count: filtered.length,
    getScrollElement: () => scrollRef.current,
    estimateSize: () => 40,
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
        aria-controls={listId}
        role="combobox"
        autoComplete="off"
        placeholder={selectedLabel || copy.search}
        value={open ? query : selectedLabel}
        onChange={(event) => {
          setQuery(event.target.value)
          setOpen(true)
        }}
        onFocus={() => setOpen(true)}
        onBlur={() => window.setTimeout(() => setOpen(false), 100)}
        onKeyDown={(event) => {
          if (event.key === "Enter" && filtered[0]) {
            event.preventDefault()
            select(filtered[0].symbol)
          }
          if (event.key === "Escape") setOpen(false)
        }}
      />
      {open && (
        <div
          ref={scrollRef}
          id={listId}
          className="symbol-combobox-list"
          role="listbox"
        >
          {filtered.length ? (
            <div
              className="symbol-combobox-virtual"
              style={{ height: `${virtualizer.getTotalSize()}px` }}
            >
              {virtualizer.getVirtualItems().map((row) => {
                const option = filtered[row.index]
                return (
                  <button
                    key={option.symbol}
                    type="button"
                    role="option"
                    aria-selected={option.symbol === value}
                    className={option.symbol === value ? "selected" : ""}
                    style={{
                      height: `${row.size}px`,
                      transform: `translateY(${row.start}px)`,
                    }}
                    onMouseDown={(event) => event.preventDefault()}
                    onClick={() => select(option.symbol)}
                  >
                    <strong>{option.symbol}</strong>
                    {option.name ? <span>{option.name}</span> : null}
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
