import { useEffect, useMemo, useState } from "react"
import { FlaskConical, Play, ShieldCheck } from "lucide-react"

import { useGlobalFilter } from "@/contexts/GlobalFilterContext"
import {
  api,
  type FactorResearchLibrary,
  type FactorResearchResult,
  type ProviderStatus,
} from "@/lib/api"
import { useDataProfile } from "@/lib/data-profile"

function monthStart(value: string): string {
  return `${value}-01`
}

function monthEnd(value: string): string {
  const [year, month] = value.split("-").map(Number)
  return new Date(Date.UTC(year, month, 0)).toISOString().slice(0, 10)
}

function percent(value: number | null | undefined): string {
  return value == null ? "--" : `${(value * 100).toFixed(2)}%`
}

function number(value: number | null | undefined, digits = 3): string {
  return value == null ? "--" : value.toFixed(digits)
}

export function FactorResearchLab() {
  const { startDate, endDate } = useGlobalFilter()
  const [profile] = useDataProfile()
  const [library, setLibrary] = useState<FactorResearchLibrary | null>(null)
  const [providerStatus, setProviderStatus] = useState<ProviderStatus | null>(null)
  const [source, setSource] = useState<"technical" | "fundamental" | "expression">("technical")
  const [name, setName] = useState("momentum_20d")
  const [expression, setExpression] = useState("zscore(momentum_60d) - 0.5 * zscore(volatility_20d)")
  const [direction, setDirection] = useState<"long" | "short">("long")
  const [neutralizeMarketCap, setNeutralizeMarketCap] = useState(false)
  const [quantiles, setQuantiles] = useState(5)
  const [result, setResult] = useState<FactorResearchResult | null>(null)
  const [error, setError] = useState("")
  const [running, setRunning] = useState(false)

  useEffect(() => {
    Promise.all([
      api.get<FactorResearchLibrary>("/factor-research/library"),
      api.get<ProviderStatus>("/data/providers"),
    ])
      .then(([factorLibrary, status]) => {
        setLibrary(factorLibrary)
        setProviderStatus(status)
      })
      .catch((loadError: Error) => setError(loadError.message))
  }, [])

  const candidates = useMemo(
    () => library?.factors.filter((factor) => factor.source === source) ?? [],
    [library, source],
  )

  useEffect(() => {
    if (source === "expression") {
      setName("custom_factor")
      return
    }
    if (!candidates.some((factor) => factor.name === name) && candidates[0]) {
      setName(candidates[0].name)
    }
  }, [candidates, name, source])

  async function evaluate() {
    setRunning(true)
    setError("")
    try {
      const latest = providerStatus?.profiles[profile]?.latest_date ?? new Date().toISOString().slice(0, 10)
      const fallbackEnd = latest.slice(0, 7)
      const fallbackStart = `${Math.max(1900, Number(fallbackEnd.slice(0, 4)) - 5)}-${fallbackEnd.slice(5, 7)}`
      const payload = await api.post<FactorResearchResult>("/factor-research/evaluate", {
        profile,
        name,
        source,
        ...(source === "expression" ? { expression } : {}),
        direction,
        winsorize: 0.01,
        neutralize: neutralizeMarketCap ? ["market_cap"] : [],
        start_date: monthStart(startDate || fallbackStart),
        end_date: monthEnd(endDate || fallbackEnd),
        frequency: "monthly",
        quantiles,
      })
      setResult(payload)
    } catch (runError) {
      setError(runError instanceof Error ? runError.message : String(runError))
    } finally {
      setRunning(false)
    }
  }

  return (
    <div className="workbench-body factor-research-lab">
      <div className="workbench-controls">
        <select aria-label="Factor source" value={source} onChange={(event) => setSource(event.target.value as typeof source)}>
          <option value="technical">Technical</option>
          <option value="fundamental">Fundamental</option>
          <option value="expression">Expression</option>
        </select>
        {source === "expression" ? (
          <input aria-label="Custom factor name" value={name} onChange={(event) => setName(event.target.value)} placeholder="Factor name" />
        ) : (
          <select aria-label="Base factor" value={name} onChange={(event) => setName(event.target.value)}>
            {candidates.map((factor) => <option key={factor.name} value={factor.name}>{factor.name}</option>)}
          </select>
        )}
        <select aria-label="Factor direction" value={direction} onChange={(event) => setDirection(event.target.value as typeof direction)}>
          <option value="long">Higher is better</option>
          <option value="short">Lower is better</option>
        </select>
        <input aria-label="Factor quantiles" type="number" min={3} max={10} value={quantiles} onChange={(event) => setQuantiles(Math.max(3, Math.min(10, Number(event.target.value) || 5)))} />
        <label className="inline-check">
          <input type="checkbox" checked={neutralizeMarketCap} onChange={(event) => setNeutralizeMarketCap(event.target.checked)} />
          Market-cap neutral
        </label>
        <button className="primary-command" type="button" onClick={evaluate} disabled={running || !name || (source === "expression" && !expression)}>
          <Play aria-hidden="true" /> {running ? "Evaluating" : "Evaluate"}
        </button>
      </div>
      {source === "expression" && (
        <div className="factor-expression-editor">
          <label htmlFor="factor-expression">Safe factor expression</label>
          <textarea id="factor-expression" value={expression} onChange={(event) => setExpression(event.target.value)} spellCheck={false} />
          <small>Inputs are registered technical/fundamental factors. Functions: {library?.expression_functions.join(", ") || "loading"}.</small>
        </div>
      )}
      {error && <div className="workbench-message error">{error}</div>}
      {result && (
        <>
          <div className="analytics-kpi-grid workbench-kpis">
            <div className="analytics-kpi"><span>Mean Rank IC</span><strong>{number(result.summary.ic_mean)}</strong></div>
            <div className="analytics-kpi"><span>ICIR</span><strong>{number(result.summary.icir)}</strong></div>
            <div className="analytics-kpi"><span>IC positive</span><strong>{percent(result.summary.ic_positive_ratio)}</strong></div>
            <div className="analytics-kpi"><span>Coverage</span><strong>{percent(result.summary.coverage_mean)}</strong></div>
            <div className="analytics-kpi"><span>Top turnover</span><strong>{percent(result.summary.top_turnover_mean)}</strong></div>
            <div className="analytics-kpi"><span>Long-short annual</span><strong>{percent(result.summary.long_short?.annual_return)}</strong></div>
          </div>
          <div className="detail-strip">
            <span>{result.periods} periods · {result.universe_size} universe</span>
            <strong>
              Bootstrap IC 95% [{number(result.summary.bootstrap_ic_95?.lower)}, {number(result.summary.bootstrap_ic_95?.upper)}]
            </strong>
          </div>
          {result.warnings.map((warning) => (
            <div className="workbench-message research-message" key={warning}><ShieldCheck aria-hidden="true" /> {warning}</div>
          ))}
          <div className="analytics-table-wrap">
            <table className="analytics-table compact">
              <thead>
                <tr>
                  <th>Date</th><th>Obs</th><th>Coverage</th><th>Rank IC</th><th>Long-short</th><th>Top turnover</th>
                  {Array.from({ length: result.quantiles }, (_, index) => <th key={index}>Q{index + 1}</th>)}
                </tr>
              </thead>
              <tbody>
                {result.rows.map((row) => (
                  <tr key={row.date}>
                    <td>{row.date}</td><td>{row.observations}</td><td>{percent(row.coverage)}</td><td>{number(row.ic)}</td><td>{percent(row.long_short)}</td><td>{percent(row.top_turnover)}</td>
                    {Array.from({ length: result.quantiles }, (_, index) => <td key={index}>{percent(row.quantile_returns[String(index + 1)])}</td>)}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
      {!result && !error && <div className="analytics-empty"><FlaskConical aria-hidden="true" /> Define a factor and run point-in-time cross-sectional diagnostics.</div>}
    </div>
  )
}
