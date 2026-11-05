import assert from "node:assert/strict"
import test from "node:test"

import { MODE_SHORTCUTS, WORKSPACE_MODES } from "../src/workspace/modes.ts"
import { widgetCatalog } from "../src/widgets/registry/catalog.ts"

const activeIds = new Set(widgetCatalog.filter((w) => w.status === "active").map((w) => w.id))

test("every mode shortcut points at a widget that exists", () => {
  for (const mode of WORKSPACE_MODES) {
    for (const widgetId of MODE_SHORTCUTS[mode]) {
      assert.ok(
        widgetCatalog.some((w) => w.id === widgetId),
        `${mode} lists unknown widget ${widgetId}`,
      )
    }
  }
})

test("nothing offered by a mode is hidden from the catalogue", () => {
  for (const mode of WORKSPACE_MODES) {
    for (const widgetId of MODE_SHORTCUTS[mode]) {
      assert.ok(activeIds.has(widgetId), `${widgetId} is not an active widget`)
    }
  }
})

test("the paper-trading risk console is reachable from a workspace mode", () => {
  // The console used to exist as an active widget with no mode listing it, so the
  // toolbar's add-widget menu could never offer it.
  const reachable = WORKSPACE_MODES.filter((mode) =>
    MODE_SHORTCUTS[mode].includes("trading.alerts"),
  )
  assert.deepEqual(reachable, ["validation"])
  assert.ok(activeIds.has("trading.alerts"))
})
