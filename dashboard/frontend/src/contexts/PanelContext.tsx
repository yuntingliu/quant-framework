/**
 * PanelContext — per-Dockview-panel state for symbol link groups.
 *
 * The link-group assignment lives in the panel's Dockview params (so it
 * persists in saved layouts and supports multiple instances of the same
 * widget); the current symbol per group is global WorkspaceContext state.
 * Panels without a group fall back to the legacy global selectedSymbol.
 */
import { createContext, useCallback, useContext, useState } from "react"
import { ChevronDown } from "lucide-react"

import {
  LINK_GROUP_COLORS,
  LINK_GROUPS,
  useWorkspace,
  type LinkGroup,
} from "@/contexts/WorkspaceContext"
import { cn } from "@/lib/utils"

interface PanelContextValue {
  panelId: string
  componentId: string
  linkGroup: LinkGroup | null
  setLinkGroup: (group: LinkGroup | null) => void
}

export const PanelContext = createContext<PanelContextValue | null>(null)

export function usePanel(): PanelContextValue | null {
  return useContext(PanelContext)
}

/**
 * Linked-symbol accessor: reads/writes the panel's link-group channel when a
 * group is assigned, otherwise the global selectedSymbol channel.
 *
 * Fallback rule: a grouped panel follows the GLOBAL symbol until its group
 * has explicitly published one — so unlinked widgets (positions rows,
 * watchlists) still drive grouped charts by default, and groups only isolate
 * once they carry their own symbol.
 */
export function useLinkedSymbol(): {
  symbol: string | null
  setSymbol: (symbol: string | null) => void
  group: LinkGroup | null
} {
  const panel = usePanel()
  const { selectedSymbol, linkSymbols, setLinkSymbol } = useWorkspace()
  const group = panel?.linkGroup ?? null
  const symbol = group ? linkSymbols[group] ?? selectedSymbol : selectedSymbol
  const setSymbol = useCallback(
    (value: string | null) => setLinkSymbol(group, value),
    [group, setLinkSymbol],
  )
  return { symbol, setSymbol, group }
}

/** Colored link-group selector dot, rendered in widget-local headers. */
export function LinkGroupBadge({ className }: { className?: string }) {
  const panel = usePanel()
  const [open, setOpen] = useState(false)
  if (!panel) return null
  const { linkGroup, setLinkGroup } = panel
  const color = linkGroup ? LINK_GROUP_COLORS[linkGroup] : undefined
  return (
    <div className={cn("relative", className)}>
      <button
        type="button"
        title={linkGroup ? `Link group ${linkGroup.toUpperCase()}` : "Not linked — click to join a symbol link group"}
        onClick={() => setOpen((value) => !value)}
        className="flex h-5 items-center gap-1 rounded border border-border/70 bg-background/60 px-1.5 text-[10px] text-muted-foreground hover:bg-muted"
      >
        <span
          className="inline-block h-2 w-2 rounded-full"
          style={{ background: color ?? "transparent", boxShadow: color ? "none" : "inset 0 0 0 1px currentColor" }}
        />
        {linkGroup ? linkGroup.toUpperCase() : "link"}
        <ChevronDown className="h-2.5 w-2.5" />
      </button>
      {open && (
        <div className="absolute right-0 top-6 z-50 flex flex-col gap-0.5 rounded border border-border bg-card p-1 shadow-lg">
          {LINK_GROUPS.map((group) => (
            <button
              key={group}
              type="button"
              onClick={() => { setLinkGroup(group); setOpen(false) }}
              className={cn(
                "flex items-center gap-1.5 rounded px-2 py-0.5 text-[11px] hover:bg-muted",
                linkGroup === group && "bg-muted",
              )}
            >
              <span className="inline-block h-2 w-2 rounded-full" style={{ background: LINK_GROUP_COLORS[group] }} />
              Group {group.toUpperCase()}
            </button>
          ))}
          <button
            type="button"
            onClick={() => { setLinkGroup(null); setOpen(false) }}
            className={cn(
              "flex items-center gap-1.5 rounded px-2 py-0.5 text-[11px] text-muted-foreground hover:bg-muted",
              linkGroup === null && "bg-muted",
            )}
          >
            <span className="inline-block h-2 w-2 rounded-full border border-current" />
            None
          </button>
        </div>
      )}
    </div>
  )
}
