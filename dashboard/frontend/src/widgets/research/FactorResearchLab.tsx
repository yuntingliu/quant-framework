import { useEffect, useMemo, useState } from "react"
import { FlaskConical, Play, ShieldCheck } from "lucide-react"

import { useGlobalFilter } from "@/contexts/GlobalFilterContext"
import { useLanguage } from "@/contexts/LanguageContext"
import { CSVExportButton } from "@/components/shared/CSVExportButton"
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
  const { language } = useLanguage()
  const copy = language === "zh" ? {
    source: "因子来源",
    technical: "技术面",
    fundamental: "基本面",
    expression: "表达式",
    customName: "自定义因子名称",
    factorName: "因子名称",
    baseFactor: "基础因子",
    direction: "因子方向",
    higher: "数值越高越好",
    lower: "数值越低越好",
    quantiles: "因子分组数",
    neutral: "市值中性化",
    evaluating: "检验中",
    evaluate: "检验",
    safeExpression: "安全因子表达式",
    expressionHint: "输入项为已注册的技术面/基本面因子。可用函数：",
    loading: "加载中",
    meanRankIc: "Rank IC 均值",
    icPositive: "IC 正值占比",
    coverage: "覆盖率",
    topTurnover: "头部分组换手",
    longShortAnnual: "多空年化收益",
    periods: "期",
    universe: "只证券",
    exportPeriods: "导出分期数据",
    date: "日期",
    observations: "样本数",
    longShort: "多空收益",
    empty: "请定义因子并运行点时横截面诊断。",
  } : {
    source: "Factor source",
    technical: "Technical",
    fundamental: "Fundamental",
    expression: "Expression",
    customName: "Custom factor name",
    factorName: "Factor name",
    baseFactor: "Base factor",
    direction: "Factor direction",
    higher: "Higher is better",
    lower: "Lower is better",
    quantiles: "Factor quantiles",
    neutral: "Market-cap neutral",
    evaluating: "Evaluating",
    evaluate: "Evaluate",
    safeExpression: "Safe factor expression",
    expressionHint: "Inputs are registered technical/fundamental factors. Functions:",
    loading: "loading",
    meanRankIc: "Mean Rank IC",
    icPositive: "IC positive",
    coverage: "Coverage",
    topTurnover: "Top turnover",
    longShortAnnual: "Long-short annual",
    periods: "periods",
    universe: "universe",
    exportPeriods: "Export periods",
    date: "Date",
    observations: "Obs",
    longShort: "Long-short",
    empty: "Define a factor and run point-in-time cross-sectional diagnostics.",
  }
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

  const exportRows = useMemo(() => {
    if (!result) return []
    return result.rows.map((row) => {
      const record: Record<string, unknown> = {
        date: row.date,
        observations: row.observations,
        coverage: row.coverage,
        rank_ic: row.ic,
        long_short: row.long_short,
        top_turnover: row.top_turnover,
      }
      for (let index = 1; index <= result.quantiles; index += 1) {
        record[`q${index}_return`] = row.quantile_returns[String(index)]
      }
      return record
    })
  }, [result])

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
        <select aria-label={copy.source} value={source} onChange={(event) => setSource(event.target.value as typeof source)}>
          <option value="technical">{copy.technical}</option>
          <option value="fundamental">{copy.fundamental}</option>
          <option value="expression">{copy.expression}</option>
        </select>
        {source === "expression" ? (
          <input aria-label={copy.customName} value={name} onChange={(event) => setName(event.target.value)} placeholder={copy.factorName} />
        ) : (
          <select aria-label={copy.baseFactor} value={name} onChange={(event) => setName(event.target.value)}>
            {candidates.map((factor) => <option key={factor.name} value={factor.name}>{factor.name}</option>)}
          </select>
        )}
        <select aria-label={copy.direction} value={direction} onChange={(event) => setDirection(event.target.value as typeof direction)}>
          <option value="long">{copy.higher}</option>
          <option value="short">{copy.lower}</option>
        </select>
        <input aria-label={copy.quantiles} type="number" min={3} max={10} value={quantiles} onChange={(event) => setQuantiles(Math.max(3, Math.min(10, Number(event.target.value) || 5)))} />
        <label className="inline-check">
          <input type="checkbox" checked={neutralizeMarketCap} onChange={(event) => setNeutralizeMarketCap(event.target.checked)} />
          {copy.neutral}
        </label>
        <button className="primary-command" type="button" onClick={evaluate} disabled={running || !name || (source === "expression" && !expression)}>
          <Play aria-hidden="true" /> {running ? copy.evaluating : copy.evaluate}
        </button>
      </div>
      {source === "expression" && (
        <div className="factor-expression-editor">
          <label htmlFor="factor-expression">{copy.safeExpression}</label>
          <textarea id="factor-expression" value={expression} onChange={(event) => setExpression(event.target.value)} spellCheck={false} />
          <small>{copy.expressionHint} {library?.expression_functions.join(", ") || copy.loading}.</small>
        </div>
      )}
      {error && <div className="workbench-message error">{error}</div>}
      {result && (
        <>
          <div className="analytics-kpi-grid workbench-kpis">
            <div className="analytics-kpi"><span>{copy.meanRankIc}</span><strong>{number(result.summary.ic_mean)}</strong></div>
            <div className="analytics-kpi"><span>ICIR</span><strong>{number(result.summary.icir)}</strong></div>
            <div className="analytics-kpi"><span>{copy.icPositive}</span><strong>{percent(result.summary.ic_positive_ratio)}</strong></div>
            <div className="analytics-kpi"><span>{copy.coverage}</span><strong>{percent(result.summary.coverage_mean)}</strong></div>
            <div className="analytics-kpi"><span>{copy.topTurnover}</span><strong>{percent(result.summary.top_turnover_mean)}</strong></div>
            <div className="analytics-kpi"><span>{copy.longShortAnnual}</span><strong>{percent(result.summary.long_short?.annual_return)}</strong></div>
          </div>
          <div className="detail-strip">
            <span>{result.periods} {copy.periods} · {result.universe_size} {copy.universe}</span>
            <strong>
              Bootstrap IC 95% [{number(result.summary.bootstrap_ic_95?.lower)}, {number(result.summary.bootstrap_ic_95?.upper)}]
            </strong>
            <CSVExportButton
              data={exportRows}
              filename={`alphalab-factor-${result.factor.name}-${profile}`}
              label={copy.exportPeriods}
            />
          </div>
          {result.warnings.map((warning) => (
            <div className="workbench-message research-message" key={warning}><ShieldCheck aria-hidden="true" /> {warning}</div>
          ))}
          <div className="analytics-table-wrap">
            <table className="analytics-table compact">
              <thead>
                <tr>
                  <th>{copy.date}</th><th>{copy.observations}</th><th>{copy.coverage}</th><th>Rank IC</th><th>{copy.longShort}</th><th>{copy.topTurnover}</th>
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
      {!result && !error && <div className="analytics-empty"><FlaskConical aria-hidden="true" /> {copy.empty}</div>}
    </div>
  )
}
