import { useEffect, useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { BookOpen, Loader2 } from "lucide-react"

import { api } from "@/lib/api"
import { Button } from "@/components/ui/button"
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
  SheetTrigger,
} from "@/components/ui/sheet"
import { SafeMarkdown } from "@/components/shared/SafeMarkdown"

export type SdkDocumentTopic = "overview" | "factor" | "strategy" | "data" | "validation" | "report"

interface SdkDocumentPayload {
  sdk_version: number
  document: string
  topic: string
  title: string
  markdown: string
  topics: Array<{ id: string; title: string }>
}

interface SdkDocumentationProps {
  topic: SdkDocumentTopic
  label?: string
  className?: string
}

export function SdkDocumentation({ topic, label = "文档", className }: SdkDocumentationProps) {
  const [open, setOpen] = useState(false)
  const [selectedTopic, setSelectedTopic] = useState<SdkDocumentTopic>(topic)

  useEffect(() => setSelectedTopic(topic), [topic])

  const document = useQuery({
    queryKey: ["sdk-documentation", selectedTopic],
    queryFn: () => api.get<SdkDocumentPayload>(`/sdk-docs/${selectedTopic}`),
    enabled: open,
    staleTime: 5 * 60 * 1000,
  })

  return (
    <Sheet open={open} onOpenChange={setOpen}>
      <SheetTrigger asChild>
        <Button className={className} size="sm" variant="outline">
          <BookOpen />{label}
        </Button>
      </SheetTrigger>
      <SheetContent className="flex w-[min(94vw,54rem)] flex-col gap-0 p-0 sm:max-w-3xl">
        <SheetHeader className="shrink-0 border-b border-border px-6 py-5 pr-12">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <SheetTitle>{document.data?.title ?? "AlphaLab SDK 文档"}</SheetTitle>
              <SheetDescription className="mt-1">
                SDK v{document.data?.sdk_version ?? 1} · 与仓库文档和工作台保持同步
              </SheetDescription>
            </div>
            {document.data?.topics.length ? (
              <label className="grid gap-1 text-left text-[11px] text-muted-foreground">
                查看章节
                <select
                  className="h-8 min-w-40 rounded border border-input bg-background px-2 text-xs text-foreground"
                  value={selectedTopic}
                  onChange={(event) => setSelectedTopic(event.target.value as SdkDocumentTopic)}
                >
                  {document.data.topics.map((item) => <option key={item.id} value={item.id}>{item.title}</option>)}
                </select>
              </label>
            ) : null}
          </div>
        </SheetHeader>
        <div className="min-h-0 flex-1 overflow-auto px-6 py-5">
          {document.isLoading ? (
            <div className="flex items-center gap-2 text-sm text-muted-foreground"><Loader2 className="h-4 w-4 animate-spin" />正在读取 SDK 文档…</div>
          ) : document.error instanceof Error ? (
            <div className="rounded border border-destructive/40 bg-destructive/5 p-3 text-sm text-destructive">{document.error.message}</div>
          ) : document.data ? (
            <SafeMarkdown className="sdk-documentation-prose">{document.data.markdown}</SafeMarkdown>
          ) : null}
        </div>
      </SheetContent>
    </Sheet>
  )
}
