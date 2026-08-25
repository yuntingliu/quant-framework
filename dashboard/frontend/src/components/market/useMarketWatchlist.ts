import { useCallback, useEffect, useState } from "react"

const WATCHLIST_STORAGE_KEY = "alphalab.market-watchlist.v1"
const LEGACY_STORAGE_KEY = "alphalab.data-watchlist.v1"

function normalizeWatchlist(value: unknown): string[] {
  if (!Array.isArray(value)) return []
  return [...new Set(
    value
      .filter((item): item is string => typeof item === "string")
      .map((item) => item.trim().toUpperCase())
      .filter(Boolean),
  )]
}

function loadWatchlist(): string[] {
  try {
    const current = window.localStorage.getItem(WATCHLIST_STORAGE_KEY)
    const legacy = window.localStorage.getItem(LEGACY_STORAGE_KEY)
    return normalizeWatchlist(JSON.parse(current ?? legacy ?? "[]"))
  } catch {
    return []
  }
}

export function useMarketWatchlist() {
  const [watchlist, setWatchlist] = useState<string[]>(loadWatchlist)

  useEffect(() => {
    try {
      window.localStorage.setItem(WATCHLIST_STORAGE_KEY, JSON.stringify(watchlist))
    } catch {
      // Local persistence is optional; the terminal still works without it.
    }
  }, [watchlist])

  const toggleWatchlist = useCallback((symbol: string) => {
    const normalized = symbol.trim().toUpperCase()
    if (!normalized) return
    setWatchlist((current) => current.includes(normalized)
      ? current.filter((item) => item !== normalized)
      : [...current, normalized])
  }, [])

  return { watchlist, toggleWatchlist }
}
