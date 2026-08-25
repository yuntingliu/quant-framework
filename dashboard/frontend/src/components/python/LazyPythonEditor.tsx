import { forwardRef, lazy, Suspense } from "react"

import type { PythonEditorHandle, PythonEditorProps } from "./PythonEditor"


const MonacoPythonEditor = lazy(async () => {
  const module = await import("./PythonEditor")
  return { default: module.PythonEditor }
})


export const PythonEditor = forwardRef<PythonEditorHandle, PythonEditorProps>(
  function LazyPythonEditor(props, ref) {
    return <Suspense fallback={<div className="python-editor-loading">正在加载 Python 编辑器…</div>}>
      <MonacoPythonEditor {...props} ref={ref} />
    </Suspense>
  },
)
