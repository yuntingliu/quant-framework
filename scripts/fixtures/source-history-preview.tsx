import React, { useEffect, useRef, useState } from 'react'
import { createRoot } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { LanguageProvider } from '/src/contexts/LanguageContext'
import { ThemeProvider } from '/src/contexts/ThemeContext'
import { WorkspaceProvider } from '/src/contexts/WorkspaceContext'
import { StrategySdkProvider, useStrategySdk } from '/src/contexts/StrategySdkContext'
import { ConfirmProvider } from '/src/hooks/useConfirm'
import { StrategyWorkbenchWidget } from '/src/widgets/strategy/StrategyWorkbench'
import { FactorWorkbenchWidget } from '/src/widgets/factors/FactorWorkbench'
import { SourceHistoryPanel } from '/src/components/shared/SourceHistoryPanel'
import '/src/index.css'

const labels = { strategy: '策略', factor: '共享因子', recipe: '数据配方', validation: '回测验证' } as const
const queries = new QueryClient({ defaultOptions: { queries: { retry: false } } })

export function Demo() {
  const sdk = useStrategySdk()
  const [kind, setKind] = useState<keyof typeof labels>('strategy')
  const opened = useRef(false)
  useEffect(() => {
    if (sdk.loading || !sdk.project || opened.current) return
    opened.current = true
    void sdk.openProject('history-demo', 'copy').catch(() => undefined)
  }, [sdk])
  return <main className="flex h-dvh min-w-0 flex-col bg-background">
    <header className="flex flex-wrap items-center gap-3 border-b px-4 py-2">
      <strong>动量策略研究 · 本地示例</strong>
      {Object.entries(labels).map(([key, label]) => <button key={key} type="button"
        aria-pressed={kind === key} className={kind === key ? 'font-semibold text-primary' : ''}
        onClick={() => setKind(key as keyof typeof labels)}>{label}</button>)}
    </header>
    <div className="min-h-0 flex-1 overflow-auto">
      {kind === 'strategy' && sdk.project?.id === 'history-demo' && <StrategyWorkbenchWidget initialSourceView="restore" />}
      {kind === 'factor' && <FactorWorkbenchWidget />}
      {(kind === 'recipe' || kind === 'validation') && <section className="source-review-workspace"><div className="source-review-content" data-wrap="true">
          <SourceHistoryPanel projectId="history-demo" projectName="动量策略研究" kind={kind} />
        </div></section>}
    </div>
  </main>
}
createRoot(document.getElementById('root')!).render(<QueryClientProvider client={queries}>
  <LanguageProvider><ThemeProvider><WorkspaceProvider><StrategySdkProvider><ConfirmProvider>
    <Demo />
  </ConfirmProvider></StrategySdkProvider></WorkspaceProvider></ThemeProvider></LanguageProvider>
</QueryClientProvider>)
