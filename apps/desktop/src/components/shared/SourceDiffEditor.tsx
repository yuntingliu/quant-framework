import { useEffect, useRef, useSyncExternalStore } from "react"
import { ArrowDown, ArrowUp } from "lucide-react"
import { useTheme } from "@/contexts/ThemeContext"
import {
  getPythonEditorRuntimeSnapshot, monaco, startPythonEditorRuntime, subscribePythonEditorRuntime,
} from "@/components/python/pythonEditorRuntime"

interface Props {
  before: string
  after: string
  labels: [string, string]
  wrap: boolean
  zh: boolean
  fallback: React.ReactNode
}

export function SourceDiffEditor({ before, after, labels, wrap, zh, fallback }: Props) {
  const container = useRef<HTMLDivElement>(null)
  const diff = useRef<monaco.editor.IStandaloneDiffEditor | null>(null)
  const runtime = useSyncExternalStore(subscribePythonEditorRuntime, getPythonEditorRuntimeSnapshot)
  const { theme } = useTheme()
  useEffect(() => { void startPythonEditorRuntime() }, [])

  useEffect(() => {
    if (!container.current || !runtime.initialized) return
    const editor = monaco.editor.createDiffEditor(container.current, {
      automaticLayout: true, readOnly: true, originalEditable: false, domReadOnly: true,
      renderSideBySide: true, renderSideBySideInlineBreakpoint: 680, useInlineViewWhenSpaceIsLimited: true,
      minimap: { enabled: false }, renderOverviewRuler: false, renderIndicators: true,
      ignoreTrimWhitespace: false, scrollBeyondLastLine: false, lineNumbersMinChars: 3,
      fontSize: 13, lineHeight: 21, folding: false, glyphMargin: false, renderValidationDecorations: "off",
      stickyScroll: { enabled: false }, contextmenu: false, padding: { top: 5, bottom: 10 },
    })
    const original = monaco.editor.createModel(before, "python")
    const modified = monaco.editor.createModel(after, "python")
    diff.current = editor
    editor.setModel({ original, modified })
    void editor.revealFirstDiff()
    return () => {
      diff.current = null
      editor.dispose()
      original.dispose()
      modified.dispose()
    }
  }, [before, after, runtime.initialized])

  useEffect(() => {
    diff.current?.updateOptions({ wordWrap: wrap ? "on" : "off", diffWordWrap: wrap ? "on" : "off" })
  }, [wrap, before, after, runtime.initialized])
  const [originalAriaLabel, modifiedAriaLabel] = labels
  useEffect(() => {
    diff.current?.getOriginalEditor().updateOptions({ ariaLabel: originalAriaLabel })
    diff.current?.getModifiedEditor().updateOptions({ ariaLabel: modifiedAriaLabel })
  }, [originalAriaLabel, modifiedAriaLabel, before, after, runtime.initialized])
  useEffect(() => {
    if (runtime.initialized) monaco.editor.setTheme(theme === "dark" ? "vs-dark" : "vs")
  }, [theme, runtime.initialized])

  if (runtime.error && !runtime.initialized) return <>{fallback}</>
  return <div className="sh-editor-document">
    <div className="sh-editor-labels"><span>{labels[0]}</span><span>{labels[1]}</span>
      <div className="sh-editor-actions">
        <button type="button" aria-label={zh ? "上一处改动" : "Previous change"} title={zh ? "上一处改动" : "Previous change"} onClick={() => diff.current?.goToDiff("previous")}><ArrowUp size={14} /></button>
        <button type="button" aria-label={zh ? "下一处改动" : "Next change"} title={zh ? "下一处改动" : "Next change"} onClick={() => diff.current?.goToDiff("next")}><ArrowDown size={14} /></button>
      </div>
    </div>
    <div className="sh-monaco" ref={container} />
    {!runtime.initialized && <p className="sh-editor-loading" role="status">{zh ? "正在打开编辑器…" : "Opening editor…"}</p>}
  </div>
}
