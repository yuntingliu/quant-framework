import { useCallback, type PointerEvent as ReactPointerEvent } from "react"
import { Bot, PanelRightClose, PanelRightOpen, X } from "lucide-react"

import { useLanguage } from "@/contexts/LanguageContext"
import { ResearchAgentPanel } from "@/widgets/research/ResearchAgent"

const RIGHT_RAIL_MIN_WIDTH = 320
const RIGHT_RAIL_MAX_WIDTH = 720

export function WorkspaceRightRail({
  collapsed,
  narrow,
  width,
  onToggleCollapsed,
  onWidthChange,
}: {
  collapsed: boolean
  narrow: boolean
  width: number
  onToggleCollapsed: () => void
  onWidthChange: (width: number) => void
}) {
  const { language, t } = useLanguage()
  const resizeLabel = language === "zh" ? "拖动调整右栏宽度" : "Drag to resize the right rail"

  const startResize = useCallback((event: ReactPointerEvent<HTMLDivElement>) => {
    event.preventDefault()
    const startX = event.clientX
    const startWidth = event.currentTarget.parentElement?.getBoundingClientRect().width ?? width
    const previousCursor = document.body.style.cursor
    const previousUserSelect = document.body.style.userSelect
    document.body.style.cursor = "col-resize"
    document.body.style.userSelect = "none"

    const handleMove = (moveEvent: PointerEvent) => {
      onWidthChange(startWidth + startX - moveEvent.clientX)
    }
    const finish = () => {
      window.removeEventListener("pointermove", handleMove)
      window.removeEventListener("pointerup", finish)
      window.removeEventListener("pointercancel", finish)
      document.body.style.cursor = previousCursor
      document.body.style.userSelect = previousUserSelect
    }

    window.addEventListener("pointermove", handleMove)
    window.addEventListener("pointerup", finish)
    window.addEventListener("pointercancel", finish)
  }, [onWidthChange, width])

  const panel = (
    <div className="flex h-full min-h-0 flex-col bg-card">
      <div className="flex shrink-0 items-center gap-2 border-b border-border px-3 py-2">
        <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded border border-primary/25 bg-primary/10 text-primary">
          <Bot className="h-4 w-4" />
        </div>
        <div className="min-w-0 flex-1 truncate text-sm font-semibold text-foreground">{t("rightRail.agent")}</div>
        <button
          className="flex h-8 w-8 shrink-0 items-center justify-center rounded text-muted-foreground transition-colors hover:bg-muted/50 hover:text-foreground"
          onClick={onToggleCollapsed}
          title={narrow ? t("rightRail.close") : t("rightRail.collapse")}
        >
          {narrow ? <X className="h-4 w-4" /> : <PanelRightClose className="h-4 w-4" />}
        </button>
      </div>
      <div className="min-h-0 flex-1 overflow-hidden">
        <ResearchAgentPanel />
      </div>
    </div>
  )

  if (narrow) {
    if (collapsed) return null
    return (
      <>
        <button
          aria-label={t("rightRail.close")}
          className="fixed inset-0 z-40 bg-background/70 backdrop-blur-sm md:hidden"
          onClick={onToggleCollapsed}
        />
        <aside
          className="fixed inset-y-0 right-0 z-50 max-w-[calc(100vw-24px)] border-l border-border shadow-xl md:hidden"
          style={{ width }}
        >
          {panel}
        </aside>
      </>
    )
  }

  if (collapsed) {
    return (
      <aside className="hidden h-full min-h-0 w-14 shrink-0 flex-col items-center border-l border-border bg-card p-2 md:flex">
        <button
          className="flex h-8 w-8 shrink-0 items-center justify-center rounded text-muted-foreground transition-colors hover:bg-muted/50 hover:text-foreground"
          onClick={onToggleCollapsed}
          title={t("rightRail.expand")}
        >
          <PanelRightOpen className="h-4 w-4" />
        </button>
      </aside>
    )
  }

  return (
    <aside
      className="relative hidden h-full min-h-0 shrink-0 border-l border-border md:block"
      style={{ width, maxWidth: "58vw" }}
    >
      <div
        role="separator"
        aria-label={resizeLabel}
        aria-orientation="vertical"
        aria-valuemin={RIGHT_RAIL_MIN_WIDTH}
        aria-valuemax={RIGHT_RAIL_MAX_WIDTH}
        aria-valuenow={Math.round(width)}
        tabIndex={0}
        className="group absolute inset-y-0 left-0 z-20 hidden w-2 -translate-x-1/2 cursor-col-resize touch-none outline-none md:block"
        onPointerDown={startResize}
        onKeyDown={(event) => {
          if (event.key === "ArrowLeft") {
            event.preventDefault()
            onWidthChange(width + 16)
          } else if (event.key === "ArrowRight") {
            event.preventDefault()
            onWidthChange(width - 16)
          }
        }}
        title={resizeLabel}
      >
        <span className="absolute inset-y-0 left-1/2 w-px -translate-x-1/2 bg-transparent transition-colors group-hover:bg-primary/60 group-focus:bg-primary" />
        <span className="absolute left-1/2 top-1/2 h-10 w-1 -translate-x-1/2 -translate-y-1/2 rounded-full bg-border transition-colors group-hover:bg-primary group-focus:bg-primary" />
      </div>
      {panel}
    </aside>
  )
}
