import * as React from "react"
import { ChevronDown } from "lucide-react"

import { cn } from "@/lib/utils"

export interface NativeSelectProps extends React.SelectHTMLAttributes<HTMLSelectElement> {
  /** Render the error border/glow state. */
  error?: boolean
  /** className targets the wrapper; this targets the <select> itself. */
  selectClassName?: string
}

/**
 * Native <select> styled to match our form primitives. Use this for simple
 * option lists in dense widgets; for rich/portal'd dropdowns use the Radix
 * `Select` in ./select.tsx.
 */
const NativeSelect = React.forwardRef<HTMLSelectElement, NativeSelectProps>(
  ({ className, selectClassName, error, children, ...props }, ref) => (
    <div className={cn("relative w-full", className)}>
      <select
        ref={ref}
        className={cn(
          "field-control h-8 appearance-none px-2.5 pr-7 text-xs",
          error && "field-error",
          selectClassName,
        )}
        {...props}
      >
        {children}
      </select>
      <ChevronDown className="pointer-events-none absolute right-2 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
    </div>
  ),
)
NativeSelect.displayName = "NativeSelect"

export { NativeSelect }
