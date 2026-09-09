import { Blocks, Cable, ShieldOff } from "lucide-react"
import { Widget } from "@/widgets/Widget"
import { Badge } from "@/components/ui/badge"

interface AdapterDisabledWidgetProps {
  title: string
  category?: string
  description?: string
}

export function AdapterDisabledWidget({
  title,
  category,
  description,
}: AdapterDisabledWidgetProps) {
  return (
    <Widget
      title={title}
      actions={<Badge variant="outline">adapter disabled</Badge>}
    >
      <div className="grid h-full min-h-[260px] content-center gap-4">
        <div className="mx-auto flex h-14 w-14 items-center justify-center rounded-lg border border-border bg-muted/40 text-muted-foreground">
          <Cable className="h-7 w-7" />
        </div>
        <div className="mx-auto max-w-xl text-center">
          <div className="text-sm font-semibold text-foreground">{title}</div>
          <p className="mt-2 text-sm leading-6 text-muted-foreground">
            {description || "This AlphaLab panel is preserved for layout and workflow continuity, but its concrete data or broker adapter is not bundled in the barebone framework."}
          </p>
        </div>
        <div className="mx-auto grid max-w-2xl gap-2 sm:grid-cols-3">
          <div className="rounded-md border border-border bg-background p-3 text-xs">
            <div className="mb-1 flex items-center gap-2 font-medium text-foreground"><Blocks className="h-3.5 w-3.5" /> GUI kept</div>
            <div className="text-muted-foreground">{category || "Original AlphaLab workspace"}</div>
          </div>
          <div className="rounded-md border border-border bg-background p-3 text-xs">
            <div className="mb-1 flex items-center gap-2 font-medium text-foreground"><ShieldOff className="h-3.5 w-3.5" /> No live route</div>
            <div className="text-muted-foreground">No vendor client, live broker, or real order path is wired.</div>
          </div>
          <div className="rounded-md border border-border bg-background p-3 text-xs">
            <div className="mb-1 flex items-center gap-2 font-medium text-foreground"><Cable className="h-3.5 w-3.5" /> Plugin slot</div>
            <div className="text-muted-foreground">Install a separate adapter package later to activate it.</div>
          </div>
        </div>
      </div>
    </Widget>
  )
}
