import * as React from "react"

import { cn } from "@/lib/utils"

export interface TextareaProps extends React.TextareaHTMLAttributes<HTMLTextAreaElement> {
  /** Render the error border/glow state. */
  error?: boolean
}

const Textarea = React.forwardRef<HTMLTextAreaElement, TextareaProps>(
  ({ className, error, ...props }, ref) => (
    <textarea
      ref={ref}
      className={cn("field-control min-h-16 px-2.5 py-1.5 text-xs", error && "field-error", className)}
      {...props}
    />
  ),
)
Textarea.displayName = "Textarea"

export { Textarea }
