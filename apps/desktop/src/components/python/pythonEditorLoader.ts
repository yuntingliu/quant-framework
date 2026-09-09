let modulePromise: Promise<typeof import("./PythonEditor")> | null = null

export function loadPythonEditor() {
  modulePromise ??= import("./PythonEditor").catch((error: unknown) => {
    modulePromise = null
    throw error
  })
  return modulePromise
}

export function preloadPythonEditor() {
  // A failed speculative load must not block normal workbench navigation.
  void loadPythonEditor().catch(() => undefined)
}
