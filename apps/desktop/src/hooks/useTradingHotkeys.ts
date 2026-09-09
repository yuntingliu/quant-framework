/**
 * Trading-mode keyboard shortcuts.
 *
 * Active only on the trading workspaces; never fires while typing in an
 * input/textarea/select/contentEditable or with modifier keys held (except
 * the dedicated Shift+X chord), so it cannot collide with Ctrl+K / Ctrl+1..4.
 *
 * Cross-panel actions are delivered as `alphalab:hotkey` CustomEvents so any
 * Dockview panel (ticket, blotter, cockpit) can opt in with a listener:
 *   { action: "ticket", side: "buy" | "sell" }   B / S
 *   { action: "cancel-last" }                    Shift+X
 *   { action: "watchlist-add" }                  W
 *   { action: "focus-symbol" }                   /
 */
import { useEffect } from "react"

import { useWorkspace } from "@/contexts/WorkspaceContext"

export interface TradingHotkeyDetail {
  action: "ticket" | "cancel-last" | "watchlist-add" | "focus-symbol"
  side?: "buy" | "sell"
}

export function dispatchTradingHotkey(detail: TradingHotkeyDetail): void {
  window.dispatchEvent(new CustomEvent<TradingHotkeyDetail>("alphalab:hotkey", { detail }))
}

function isEditableTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false
  const tag = target.tagName
  return (
    tag === "INPUT" ||
    tag === "TEXTAREA" ||
    tag === "SELECT" ||
    target.isContentEditable
  )
}

export function useTradingHotkeys(): void {
  const { activeMode, setSelectedOrderSide } = useWorkspace()

  useEffect(() => {
    if (!activeMode.startsWith("trading_")) return

    const handler = (event: KeyboardEvent) => {
      if (isEditableTarget(event.target)) return
      if (event.ctrlKey || event.metaKey || event.altKey) return

      const key = event.key
      if (event.shiftKey) {
        if (key.toUpperCase() === "X") {
          event.preventDefault()
          dispatchTradingHotkey({ action: "cancel-last" })
        }
        return
      }
      switch (key.toLowerCase()) {
        case "b":
          event.preventDefault()
          setSelectedOrderSide("buy")
          dispatchTradingHotkey({ action: "ticket", side: "buy" })
          break
        case "s":
          event.preventDefault()
          setSelectedOrderSide("sell")
          dispatchTradingHotkey({ action: "ticket", side: "sell" })
          break
        case "w":
          event.preventDefault()
          dispatchTradingHotkey({ action: "watchlist-add" })
          break
        case "/":
          event.preventDefault()
          dispatchTradingHotkey({ action: "focus-symbol" })
          break
      }
    }
    window.addEventListener("keydown", handler)
    return () => window.removeEventListener("keydown", handler)
  }, [activeMode, setSelectedOrderSide])
}
