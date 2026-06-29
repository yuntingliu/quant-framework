import * as React from "react"

import { cn } from "@/lib/utils"
import { Label } from "@/components/ui/label"

export interface FieldProps {
  label?: React.ReactNode
  htmlFor?: string
  /** Helper text shown below the control when there is no error. */
  hint?: React.ReactNode
  /** Error text shown below the control (takes precedence over hint). */
  error?: React.ReactNode
  required?: boolean
  className?: string
  children: React.ReactNode
}

/**
 * Label + control + hint/error wrapper. Pairs with Input / Textarea /
 * NativeSelect to give every form field a consistent vertical rhythm.
 */
export function Field({ label, htmlFor, hint, error, required, className, children }: FieldProps) {
  return (
    <div className={cn("space-y-1", className)}>
      {label ? (
        <Label htmlFor={htmlFor}>
          {label}
          {required ? <span className="ml-0.5 text-destructive">*</span> : null}
        </Label>
      ) : null}
      {children}
      {error ? (
        <p className="text-[10px] text-destructive">{error}</p>
      ) : hint ? (
        <p className="text-[10px] text-muted-foreground">{hint}</p>
      ) : null}
    </div>
  )
}
