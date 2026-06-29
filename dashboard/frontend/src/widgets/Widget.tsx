/**
 * Base widget wrapper — provides consistent header, loading, error states
 * for all dockview panels. Includes ErrorBoundary for crash isolation.
 */
import { EmptyState } from "@/components/shared/EmptyState"
import { ErrorBoundary } from "@/components/shared/ErrorBoundary"
import { useLanguage } from "@/contexts/LanguageContext"
import { cn } from "@/lib/utils"
import { AlertCircle, RefreshCw } from "lucide-react"

interface WidgetProps {
  title?: string
  children: React.ReactNode
  actions?: React.ReactNode
  loading?: boolean
  error?: string | null
  onRetry?: () => void
  className?: string
  bodyClassName?: string
  bodyPadding?: "none" | "compact" | "normal"
  /** If true, no header bar is shown (for KPI cards etc.) */
  headerless?: boolean
}

/** Skeleton placeholder for loading states — matches widget content area */
function WidgetSkeleton({ title }: { title?: string }) {
  return (
    <div className="widget-frame h-full min-h-0 min-w-0 flex flex-col overflow-hidden bg-card">
      {title && (
        <div className="flex min-w-0 items-center px-3 py-1.5 border-b border-border shrink-0">
          <div className="skeleton h-4 w-24" />
        </div>
      )}
      <div className="min-h-0 min-w-0 flex-1 space-y-3 overflow-hidden p-3">
        <div className="skeleton h-6 w-3/4" />
        <div className="skeleton h-4 w-full" />
        <div className="skeleton h-4 w-5/6" />
        <div className="skeleton h-32 w-full mt-2" />
      </div>
    </div>
  )
}

function WidgetInner({
  title,
  children,
  actions,
  loading,
  error,
  onRetry,
  className = "",
  bodyClassName,
  bodyPadding = "normal",
  headerless = false,
}: WidgetProps) {
  const { t } = useLanguage()
  const paddingClass =
    bodyPadding === "none" ? "p-0" : bodyPadding === "compact" ? "p-2" : "p-3"

  if (loading) {
    return <WidgetSkeleton title={headerless ? undefined : title} />
  }

  if (error) {
    return (
      <div className="widget-frame flex h-full min-h-0 min-w-0 items-center justify-center overflow-hidden bg-card p-4">
        <div className="flex max-w-xs min-w-0 flex-col items-center gap-2 text-center">
          <EmptyState
            title={t("common.loadFailed")}
            description={error}
            icon={AlertCircle}
          />
          {onRetry && (
            <button
              onClick={onRetry}
              className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-md bg-primary/10 text-primary hover:bg-primary/20 transition-colors mt-2"
            >
              <RefreshCw className="w-3 h-3" />
              {t("common.retry")}
            </button>
          )}
        </div>
      </div>
    )
  }

  if (headerless) {
    return (
      <div className={cn("widget-frame h-full min-h-0 min-w-0 overflow-auto bg-card", className)}>
        {children}
      </div>
    )
  }

  return (
    <div className={cn("widget-frame h-full min-h-0 min-w-0 flex flex-col overflow-hidden bg-card", className)}>
      {title && (
        <div className="flex min-w-0 shrink-0 flex-wrap items-center gap-x-2 gap-y-1 border-b border-border px-3 py-1.5">
          <span className="min-w-0 basis-32 flex-1 truncate text-sm font-medium text-foreground">{title}</span>
          {actions && <div className="flex max-w-full flex-none items-center gap-1 overflow-x-auto">{actions}</div>}
        </div>
      )}
      <div className={cn("min-h-0 min-w-0 flex-1 overflow-auto", paddingClass, bodyClassName)}>{children}</div>
    </div>
  )
}

export function Widget(props: WidgetProps) {
  const { t } = useLanguage()
  return (
    <ErrorBoundary fallbackTitle={props.title ? `${props.title} ${t("common.loadFailed")}` : undefined}>
      <WidgetInner {...props} />
    </ErrorBoundary>
  )
}
