import { useEffect, useMemo, useState } from "react"
import { AlertTriangle, ChevronDown, Database, FlaskConical, Play, Sigma } from "lucide-react"
import {
  Bar,
  BarChart,
  CartesianGrid,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts"

import { CSVExportButton } from "@/components/shared/CSVExportButton"
import { useGlobalFilter } from "@/contexts/GlobalFilterContext"
import { useLanguage } from "@/contexts/LanguageContext"
import {
  api,
  type FactorResearchLibrary,
  type FactorResearchResult,
  type ProviderStatus,
} from "@/lib/api"
import { CHART_COLORS } from "@/lib/constants"
import { useDataProfile } from "@/lib/data-profile"

export interface FactorResearchSelection {
  name: string
  source: "technical" | "fundamental"
}

interface FactorResearchLabProps {
  requestedFactor?: FactorResearchSelection | null
  mode?: "builtin" | "custom"
}

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

function mean(values: Array<number | null | undefined>): number | null {
  const finite = values.filter((value): value is number => value != null && Number.isFinite(value))
  return finite.length ? finite.reduce((sum, value) => sum + value, 0) / finite.length : null
}

function researchVerdict(result: FactorResearchResult): "candidate" | "watch" | "invalid" {
  const ic = result.summary.ic_mean ?? 0
  const coverage = result.summary.coverage_mean ?? 0
  const lower = result.summary.bootstrap_ic_95?.lower
  if (result.periods < 6 || ic <= 0) return "invalid"
  if (result.periods >= 24 && coverage >= 0.6 && lower != null && lower > 0) return "candidate"
  return "watch"
}

export function FactorResearchLab({ requestedFactor, mode = "builtin" }: FactorResearchLabProps) {
  const { language } = useLanguage()
  const copy = language === "zh" ? {
    source: "因子来源",
    technical: "技术面",
    fundamental: "基本面",
    expression: "表达式",
    customName: "自定义因子名称",
    factorName: "因子名称",
    customTitle: "定义自定义横截面因子",
    customHint: "组合已注册的技术面和基本面输入，先运行点时检验，再把表达式加入选股策略。表达式不会执行任意 Python。",
    baseFactor: "股票横截面因子",
    direction: "因子方向",
    higher: "数值越高越好",
    lower: "数值越低越好",
    quantiles: "因子分组数",
    neutral: "市值中性化",
    evaluating: "检验中",
    evaluate: "运行检验",
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
    empty: "选择或定义一个股票横截面因子，然后运行点时横截面检验。",
    dataContext: "横截面检验使用的数据",
    demoProfile: "演示数据",
    runtimeProfile: "本地 RQ 数据",
    dataThrough: "更新至",
    availableSymbols: "可用证券",
    crossSectionNote: "使用该数据源的整个可用股票池做横截面检验，不使用数据工作台当前选中的单只股票。",
    advanced: "高级检验设置",
    advancedHint: "调仓频率、去极值和股票池过滤",
    frequency: "检验频率",
    monthly: "月频",
    weekly: "周频",
    winsorize: "双侧去极值（%）",
    minPrice: "最低价格",
    minHistory: "最少历史天数",
    minAmount: "最低平均成交额",
    candidate: "研究候选",
    watch: "需要观察",
    invalid: "暂不采用",
    candidateDetail: "样本、覆盖率和 Bootstrap 证据达到研究候选门槛；不代表可以直接交易。",
    watchDetail: "存在正向证据，但样本、覆盖率或置信区间仍不够稳定。",
    invalidDetail: "当前样本未显示稳定的正向预测能力。",
    periodCheck: "至少 24 个检验期",
    coverageCheck: "平均覆盖率至少 60%",
    bootstrapCheck: "IC 的 95% Bootstrap 下界大于 0",
    quantileChart: "平均分组收益",
    icChart: "Rank IC 时间序列",
    cumulativeChart: "累计多空收益",
    decayChart: "因子衰减",
    forwardPeriods: "后续期数",
    periodDetails: "每期明细与分组收益",
    warningTitle: "研究限制",
  } : {
    source: "Factor source",
    technical: "Technical",
    fundamental: "Fundamental",
    expression: "Expression",
    customName: "Custom factor name",
    factorName: "Factor name",
    customTitle: "Define a custom cross-sectional factor",
    customHint: "Combine registered technical and fundamental inputs, run a point-in-time test, then use the expression in a stock-selection strategy. Expressions never execute arbitrary Python.",
    baseFactor: "Stock cross-sectional factor",
    direction: "Factor direction",
    higher: "Higher is better",
    lower: "Lower is better",
    quantiles: "Factor quantiles",
    neutral: "Market-cap neutral",
    evaluating: "Evaluating",
    evaluate: "Run test",
    safeExpression: "Safe factor expression",
    expressionHint: "Inputs are registered technical/fundamental factors. Functions:",
    loading: "loading",
    meanRankIc: "Mean Rank IC",
    icPositive: "IC positive",
    coverage: "Coverage",
    topTurnover: "Top turnover",
    longShortAnnual: "Long-short annual",
    periods: "periods",
    universe: "securities",
    exportPeriods: "Export periods",
    date: "Date",
    observations: "Obs",
    longShort: "Long-short",
    empty: "Choose or define a stock cross-sectional factor, then run point-in-time cross-sectional diagnostics.",
    dataContext: "Data used by this cross-sectional test",
    demoProfile: "Demo data",
    runtimeProfile: "Local RQ data",
    dataThrough: "through",
    availableSymbols: "available securities",
    crossSectionNote: "Tests the full available universe from this source; it does not use the single symbol selected in Data Workbench.",
    advanced: "Advanced test settings",
    advancedHint: "Frequency, winsorization, and universe filters",
    frequency: "Test frequency",
    monthly: "Monthly",
    weekly: "Weekly",
    winsorize: "Two-sided winsorization (%)",
    minPrice: "Minimum price",
    minHistory: "Minimum history days",
    minAmount: "Minimum average amount",
    candidate: "Research candidate",
    watch: "Needs observation",
    invalid: "Do not use yet",
    candidateDetail: "Sample size, coverage, and bootstrap evidence meet the research-candidate gate; this is not trading authorization.",
    watchDetail: "Evidence is positive, but sample size, coverage, or the confidence interval remains unstable.",
    invalidDetail: "The current sample does not show stable positive predictive power.",
    periodCheck: "At least 24 test periods",
    coverageCheck: "Average coverage of at least 60%",
    bootstrapCheck: "95% bootstrap lower bound for IC is above 0",
    quantileChart: "Average quantile returns",
    icChart: "Rank IC over time",
    cumulativeChart: "Cumulative long-short return",
    decayChart: "Factor decay",
    forwardPeriods: "Forward periods",
    periodDetails: "Period details and quantile returns",
    warningTitle: "Research limitation",
  }
  const { startDate, endDate } = useGlobalFilter()
  const [profile] = useDataProfile()
  const [library, setLibrary] = useState<FactorResearchLibrary | null>(null)
  const [providerStatus, setProviderStatus] = useState<ProviderStatus | null>(null)
  const [source, setSource] = useState<"technical" | "fundamental" | "expression">(mode === "custom" ? "expression" : "technical")
  const [name, setName] = useState(mode === "custom" ? "custom_factor" : "momentum_20d")
  const [expression, setExpression] = useState("zscore(momentum_60d) - 0.5 * zscore(volatility_20d)")
  const [direction, setDirection] = useState<"long" | "short">("long")
  const [neutralizeMarketCap, setNeutralizeMarketCap] = useState(false)
  const [quantiles, setQuantiles] = useState(5)
  const [frequency, setFrequency] = useState<"monthly" | "weekly">("monthly")
  const [winsorizePercent, setWinsorizePercent] = useState(1)
  const [minPrice, setMinPrice] = useState(0)
  const [minHistoryDays, setMinHistoryDays] = useState(60)
  const [minAverageAmount, setMinAverageAmount] = useState(0)
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

  useEffect(() => {
    if (!requestedFactor || mode === "custom") return
    setSource(requestedFactor.source)
    setName(requestedFactor.name)
    setResult(null)
    setError("")
  }, [mode, requestedFactor])

  useEffect(() => {
    if (mode !== "custom") return
    setSource("expression")
    setName("custom_factor")
    setResult(null)
    setError("")
  }, [mode])

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

  const quantileChartData = useMemo(() => {
    if (!result) return []
    return Array.from({ length: result.quantiles }, (_, index) => ({
      bucket: `Q${index + 1}`,
      value: mean(result.rows.map((row) => row.quantile_returns[String(index + 1)])),
    }))
  }, [result])

  const icChartData = useMemo(
    () => result?.rows.map((row) => ({ date: row.date, value: row.ic })) ?? [],
    [result],
  )

  const cumulativeChartData = useMemo(() => {
    if (!result) return []
    let wealth = 1
    return result.rows.map((row) => {
      wealth *= 1 + row.long_short
      return { date: row.date, value: wealth - 1 }
    })
  }, [result])

  const decayChartData = useMemo(
    () => result
      ? Object.entries(result.decay)
          .sort(([left], [right]) => Number(left) - Number(right))
          .map(([horizon, value]) => ({ horizon, value: value.mean_ic, observations: value.observations }))
      : [],
    [result],
  )

  useEffect(() => {
    if (source === "expression") return
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
        winsorize: winsorizePercent / 100,
        neutralize: neutralizeMarketCap ? ["market_cap"] : [],
        start_date: monthStart(startDate || fallbackStart),
        end_date: monthEnd(endDate || fallbackEnd),
        frequency,
        quantiles,
        min_price: minPrice,
        min_history_days: minHistoryDays,
        min_average_amount: minAverageAmount,
      })
      setResult(payload)
    } catch (runError) {
      setError(runError instanceof Error ? runError.message : String(runError))
    } finally {
      setRunning(false)
    }
  }

  const activeProfile = providerStatus?.profiles[profile]
  const verdict = result ? researchVerdict(result) : null
  const verdictLabel = verdict === "candidate" ? copy.candidate : verdict === "watch" ? copy.watch : copy.invalid
  const verdictDetail = verdict === "candidate" ? copy.candidateDetail : verdict === "watch" ? copy.watchDetail : copy.invalidDetail
  const bootstrapLower = result?.summary.bootstrap_ic_95?.lower

  return (
    <div className="workbench-body factor-research-lab">
      <div className="factor-data-context">
        <Database aria-hidden="true" />
        <div>
          <strong>{copy.dataContext}: {profile === "demo" ? copy.demoProfile : copy.runtimeProfile}</strong>
          <span>{copy.crossSectionNote}</span>
        </div>
        <small>
          {copy.dataThrough} {activeProfile?.latest_date ?? "—"} · {(activeProfile?.symbol_count ?? 0).toLocaleString()} {copy.availableSymbols}
        </small>
      </div>

      {mode === "custom" && (
        <div className="factor-custom-context">
          <Sigma aria-hidden="true" />
          <div><strong>{copy.customTitle}</strong><span>{copy.customHint}</span></div>
        </div>
      )}

      <div className="workbench-controls factor-evaluation-controls">
        {mode === "builtin" && (
          <select aria-label={copy.source} value={source} onChange={(event) => setSource(event.target.value as typeof source)}>
            <option value="technical">{copy.technical}</option>
            <option value="fundamental">{copy.fundamental}</option>
          </select>
        )}
        {mode === "custom" ? (
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

      {mode === "custom" && (
        <div className="factor-expression-editor">
          <label htmlFor="factor-expression">{copy.safeExpression}</label>
          <textarea id="factor-expression" value={expression} onChange={(event) => setExpression(event.target.value)} spellCheck={false} />
          <small>{copy.expressionHint} {library?.expression_functions.join(", ") || copy.loading}.</small>
        </div>
      )}

      <details className="factor-advanced-settings">
        <summary>
          <span><strong>{copy.advanced}</strong><small>{copy.advancedHint}</small></span>
          <ChevronDown aria-hidden="true" />
        </summary>
        <div className="factor-advanced-grid">
          <label><span>{copy.frequency}</span><select value={frequency} onChange={(event) => setFrequency(event.target.value as typeof frequency)}><option value="monthly">{copy.monthly}</option><option value="weekly">{copy.weekly}</option></select></label>
          <label><span>{copy.winsorize}</span><input type="number" min={0} max={24.9} step={0.5} value={winsorizePercent} onChange={(event) => setWinsorizePercent(Math.max(0, Math.min(24.9, Number(event.target.value) || 0)))} /></label>
          <label><span>{copy.minPrice}</span><input type="number" min={0} step={0.1} value={minPrice} onChange={(event) => setMinPrice(Math.max(0, Number(event.target.value) || 0))} /></label>
          <label><span>{copy.minHistory}</span><input type="number" min={2} max={2000} value={minHistoryDays} onChange={(event) => setMinHistoryDays(Math.max(2, Math.min(2000, Number(event.target.value) || 60)))} /></label>
          <label><span>{copy.minAmount}</span><input type="number" min={0} step={1000000} value={minAverageAmount} onChange={(event) => setMinAverageAmount(Math.max(0, Number(event.target.value) || 0))} /></label>
        </div>
      </details>

      {error && <div className="workbench-message error">{error}</div>}
      {result && verdict && (
        <>
          <div className={`factor-verdict factor-verdict-${verdict}`}>
            <div>
              <span>{verdictLabel}</span>
              <strong>{result.factor.name}</strong>
              <p>{verdictDetail}</p>
            </div>
            <ul>
              <li data-passed={result.periods >= 24}>{copy.periodCheck}<strong>{result.periods}</strong></li>
              <li data-passed={(result.summary.coverage_mean ?? 0) >= 0.6}>{copy.coverageCheck}<strong>{percent(result.summary.coverage_mean)}</strong></li>
              <li data-passed={bootstrapLower != null && bootstrapLower > 0}>{copy.bootstrapCheck}<strong>{number(bootstrapLower)}</strong></li>
            </ul>
          </div>

          <div className="analytics-kpi-grid workbench-kpis">
            <div className="analytics-kpi"><span>{copy.meanRankIc}</span><strong>{number(result.summary.ic_mean)}</strong></div>
            <div className="analytics-kpi"><span>ICIR</span><strong>{number(result.summary.icir)}</strong></div>
            <div className="analytics-kpi"><span>Newey-West t</span><strong>{number(result.summary.ic_t_stat)}</strong></div>
            <div className="analytics-kpi"><span>{copy.icPositive}</span><strong>{percent(result.summary.ic_positive_ratio)}</strong></div>
            <div className="analytics-kpi"><span>{copy.coverage}</span><strong>{percent(result.summary.coverage_mean)}</strong></div>
            <div className="analytics-kpi"><span>{copy.topTurnover}</span><strong>{percent(result.summary.top_turnover_mean)}</strong></div>
            <div className="analytics-kpi"><span>{copy.longShortAnnual}</span><strong>{percent(result.summary.long_short?.annual_return)}</strong></div>
          </div>

          <div className="factor-evidence-grid">
            <section className="factor-evidence-card">
              <h3>{copy.quantileChart}</h3>
              <ResponsiveContainer width="100%" height={220}>
                <BarChart data={quantileChartData}><CartesianGrid strokeDasharray="3 3" vertical={false} /><XAxis dataKey="bucket" /><YAxis tickFormatter={(value) => `${(Number(value) * 100).toFixed(1)}%`} /><Tooltip /><ReferenceLine y={0} stroke="currentColor" /><Bar dataKey="value" fill={CHART_COLORS[0]} /></BarChart>
              </ResponsiveContainer>
            </section>
            <section className="factor-evidence-card">
              <h3>{copy.icChart}</h3>
              <ResponsiveContainer width="100%" height={220}>
                <LineChart data={icChartData}><CartesianGrid strokeDasharray="3 3" vertical={false} /><XAxis dataKey="date" minTickGap={28} /><YAxis /><Tooltip /><ReferenceLine y={0} stroke="currentColor" /><Line type="monotone" dataKey="value" stroke={CHART_COLORS[1]} dot={false} /></LineChart>
              </ResponsiveContainer>
            </section>
            <section className="factor-evidence-card">
              <h3>{copy.cumulativeChart}</h3>
              <ResponsiveContainer width="100%" height={220}>
                <LineChart data={cumulativeChartData}><CartesianGrid strokeDasharray="3 3" vertical={false} /><XAxis dataKey="date" minTickGap={28} /><YAxis tickFormatter={(value) => `${(Number(value) * 100).toFixed(0)}%`} /><Tooltip /><ReferenceLine y={0} stroke="currentColor" /><Line type="monotone" dataKey="value" stroke={CHART_COLORS[2]} dot={false} /></LineChart>
              </ResponsiveContainer>
            </section>
            <section className="factor-evidence-card">
              <h3>{copy.decayChart}</h3>
              <ResponsiveContainer width="100%" height={220}>
                <BarChart data={decayChartData}><CartesianGrid strokeDasharray="3 3" vertical={false} /><XAxis dataKey="horizon" label={{ value: copy.forwardPeriods, position: "insideBottom", offset: -2 }} /><YAxis /><Tooltip /><ReferenceLine y={0} stroke="currentColor" /><Bar dataKey="value" fill={CHART_COLORS[3]} /></BarChart>
              </ResponsiveContainer>
            </section>
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
            <div className="workbench-message research-message factor-warning" key={warning}><AlertTriangle aria-hidden="true" /><span><strong>{copy.warningTitle}</strong>{warning}</span></div>
          ))}
          <details className="factor-period-details">
            <summary>{copy.periodDetails}<ChevronDown aria-hidden="true" /></summary>
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
          </details>
        </>
      )}
      {!result && !error && <div className="analytics-empty"><FlaskConical aria-hidden="true" /> {copy.empty}</div>}
    </div>
  )
}
