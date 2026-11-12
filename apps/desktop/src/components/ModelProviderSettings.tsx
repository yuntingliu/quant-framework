import { useCallback, useState } from "react"
import { Settings2 } from "lucide-react"
import { SearchProviderSettings } from "@/components/SearchProviderSettings"
import { ProfileSettings } from "@/components/configuration/ProfileSettings"
import { ConfigurationTransfer } from "@/components/configuration/ConfigurationTransfer"
import { useConfigurationOperation, useConfigurationText } from "@/components/configuration/ConfigurationControls"
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle, DialogTrigger } from "@/components/ui/dialog"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"

export function ModelProviderSettings({ collapsed = false }: { collapsed?: boolean }) {
  const tr = useConfigurationText()
  const [open, setOpen] = useState(false)
  const [generation, setGeneration] = useState(0)
  const refresh = useCallback(() => {
    setGeneration(value => value + 1)
    window.dispatchEvent(new Event("alphalab:model-provider-changed"))
  }, [])
  const transfer = useConfigurationOperation(refresh)
  return <Dialog open={open} onOpenChange={setOpen}>
    <DialogTrigger asChild><button className="flex w-full items-center gap-2 rounded-md px-3 py-2 text-sm text-muted-foreground hover:bg-accent hover:text-foreground" title={tr("Personal configuration", "个人配置")} aria-label={tr("Personal configuration", "个人配置")}><Settings2 className="h-4 w-4 shrink-0" />{!collapsed && tr("Personal configuration", "个人配置")}</button></DialogTrigger>
    {open && <DialogContent className="max-h-[90vh] max-w-3xl overflow-y-auto">
      <DialogHeader><DialogTitle>{tr("Personal configuration", "个人配置")}</DialogTitle><DialogDescription>{tr("New Agent runs and jobs use newly selected settings. Already admitted operations retain the configuration captured at their first use or admission.", "新的 Agent 运行和任务使用新选择的设置。已接纳操作保留在首次使用或接纳时捕获的配置。")}</DialogDescription></DialogHeader>
      <Tabs defaultValue="models" className="min-w-0">
        <TabsList className="flex h-auto flex-wrap justify-start" aria-label={tr("Configuration sections", "配置分区")}>
          <TabsTrigger value="models">{tr("Models", "模型")}</TabsTrigger><TabsTrigger value="search">{tr("Web Search", "网页搜索")}</TabsTrigger><TabsTrigger value="rqdata">{tr("Data Sources", "数据源")}</TabsTrigger><TabsTrigger value="transfer">{tr("Import / Export", "导入 / 导出")}</TabsTrigger>
        </TabsList>
        <TabsContent value="models"><ProfileSettings key={generation} service="models" onSaved={() => undefined} /></TabsContent>
        <TabsContent value="search"><SearchProviderSettings key={generation} onSaved={() => undefined} /></TabsContent>
        <TabsContent value="rqdata"><ProfileSettings key={generation} service="rqdata" onSaved={() => undefined} /></TabsContent>
        <TabsContent value="transfer"><ConfigurationTransfer onApplied={refresh} coordinator={transfer} /></TabsContent>
      </Tabs>
    </DialogContent>}
  </Dialog>
}
