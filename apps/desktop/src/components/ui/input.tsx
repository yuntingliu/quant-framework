import * as React from "react"

import { cn } from "@/lib/utils"

export interface InputProps extends React.InputHTMLAttributes<HTMLInputElement> {
  /** Icon rendered on the left inside the field (decorative). */
  leadingIcon?: React.ReactNode
  /** Action/element rendered on the right inside the field (e.g. password toggle). */
  trailingAction?: React.ReactNode
  /** Render the error border/glow state. */
  error?: boolean
  /** When icon slots are used, className targets the wrapper; this targets the <input>. */
  inputClassName?: string
}

// Compact default matches the dashboard's dense trading widgets.
const CONTROL = "field-control h-8 px-2.5 text-xs"

const Input = React.forwardRef<HTMLInputElement, InputProps>(
  ({ className, inputClassName, leadingIcon, trailingAction, error, type, ...props }, ref) => {
    if (leadingIcon || trailingAction) {
      return (
        <div className={cn("relative w-full", className)}>
          {leadingIcon ? (
            <span className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-muted-foreground [&_svg]:h-3.5 [&_svg]:w-3.5">
              {leadingIcon}
            </span>
          ) : null}
          <input
            ref={ref}
            type={type}
            className={cn(
              CONTROL,
              error && "field-error",
              leadingIcon && "pl-8",
              trailingAction && "pr-9",
              inputClassName,
            )}
            {...props}
          />
          {trailingAction ? (
            <span className="absolute right-1 top-1/2 flex -translate-y-1/2 items-center">
              {trailingAction}
            </span>
          ) : null}
        </div>
      )
    }
    return (
      <input
        ref={ref}
        type={type}
        className={cn(CONTROL, error && "field-error", className)}
        {...props}
      />
    )
  },
)
Input.displayName = "Input"

export { Input }
