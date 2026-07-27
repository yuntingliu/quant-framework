/**
 * Widget registry public barrel.
 */
export { widgetComponents } from "./registry/components"
export type { WidgetMeta, WidgetStatus } from "./registry/catalog"
export {
  agentWorkspaceWidgetIds,
  isActiveWidgetId,
  widgetCatalog,
  widgetTitle,
  widgetDescription,
  widgetCategory,
  widgetTitleById,
} from "./registry/catalog"
