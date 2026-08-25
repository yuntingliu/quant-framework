import {
  forwardRef,
  useEffect,
  useImperativeHandle,
  useMemo,
  useRef,
  useState,
  useSyncExternalStore,
  type CSSProperties,
} from "react"
import { AlertCircle, CheckCircle2, Code2, GitCompare, Sparkles, Wand2 } from "lucide-react"

import { useTheme } from "@/contexts/ThemeContext"
import { api } from "@/lib/api"
import { cn } from "@/lib/utils"
import {
  getPythonEditorRuntimeSnapshot,
  monaco,
  preparePythonDocument,
  startPythonEditorRuntime,
  subscribePythonEditorRuntime,
  type EditorServerStatus,
} from "./pythonEditorRuntime"

type MonacoEditor = monaco.editor.IStandaloneCodeEditor
type MonacoModel = monaco.editor.ITextModel

export interface PythonSdkField {
  name: string
  dataset?: "market_bars" | "fundamentals" | string
  detail?: string
}

export interface PythonSdkFactor {
  id: string
  label?: string | null
}

export interface PythonSdkParameter {
  name: string
  annotation?: string | null
  description?: string | null
  minimum?: number | null
  maximum?: number | null
  step?: number | null
}

export interface PythonEditorHandle {
  focus: () => void
  insertText: (text: string) => void
  revealLine: (line: number) => void
  getValue: () => string
}

interface Diagnostic {
  severity: "error" | "warning" | "info"
  line: number
  column: number
  code: string
  message: string
  source: string
}

interface DiagnosticsPayload {
  valid: boolean
  diagnostics: Diagnostic[]
}

export interface PythonEditorProps {
  kind: "strategy" | "data"
  documentId: string
  value: string
  version?: string
  baselineValue?: string
  disabled?: boolean
  height?: number | string
  className?: string
  revealLine?: number
  fields?: PythonSdkField[]
  factors?: PythonSdkFactor[]
  parameters?: PythonSdkParameter[]
  onChange: (value: string) => void
  onSave?: (value: string) => void | Promise<void>
}

interface ModelMetadata {
  externalValue: string
  externalVersion?: string
}

interface SdkCatalog {
  kind: "strategy" | "data"
  fields: PythonSdkField[]
  factors: PythonSdkFactor[]
  parameters: PythonSdkParameter[]
  rqOperations: Array<{ name: string; signature: string; documentation: string }>
}

const modelMetadata = new Map<string, ModelMetadata>()
const sdkCatalogs = new Map<string, SdkCatalog>()
let sdkProvidersInstalled = false

const CONTEXT_METHODS = [
  ["history", "history(fields, *, window, symbols=None)", "读取截至当前时点的历史行情窗口"],
  ["current", "current(field, *, symbols=None)", "读取当前截面行情字段"],
  ["fundamental", "fundamental(field, *, symbols=None)", "读取点时可用的基本面字段"],
  ["factor", "factor(factor_id, **parameters)", "调用项目内已注册因子"],
  ["combine_factors", "combine_factors(weights, *, normalization='rank', parameters=None)", "标准化并合成多个因子"],
  ["instruments", "instruments(*, asset_type=None)", "读取当前可用证券主数据"],
] as const

const DATA_CONTEXT_METHODS = [
  ["publish", "publish(dataset, frame)", "校验并写入统一研究数据仓库"],
  ["expect", "expect(dataset, operation, **details)", "声明数据配方预览步骤"],
  ["read", "read(dataset)", "读取已经发布的运行时数据集"],
  ["output", "output(name, value)", "发布有界的同步结果摘要"],
  ["finalize", "finalize()", "对已发布数据集执行质量契约"],
  ["result", "result()", "返回当前配方结果摘要"],
] as const

let fieldsPromise: Promise<PythonSdkField[]> | null = null

function loadRuntimeFields() {
  if (fieldsPromise) return fieldsPromise
  fieldsPromise = api.get<{ datasets: Record<string, Array<{ name: string; data_type: string; nullable: boolean }>> }>("/strategy/fields?profile=runtime")
    .then((payload) => Object.entries(payload.datasets).flatMap(([dataset, items]) => items.map((item) => ({
      name: item.name,
      dataset,
      detail: `${item.data_type}${item.nullable ? " · 可空" : ""}`,
    }))))
    .catch(() => [])
  return fieldsPromise
}

