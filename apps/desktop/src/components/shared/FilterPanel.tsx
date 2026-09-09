import { useState, type ReactNode } from "react"
import { ChevronDown, ChevronUp, SlidersHorizontal } from "lucide-react"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"

interface FilterPanelProps {
  children: ReactNode
  collapsible?: boolean
  title?: string
  className?: string
}

export function FilterPanel({
  children,
  collapsible = false,
  title = "Filters",
  className,
}: FilterPanelProps) {
  const [isOpen, setIsOpen] = useState(true)

  return (
    <Card className={cn("overflow-hidden", className)}>
      <CardHeader className="py-3 px-5">
        <div className="flex items-center justify-between">
          <CardTitle className="flex items-center gap-2 text-sm font-medium">
            <SlidersHorizontal className="h-4 w-4 text-muted-foreground" />
            {title}
          </CardTitle>
          {collapsible && (
            <Button
              variant="ghost"
              size="icon"
              className="h-7 w-7"
              onClick={() => setIsOpen((prev) => !prev)}
            >
              {isOpen ? (
                <ChevronUp className="h-4 w-4" />
              ) : (
                <ChevronDown className="h-4 w-4" />
              )}
            </Button>
          )}
        </div>
      </CardHeader>
      {isOpen && (
        <CardContent className="px-5 pb-4 pt-0">
          <div className="flex flex-wrap items-end gap-4">{children}</div>
        </CardContent>
      )}
    </Card>
  )
}
