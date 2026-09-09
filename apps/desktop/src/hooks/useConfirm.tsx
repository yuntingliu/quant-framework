import * as React from "react"
import { AlertTriangle } from "lucide-react"

import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { useLanguage } from "@/contexts/LanguageContext"
import { cn } from "@/lib/utils"

export interface ConfirmOptions {
  title: React.ReactNode
  description?: React.ReactNode
  /** Extra detail block (e.g. an order summary) rendered above the buttons. */
  body?: React.ReactNode
  confirmText?: string
  cancelText?: string
  /** "danger" renders a red confirm button + warning icon (use for live trades). */
  tone?: "default" | "danger"
  /** If set, the user must type this exact string to enable the confirm button. */
  requireText?: string
}

type ConfirmFn = (options: ConfirmOptions) => Promise<boolean>

const ConfirmContext = React.createContext<ConfirmFn | null>(null)

interface PendingState {
  options: ConfirmOptions
  resolve: (ok: boolean) => void
}

/**
 * App-level provider for promise-based confirmations. Replaces window.confirm
 * with a themed, branded dialog that can show order/risk detail and require a
 * typed confirmation phrase for live trading actions.
 */
export function ConfirmProvider({ children }: { children: React.ReactNode }) {
  const { language } = useLanguage()
  const [pending, setPending] = React.useState<PendingState | null>(null)
  const [typed, setTyped] = React.useState("")

  const confirm = React.useCallback<ConfirmFn>((options) => {
    setTyped("")
    return new Promise<boolean>((resolve) => setPending({ options, resolve }))
  }, [])

  const settle = React.useCallback(
    (ok: boolean) => {
      setPending((current) => {
        current?.resolve(ok)
        return null
      })
    },
    [],
  )

  const opts = pending?.options
  const needsText = Boolean(opts?.requireText)
  const canConfirm = !needsText || typed.trim() === opts?.requireText
  const isDanger = opts?.tone === "danger"
  const confirmLabel = opts?.confirmText ?? (language === "en" ? "Confirm" : "确认")
  const cancelLabel = opts?.cancelText ?? (language === "en" ? "Cancel" : "取消")

  return (
    <ConfirmContext.Provider value={confirm}>
      {children}
      <Dialog open={pending !== null} onOpenChange={(open) => { if (!open) settle(false) }}>
        {opts ? (
          <DialogContent className="max-w-sm" showClose={false}>
            <DialogHeader>
              <DialogTitle className="flex items-center gap-2">
                {isDanger ? <AlertTriangle className="h-4 w-4 text-destructive" /> : null}
                {opts.title}
              </DialogTitle>
              {opts.description ? <DialogDescription>{opts.description}</DialogDescription> : null}
            </DialogHeader>

            {opts.body ? <div className="text-xs text-muted-foreground">{opts.body}</div> : null}

            {needsText ? (
              <div className="space-y-1">
                <p className="text-[11px] text-muted-foreground">
                  {language === "en" ? "Type " : "请输入 "}
                  <span className="font-mono font-semibold text-foreground">{opts.requireText}</span>
                  {language === "en" ? " to confirm" : " 以确认"}
                </p>
                <Input
                  value={typed}
                  onChange={(event) => setTyped(event.target.value)}
                  className="font-mono"
                  autoFocus
                  onKeyDown={(event) => {
                    if (event.key === "Enter" && canConfirm) settle(true)
                  }}
                />
              </div>
            ) : null}

            <DialogFooter>
              <Button variant="outline" size="sm" onClick={() => settle(false)}>
                {cancelLabel}
              </Button>
              <Button
                size="sm"
                onClick={() => settle(true)}
                disabled={!canConfirm}
                className={cn(isDanger && "bg-red-600 text-white hover:bg-red-700")}
              >
                {confirmLabel}
              </Button>
            </DialogFooter>
          </DialogContent>
        ) : null}
      </Dialog>
    </ConfirmContext.Provider>
  )
}

export function useConfirm(): ConfirmFn {
  const ctx = React.useContext(ConfirmContext)
  if (!ctx) throw new Error("useConfirm must be used within a ConfirmProvider")
  return ctx
}
