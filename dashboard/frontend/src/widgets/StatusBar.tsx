/**
 * StatusBar — persistent bottom bar showing connection, account, market state.
 * Visible across all modes. The #1 "professional trading app" signal.
 */
import { useEffect, useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { useLanguage } from "@/contexts/LanguageContext"
import { api } from "@/lib/api"
import { cn } from "@/lib/utils"

interface TradingStatus {
  connected: boolean
  mode: string
  account_id: string | null
  broker?: "paper" | null
  broker_label?: string
  account_currency?: string | null
  readonly?: boolean
  supports_real_orders?: boolean
  supports_paper_orders?: boolean
  session_state?: string
}

interface AssetData {
  cash: number
  total_asset: number
}

export function StatusBar() {
  const { language, t } = useLanguage()
  const { data: status } = useQuery({
    queryKey: ["trading", "status"],
    queryFn: () => api.get<TradingStatus>("/trading/status"),
    refetchInterval: 5000,
  })

  const { data: asset } = useQuery({
    queryKey: ["trading", "asset"],
    queryFn: () => api.get<AssetData>("/trading/asset"),
    enabled: status?.connected === true,
    refetchInterval: 10000,
  })

  const [now, setNow] = useState(() => new Date())
  useEffect(() => {
    const id = setInterval(() => setNow(new Date()), 1000)
    return () => clearInterval(id)
  }, [])

  const connected = status?.connected ?? false
  const mode = status?.mode ?? "monitor"
  const broker = status?.broker
  const timeStr = now.toLocaleTimeString(language === "zh" ? "zh-CN" : "en-US", { hour: "2-digit", minute: "2-digit", second: "2-digit" })
  const modeLabels: Record<string, string> = {
    monitor: t("status.monitor"),
    preview: t("status.preview"),
    execute: t("status.execute"),
  }
  const connectionLabel = !connected
    ? (status?.session_state === "not_configured"
        ? (language === "zh" ? "历史数据 · 实时未配置" : "Historical data · realtime not configured")
        : (language === "zh" ? "交易未连接" : "Trading Disconnected"))
    : broker === "paper"
        ? (language === "zh" ? "AlphaLab纸面交易" : "AlphaLab Paper")
        : `${language === "zh" ? "纸面交易已连接" : "Paper connected"}${status?.broker_label ? ` · ${status.broker_label}` : ""}`

  return (
    <div className="alphalab-status-bar h-6 flex items-center gap-4 px-3 bg-card border-t border-border text-[10px] font-tabular shrink-0 select-none">
      {/* Connection */}
      <div className="flex items-center gap-1.5">
        <div className={cn(
          "w-1.5 h-1.5 rounded-full",
          connected ? "bg-green-500" : "bg-amber-500"
        )} />
        <span className="text-muted-foreground">
          {connectionLabel}
        </span>
      </div>

      {/* Mode */}
      {connected && (
        <span className={cn(
          mode === "execute" ? "text-red-400" : "text-muted-foreground"
        )}>
          {modeLabels[mode] ?? mode}
        </span>
      )}

      {/* Account value */}
      {connected && asset && (
        <span className="text-foreground">
          {t("status.asset")} {status?.account_currency || "¥"} {asset.total_asset.toLocaleString(undefined, { maximumFractionDigits: 0 })}
        </span>
      )}

      <div className="flex-1" />

      {/* Ctrl+K shortcut hint */}
      <button
        onClick={() => window.dispatchEvent(new KeyboardEvent("keydown", { key: "k", ctrlKey: true }))}
        className="flex items-center gap-1 text-muted-foreground hover:text-foreground transition-colors cursor-pointer"
      >
        <kbd className="px-1 py-0.5 rounded border border-border bg-muted/50 text-[9px] font-mono">Ctrl+K</kbd>
        <span>{t("status.nav")}</span>
      </button>

      {/* Market state */}
      <span className="text-muted-foreground">
        {language === "zh" ? "仅历史数据" : "Historical only"}
      </span>

      {/* Clock */}
      <span className="text-muted-foreground">{timeStr}</span>
    </div>
  )
}
