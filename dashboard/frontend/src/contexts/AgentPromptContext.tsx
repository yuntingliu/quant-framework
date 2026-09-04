import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react"

import {
  parseWorkspaceReportNode,
  type AgentResearchResult,
} from "@/workspace/researchResults"
import { readWorkspace } from "@/lib/conexus/publishedHarnessClient"

export interface AgentDecisionNotebook {
  runId?: string
  updatedAt?: string
  classification?: string
  baseCase?: string
  riskCase?: string
  nextAction?: string
  candidateExpressions?: string[]
}

export interface StagedAgentPrompt {
  id: string
  message: string
  context: Record<string, unknown>
}

interface AgentPromptContextValue {
  stagedPrompt: StagedAgentPrompt | null
  stagePrompt: (message: string, context: Record<string, unknown>) => void
  consumePrompt: (id: string) => void
  decisionNotebook: AgentDecisionNotebook | null
  setDecisionNotebook: (value: AgentDecisionNotebook | null) => void
  researchResults: AgentResearchResult[]
  registerResearchResult: (value: AgentResearchResult) => void
  refreshResearchResults: () => Promise<void>
}

const AgentPromptContext = createContext<AgentPromptContextValue | null>(null)
const MAX_SAVED_RESULTS = 100

export function AgentPromptProvider({ children }: { children: ReactNode }) {
  const [stagedPrompt, setStagedPrompt] = useState<StagedAgentPrompt | null>(null)
  const [decisionNotebook, setDecisionNotebook] = useState<AgentDecisionNotebook | null>(null)
  const [researchResults, setResearchResults] = useState<AgentResearchResult[]>([])
  const registerResearchResult = useCallback((result: AgentResearchResult) => {
    setResearchResults((current) => [
      result,
      ...current.filter((item) => item.id !== result.id),
    ].slice(0, MAX_SAVED_RESULTS))
  }, [])

  const refreshResearchResults = useCallback(async () => {
    const workspace = await readWorkspace()
    const persisted = workspace.nodes
      .map(parseWorkspaceReportNode)
      .filter((item): item is AgentResearchResult => item !== null)
      .sort((left, right) => (right.updatedAt ?? "").localeCompare(left.updatedAt ?? ""))
      .slice(0, MAX_SAVED_RESULTS)
    setResearchResults((current) => persisted.map((saved) => {
      const cached = current.find((item) => item.id === saved.id)
      return cached ? { ...cached, ...saved } : saved
    }))
  }, [])

  useEffect(() => {
    const refresh = () => { void refreshResearchResults().catch(() => undefined) }
    refresh()
    window.addEventListener("focus", refresh)
    return () => window.removeEventListener("focus", refresh)
  }, [refreshResearchResults])

  const value = useMemo<AgentPromptContextValue>(() => ({
    stagedPrompt,
    stagePrompt: (message, context) => setStagedPrompt({
      id: `${Date.now()}-${Math.random().toString(36).slice(2)}`,
      message,
      context,
    }),
    consumePrompt: (id) => setStagedPrompt((current) => current?.id === id ? null : current),
    decisionNotebook,
    setDecisionNotebook,
    researchResults,
    registerResearchResult,
    refreshResearchResults,
  }), [decisionNotebook, refreshResearchResults, registerResearchResult, researchResults, stagedPrompt])

  return <AgentPromptContext.Provider value={value}>{children}</AgentPromptContext.Provider>
}

export function useAgentPrompt(): AgentPromptContextValue {
  const value = useContext(AgentPromptContext)
  if (!value) throw new Error("useAgentPrompt must be used inside AgentPromptProvider")
  return value
}
