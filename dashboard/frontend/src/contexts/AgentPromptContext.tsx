import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react"

import { parseAgentResearchResult, type AgentResearchResult } from "@/workspace/researchResults"
import { api } from "@/lib/api"
import { getDataProfile } from "@/lib/data-profile"

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
}

const AgentPromptContext = createContext<AgentPromptContextValue | null>(null)
const RESEARCH_RESULTS_KEY = "alphalab.agent-research-results.v1"
const MAX_SAVED_RESULTS = 100

function migrateSavedResearchResult(value: unknown): unknown {
  if (!value || typeof value !== "object" || Array.isArray(value)) return value
  const saved = value as Record<string, unknown>
  if (saved.kind !== "table") return value
  const title = typeof saved.title === "string" && saved.title.trim() ? saved.title.trim() : "Research result"
  const description = typeof saved.description === "string" && saved.description.trim()
    ? saved.description.trim()
    : "Migrated from an earlier interactive-table result."
  return {
    ...saved,
    kind: "document",
    markdown: `# ${title}\n\n${description}`,
  }
}

function loadResearchResults(): AgentResearchResult[] {
  try {
    const raw = localStorage.getItem(RESEARCH_RESULTS_KEY)
    if (!raw) return []
    const values = JSON.parse(raw)
    if (!Array.isArray(values)) return []
    return values
      .map((rawValue) => {
        const value = migrateSavedResearchResult(rawValue) as Record<string, unknown>
        return parseAgentResearchResult(value, {
        markdown: typeof value?.markdown === "string" ? value.markdown : undefined,
        runId: typeof value?.runId === "string" ? value.runId : undefined,
        artifactId: typeof value?.artifactId === "string" ? value.artifactId : undefined,
        updatedAt: typeof value?.updatedAt === "string" ? value.updatedAt : undefined,
        })
      })
      .filter((value): value is AgentResearchResult => value !== null)
      .slice(0, MAX_SAVED_RESULTS)
  } catch {
    return []
  }
}

export function AgentPromptProvider({ children }: { children: ReactNode }) {
  const [stagedPrompt, setStagedPrompt] = useState<StagedAgentPrompt | null>(null)
  const [decisionNotebook, setDecisionNotebook] = useState<AgentDecisionNotebook | null>(null)
  const [researchResults, setResearchResults] = useState<AgentResearchResult[]>(loadResearchResults)
  const registerResearchResult = useCallback((result: AgentResearchResult) => {
    setResearchResults((current) => [
      result,
      ...current.filter((item) => item.id !== result.id),
    ].slice(0, MAX_SAVED_RESULTS))
    void api.post<AgentResearchResult>("/reports", {
      profile: getDataProfile(),
      result,
    }).then((persisted) => {
      const parsed = parseAgentResearchResult(persisted, {
        markdown: persisted.markdown,
        runId: persisted.runId,
        artifactId: persisted.artifactId,
        updatedAt: persisted.updatedAt,
      })
      if (!parsed) return
      setResearchResults((current) => [
        parsed,
        ...current.filter((item) => item.id !== parsed.id),
      ].slice(0, MAX_SAVED_RESULTS))
    }).catch(() => {
      // The validated local result remains available if backend persistence fails.
    })
  }, [])

  useEffect(() => {
    let cancelled = false
    void api.get<{ items: unknown[] }>("/reports?limit=100").then(({ items }) => {
      if (cancelled) return
      const persisted = items
        .map((item) => parseAgentResearchResult(item, {
          markdown: typeof (item as Record<string, unknown>)?.markdown === "string"
            ? (item as Record<string, unknown>).markdown as string
            : undefined,
        }))
        .filter((item): item is AgentResearchResult => item !== null)
      setResearchResults((current) => [
        ...persisted,
        ...current.filter((item) => !persisted.some((saved) => saved.id === item.id)),
      ].slice(0, MAX_SAVED_RESULTS))
    }).catch(() => {
      // Offline startup continues with browser-local results.
    })
    return () => { cancelled = true }
  }, [])

  useEffect(() => {
    for (let count = researchResults.length; count > 0; count -= 1) {
      try {
        localStorage.setItem(RESEARCH_RESULTS_KEY, JSON.stringify(researchResults.slice(0, count)))
        return
      } catch {
        // Keep shrinking until the newest results fit the browser quota.
      }
    }
    try { localStorage.removeItem(RESEARCH_RESULTS_KEY) } catch { /* ignore */ }
  }, [researchResults])

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
  }), [decisionNotebook, registerResearchResult, researchResults, stagedPrompt])

  return <AgentPromptContext.Provider value={value}>{children}</AgentPromptContext.Provider>
}

export function useAgentPrompt(): AgentPromptContextValue {
  const value = useContext(AgentPromptContext)
  if (!value) throw new Error("useAgentPrompt must be used inside AgentPromptProvider")
  return value
}
