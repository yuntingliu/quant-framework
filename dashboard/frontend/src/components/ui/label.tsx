import * as React from "react"

import { cn } from "@/lib/utils"

/**
 * Lightweight form label. Matches the dashboard's existing field-label
 * convention (uppercase, 10px, tracked, muted) used across trading widgets.
 */
const Label = React.forwardRef<HTMLLabelElement, React.LabelHTMLAttributes<HTMLLabelElement>>(
  ({ className, ...props }, ref) => (
    <label
      ref={ref}
      className={cn(
        "block text-[10px] font-medium uppercase tracking-wider text-muted-foreground",
        className,
      )}
      {...props}
    />
  ),
)
Label.displayName = "Label"

export { Label }
