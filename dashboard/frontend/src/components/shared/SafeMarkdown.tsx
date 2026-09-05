import type { ReactNode } from "react"
import ReactMarkdown from "react-markdown"
import rehypeRaw from "rehype-raw"
import rehypeSanitize, { defaultSchema } from "rehype-sanitize"
import remarkGfm from "remark-gfm"

import { cn } from "@/lib/utils"
import "./SafeMarkdown.css"

const safeDocumentSchema = {
  ...defaultSchema,
  tagNames: [...(defaultSchema.tagNames ?? []), "details", "summary"],
  attributes: {
    ...defaultSchema.attributes,
    "*": [...(defaultSchema.attributes?.["*"] ?? []), "className"],
    img: [...(defaultSchema.attributes?.img ?? []), "loading", "width", "height"],
  },
}

export function SafeMarkdown({ children, className }: { children: string; className?: string }) {
  return (
    <div className={cn("safe-markdown min-w-0 max-w-none break-words", className)}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        rehypePlugins={[rehypeRaw, [rehypeSanitize, safeDocumentSchema]]}
        components={{
          a: ({ children: linkChildren, ...props }) => <a {...props} target="_blank" rel="noreferrer">{linkChildren}</a>,
          table: ({ children: tableChildren, ...props }) => (
            <div className="safe-markdown-table max-w-full overflow-x-auto">
              <table {...props}>{tableChildren}</table>
            </div>
          ),
        }}
      >
        {children}
      </ReactMarkdown>
    </div>
  )
}

export function SafeMarkdownFrame({ children }: { children: ReactNode }) {
  return <div className="rounded border border-border bg-background p-3">{children}</div>
}