const DECORATORS = [
  ["factor", '@factor(id="${1:factor_id}", label="${2:因子}", inputs=["${3:close}"])\n'],
  ["signal", '@signal(id="${1:signal_id}", label="${2:信号}", schedule=${3:Monthly.last_trading_day(at="close")})\n'],
  ["portfolio", '@portfolio(id="${1:portfolio_id}", label="${2:组合}")\n'],
  ["execution", '@execution(id="${1:execution_id}", label="${2:执行}")\n'],
  ["universe", '@universe(id="${1:universe_id}", label="${2:标的池}")\n'],
  ["on_event", '@on_event(Event.${1:SESSION_CLOSE})\n'],
] as const

function installSdkProviders() {
  if (sdkProvidersInstalled) return
  sdkProvidersInstalled = true
  monaco.languages.registerCompletionItemProvider("python", {
    triggerCharacters: [".", "\"", "'", "@"],
    provideCompletionItems(model, position) {
      const catalog = sdkCatalogs.get(model.uri.toString()) ?? { kind: "strategy", fields: [], factors: [], parameters: [], rqOperations: [] }
      const prefix = model.getValueInRange({
        startLineNumber: position.lineNumber,
        startColumn: 1,
        endLineNumber: position.lineNumber,
        endColumn: position.column,
      })
      const word = model.getWordUntilPosition(position)
      const range = new monaco.Range(position.lineNumber, word.startColumn, position.lineNumber, word.endColumn)
      if (/context\.\w*$/.test(prefix)) {
        const methods = catalog.kind === "data" ? DATA_CONTEXT_METHODS : CONTEXT_METHODS
        return {
          suggestions: methods.map(([label, signature, documentation]) => ({
            label,
            kind: monaco.languages.CompletionItemKind.Method,
            detail: `AlphaLab Context · ${signature}`,
            documentation,
            insertText: `${label}($0)`,
            insertTextRules: monaco.languages.CompletionItemInsertTextRule.InsertAsSnippet,
            range,
          })),
        }
      }
      if (catalog.kind === "data" && /rq\.\w*$/.test(prefix)) {
        return {
          suggestions: catalog.rqOperations.map((operation) => ({
            label: operation.name,
            kind: monaco.languages.CompletionItemKind.Method,
            detail: `RQData · ${operation.name}${operation.signature}`,
            documentation: operation.documentation,
            insertText: `${operation.name}($0)`,
            insertTextRules: monaco.languages.CompletionItemInsertTextRule.InsertAsSnippet,
            range,
          })),
        }
      }
      const fieldCall = /context\.(history|current|fundamental)\(\s*["'][^"']*$/.exec(prefix)
      if (fieldCall) {
        const method = fieldCall[1]
        const wantedDataset = method === "fundamental" ? "fundamentals" : "market_bars"
        return {
          suggestions: catalog.fields
            .filter((field) => !field.dataset || field.dataset === wantedDataset)
            .map((field) => ({
              label: field.name,
              kind: monaco.languages.CompletionItemKind.Field,
              detail: `AlphaLab 数据字段 · ${field.dataset ?? wantedDataset}`,
              documentation: field.detail,
              insertText: field.name,
              range,
            })),
        }
      }
      if (/context\.factor\(\s*["'][^"']*$/.test(prefix)) {
        return {
          suggestions: catalog.factors.map((factor) => ({
            label: factor.id,
            kind: monaco.languages.CompletionItemKind.Reference,
            detail: `项目因子${factor.label ? ` · ${factor.label}` : ""}`,
            insertText: factor.id,
            range,
          })),
        }
      }
      if (/^\s*@\w*$/.test(prefix)) {
        if (catalog.kind === "data") {
          return {
            suggestions: [{
              label: "@data_recipe",
              kind: monaco.languages.CompletionItemKind.Function,
              detail: "AlphaLab Data SDK 装饰器",
              insertText: '@data_recipe(id="${1:recipe_id}", label="${2:数据配方}")\n',
              insertTextRules: monaco.languages.CompletionItemInsertTextRule.InsertAsSnippet,
              range,
            }],
          }
        }
        return {
          suggestions: DECORATORS.map(([label, insertText]) => ({
            label: `@${label}`,
            kind: monaco.languages.CompletionItemKind.Function,
            detail: "AlphaLab SDK 装饰器",
            insertText,
            insertTextRules: monaco.languages.CompletionItemInsertTextRule.InsertAsSnippet,
            range,
          })),
        }
      }
      return { suggestions: [] }
    },
  })
  monaco.languages.registerHoverProvider("python", {
    provideHover(model, position) {
      const word = model.getWordAtPosition(position)
      if (!word) return null
      const parameter = sdkCatalogs.get(model.uri.toString())?.parameters.find((item) => item.name === word.word)
      if (!parameter) return null
      const bounds = [
        parameter.minimum != null ? `最小值 ${parameter.minimum}` : "",
        parameter.maximum != null ? `最大值 ${parameter.maximum}` : "",
        parameter.step != null ? `步长 ${parameter.step}` : "",
      ].filter(Boolean).join(" · ")
      return {
        range: new monaco.Range(position.lineNumber, word.startColumn, position.lineNumber, word.endColumn),
        contents: [
          { value: `**AlphaLab 参数** \`${parameter.name}: ${parameter.annotation ?? "Any"}\`` },
          { value: parameter.description || "由 Python 默认值投影到无代码参数面板。" },
          ...(bounds ? [{ value: bounds }] : []),
        ],
      }
    },
  })
}

function serverLabel(status: EditorServerStatus) {
  return ({ starting: "连接中", ready: "就绪", missing: "未安装", error: "连接失败" } as const)[status]
}

function markerSeverity(severity: Diagnostic["severity"]) {
  if (severity === "error") return monaco.MarkerSeverity.Error
  if (severity === "warning") return monaco.MarkerSeverity.Warning
  return monaco.MarkerSeverity.Info
}

export const PythonEditor = forwardRef<PythonEditorHandle, PythonEditorProps>(function PythonEditor({
  kind,
  documentId,
  value,
  version,
  baselineValue,
  disabled = false,
  height = 620,
  className,
  revealLine,
  fields = [],
  factors = [],
  parameters = [],
  onChange,
  onSave,
}, forwardedRef) {
  const { theme } = useTheme()
  const runtime = useSyncExternalStore(
    subscribePythonEditorRuntime,
    getPythonEditorRuntimeSnapshot,
    getPythonEditorRuntimeSnapshot,
  )
  const container = useRef<HTMLDivElement>(null)
  const editorRef = useRef<MonacoEditor | null>(null)
  const modelRef = useRef<MonacoModel | null>(null)
  const adoptedSharedModel = useRef(false)
  const onChangeRef = useRef(onChange)
  const onSaveRef = useRef(onSave)
  const [documentUri, setDocumentUri] = useState("")
  const [problems, setProblems] = useState<monaco.editor.IMarker[]>([])
  const [showProblems, setShowProblems] = useState(false)
  const [showDiff, setShowDiff] = useState(false)
  const [cursor, setCursor] = useState({ line: 1, column: 1 })
  const [discoveredFields, setDiscoveredFields] = useState<PythonSdkField[]>([])
  const effectiveFields = fields.length ? fields : discoveredFields
  const sdkCatalog = useMemo(() => ({
    kind,
    fields: effectiveFields,
    factors,
    parameters,
    rqOperations: runtime.capabilities?.rqdata_operations ?? [],
  }), [effectiveFields, factors, kind, parameters, runtime.capabilities?.rqdata_operations])

  onChangeRef.current = onChange
  onSaveRef.current = onSave

  useEffect(() => { void startPythonEditorRuntime() }, [])
  useEffect(() => {
    if (kind !== "strategy" || fields.length) return
    let current = true
    void loadRuntimeFields().then((items) => { if (current) setDiscoveredFields(items) })
    return () => { current = false }
  }, [fields.length, kind])
  useEffect(() => {
    let current = true
    void preparePythonDocument(kind, documentId, value).then((document) => {
      if (current) setDocumentUri(document.uri)
    }).catch(() => {
      if (current) setDocumentUri(`file:///alphalab/${kind}/${encodeURIComponent(documentId)}.py`)
    })
    return () => { current = false }
  }, [documentId, kind]) // eslint-disable-line react-hooks/exhaustive-deps -- source changes use the debounced mirror below

  useEffect(() => {
    if (!runtime.initialized || !documentUri || !container.current) return
    installSdkProviders()
    const uri = monaco.Uri.parse(documentUri)
    let model = monaco.editor.getModel(uri)
    const existingMetadata = modelMetadata.get(documentUri)
    if (!model) {
      model = monaco.editor.createModel(value, "python", uri)
      modelMetadata.set(documentUri, { externalValue: value, externalVersion: version })
    } else if (model.getValue() !== value) {
      const valueIsLastCanonicalSource = existingMetadata?.externalVersion === version
        && existingMetadata?.externalValue === value
      if (valueIsLastCanonicalSource) {
        adoptedSharedModel.current = true
        onChangeRef.current(model.getValue())
      } else {
        model.setValue(value)
      }
    }
    modelRef.current = model
    sdkCatalogs.set(documentUri, sdkCatalog)
    const editor = monaco.editor.create(container.current, {
      model,
      readOnly: disabled,
      automaticLayout: true,
      minimap: { enabled: true },
      fontFamily: "JetBrains Mono, SFMono-Regular, Consolas, Liberation Mono, monospace",
      fontSize: 12,
      lineHeight: 20,
      folding: true,
      glyphMargin: true,
      bracketPairColorization: { enabled: true },
      guides: { bracketPairs: true, indentation: true },
      quickSuggestions: { other: true, comments: false, strings: true },
      suggestOnTriggerCharacters: true,
      renderValidationDecorations: "on",
      scrollBeyondLastLine: false,
      smoothScrolling: true,
      padding: { top: 10, bottom: 10 },
    })
    editorRef.current = editor
    const change = model.onDidChangeContent(() => onChangeRef.current(model!.getValue()))
    const position = editor.onDidChangeCursorPosition((event) => {
      setCursor({ line: event.position.lineNumber, column: event.position.column })
    })
    editor.addAction({
      id: "alphalab.savePythonDraft",
      label: "保存 AlphaLab Python 草稿",
      keybindings: [monaco.KeyMod.CtrlCmd | monaco.KeyCode.KeyS],
      run: () => onSaveRef.current?.(model!.getValue()),
    })
    if (revealLine) {
      editor.revealLineInCenter(revealLine)
      editor.setPosition({ lineNumber: revealLine, column: 1 })
    }
    return () => {
      position.dispose()
      change.dispose()
      editor.dispose()
      editorRef.current = null
      modelRef.current = null
    }
  }, [documentUri, runtime.initialized]) // eslint-disable-line react-hooks/exhaustive-deps -- create once per shared model; later props have dedicated effects

  useEffect(() => {
    if (!documentUri) return
    sdkCatalogs.set(documentUri, sdkCatalog)
  }, [documentUri, sdkCatalog])

  useEffect(() => {
    const model = modelRef.current
    if (!model || !documentUri) return
    if (adoptedSharedModel.current) {
      adoptedSharedModel.current = false
      return
    }
    const previous = modelMetadata.get(documentUri)
    if (!previous) {
      modelMetadata.set(documentUri, { externalValue: value, externalVersion: version })
      return
    }
    const modelValue = model.getValue()
    if (previous.externalVersion === version) {
      if (modelValue !== value && value === previous.externalValue) model.setValue(value)
      return
    }
    if (modelValue === previous.externalValue && modelValue !== value) model.setValue(value)
    modelMetadata.set(documentUri, { externalValue: value, externalVersion: version })
  }, [documentUri, value, version])

  useEffect(() => { editorRef.current?.updateOptions({ readOnly: disabled }) }, [disabled])
  useEffect(() => {
    if (!runtime.initialized) return
    monaco.editor.setTheme(theme === "dark" ? "vs-dark" : "vs")
  }, [runtime.initialized, theme])
  useEffect(() => {
    if (!revealLine || !editorRef.current) return
    editorRef.current.revealLineInCenter(revealLine)
    editorRef.current.setPosition({ lineNumber: revealLine, column: 1 })
  }, [revealLine])

  useEffect(() => {
    const model = modelRef.current
    if (!model || !documentUri) return
    const refresh = () => {
      const next = monaco.editor.getModelMarkers({ resource: model.uri })
      setProblems(next)
      if (next.some((item) => item.severity === monaco.MarkerSeverity.Error)) setShowProblems(true)
    }
    refresh()
    const disposable = monaco.editor.onDidChangeMarkers((resources) => {
      if (resources.some((resource) => resource.toString() === documentUri)) refresh()
    })
    return () => disposable.dispose()
  }, [documentUri, runtime.initialized])

  useEffect(() => {
    const model = modelRef.current
    if (!model || !documentUri) return
    const timer = window.setTimeout(() => {
      const source = model.getValue()
      void Promise.all([
        api.post<DiagnosticsPayload>("/python-editor/diagnostics", { kind, source }),
        preparePythonDocument(kind, documentId, source),
      ]).then(([payload]) => {
        if (model.isDisposed() || model.getValue() !== source) return
        monaco.editor.setModelMarkers(model, "alphalab-sdk", payload.diagnostics.map((item) => ({
          severity: markerSeverity(item.severity),
          startLineNumber: Math.max(1, item.line),
          startColumn: Math.max(1, item.column),
          endLineNumber: Math.max(1, item.line),
          endColumn: Math.max(2, item.column + 1),
          message: item.message,
          code: item.code,
          source: item.source,
        })))
      }).catch(() => undefined)
    }, 500)
    return () => window.clearTimeout(timer)
  }, [documentId, documentUri, kind, value])

  useImperativeHandle(forwardedRef, () => ({
    focus: () => editorRef.current?.focus(),
    insertText: (text: string) => {
      const editor = editorRef.current
      const model = modelRef.current
      if (!editor || !model) return
      const selection = editor.getSelection() ?? new monaco.Selection(model.getLineCount(), model.getLineMaxColumn(model.getLineCount()), model.getLineCount(), model.getLineMaxColumn(model.getLineCount()))
      editor.executeEdits("alphalab-sdk", [{ range: selection, text, forceMoveMarkers: true }])
      editor.focus()
    },
    revealLine: (line: number) => {
      editorRef.current?.revealLineInCenter(line)
      editorRef.current?.setPosition({ lineNumber: line, column: 1 })
      editorRef.current?.focus()
    },
    getValue: () => modelRef.current?.getValue() ?? value,
  }), [value])

  const editorStyle: CSSProperties = { height }
  const format = () => void editorRef.current?.getAction("editor.action.formatDocument")?.run()
  return <section className={cn("python-editor-shell", className)}>
    <div className="python-editor-toolbar">
      <span className="python-editor-language"><Code2 size={13} />Python</span>
      <button type="button" onClick={format} title="Shift+Alt+F"><Wand2 size={13} />格式化</button>
      {baselineValue !== undefined ? <button type="button" className={showDiff ? "active" : ""} onClick={() => setShowDiff((current) => !current)}><GitCompare size={13} />Diff</button> : null}
      <span className={`python-editor-server ${runtime.pyrefly}`}><Sparkles size={12} />Pyrefly {serverLabel(runtime.pyrefly)}</span>
      <span className={`python-editor-server ${runtime.ruff}`}><CheckCircle2 size={12} />Ruff {serverLabel(runtime.ruff)}</span>
      <span className="python-editor-position">Ln {cursor.line}, Col {cursor.column}</span>
    </div>
    {runtime.error ? <div className="python-editor-runtime-error">编辑器初始化失败：{runtime.error}</div> : null}
    <div ref={container} className="python-editor-canvas" style={{ ...editorStyle, display: showDiff ? "none" : undefined }} />
    {showDiff && baselineValue !== undefined && documentUri ? <PythonDiffEditor original={baselineValue} modified={value} uri={documentUri} height={height} /> : null}
    <button type="button" className={cn("python-editor-problems-toggle", problems.some((item) => item.severity === monaco.MarkerSeverity.Error) && "error")} onClick={() => setShowProblems((current) => !current)}>
      <AlertCircle size={12} />问题 {problems.length}
    </button>
    {showProblems ? <div className="python-editor-problems">
      {problems.length ? problems.map((problem, index) => <button key={`${problem.owner}-${problem.startLineNumber}-${index}`} type="button" onClick={() => {
        setShowDiff(false)
        editorRef.current?.setPosition({ lineNumber: problem.startLineNumber, column: problem.startColumn })
        editorRef.current?.revealLineInCenter(problem.startLineNumber)
        editorRef.current?.focus()
      }}><span className={problem.severity === monaco.MarkerSeverity.Error ? "error" : "warning"}>{problem.severity === monaco.MarkerSeverity.Error ? "错误" : "警告"}</span><strong>{problem.message}</strong><em>L{problem.startLineNumber}:{problem.startColumn} · {problem.source || problem.owner}</em></button>) : <div>没有发现问题</div>}
    </div> : null}
  </section>
})

function PythonDiffEditor({ original, modified, uri, height }: { original: string; modified: string; uri: string; height: number | string }) {
  const container = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!container.current) return
    const originalUri = monaco.Uri.parse(`${uri}.baseline.py`)
    const modifiedUri = monaco.Uri.parse(uri)
    const originalModel = monaco.editor.getModel(originalUri) ?? monaco.editor.createModel(original, "python", originalUri)
    const modifiedModel = monaco.editor.getModel(modifiedUri) ?? monaco.editor.createModel(modified, "python", modifiedUri)
    if (originalModel.getValue() !== original) originalModel.setValue(original)
    const editor = monaco.editor.createDiffEditor(container.current, {
      automaticLayout: true,
      readOnly: true,
      renderSideBySide: true,
      minimap: { enabled: false },
      fontSize: 12,
      lineHeight: 20,
    })
    editor.setModel({ original: originalModel, modified: modifiedModel })
    return () => editor.dispose()
  }, [modified, original, uri])
  return <div className="python-editor-canvas" ref={container} style={{ height }} />
}
