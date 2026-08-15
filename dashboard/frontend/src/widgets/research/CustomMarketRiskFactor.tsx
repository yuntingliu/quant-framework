import { useMemo, useState } from "react"
import { AlertTriangle, Play, Sigma } from "lucide-react"

import { CumulativeReturnsChart } from "@/components/charts"
import { useGlobalFilter } from "@/contexts/GlobalFilterContext"
import { useLanguage } from "@/contexts/LanguageContext"
import { api, type CustomRiskFactorResult } from "@/lib/api"
import type { DataProfile } from "@/lib/data-profile"

interface CustomMarketRiskFactorProps {
  profile: DataProfile
}

function monthStart(value: string): string {
  return `${value}-01`
}

function monthEnd(value: string): string {
  const [year, month] = value.split("-").map(Number)
  return new Date(Date.UTC(year, month, 0)).toISOString().slice(0, 10)
}

function percent(value: number | null): string {
  return value == null ? "—" : `${(value * 100).toFixed(2)}%`
}

function number(value: number | null): string {
  return value == null ? "—" : value.toFixed(3)
}

export function CustomMarketRiskFactor({ profile }: CustomMarketRiskFactorProps) {
  const { language } = useLanguage()
  const { startDate, endDate } = useGlobalFilter()
  const [name, setName] = useState("market_style_blend")
  const [expression, setExpression] = useState("0.75 * MKT + 0.25 * SMB - rf")
  const [result, setResult] = useState<CustomRiskFactorResult | null>(null)
  const [error, setError] = useState("")
  const [running, setRunning] = useState(false)
  const copy = language === "zh"
    ? {
        title: "自定义市场风险因子",
        hint: "用系统风险因子收益构造新的描述性收益序列。这里只允许线性组合，不把它当作选股因子或择时信号。",
        name: "风险因子名称",
        expression: "收益表达式",
        expressionHint: "可用输入：MKT、SMB、HML、MOM、RMW、rf；可用运算：+、-、乘以常数、除以非零常数。",
        run: "运行分析",
        running: "分析中",
        observations: "观测期数",
        annualReturn: "年化收益",
        annualVolatility: "年化波动",
        sharpe: "Sharpe",
        maxDrawdown: "最大回撤",
        positiveRatio: "正收益占比",
        cumulative: "自定义风险因子累计收益",
        boundary: "这是研究用的派生收益序列；除非另行定义可交易的多空组合，否则不能直接下单。",
      }
    : {
        title: "Custom market risk factor",
        hint: "Build a descriptive return series from registered risk-factor returns. Only linear combinations are allowed; this is neither a stock-ranking factor nor a timing signal.",
        name: "Risk factor name",
        expression: "Return expression",
        expressionHint: "Inputs: MKT, SMB, HML, MOM, RMW, rf. Operators: +, -, scalar multiplication, and division by a non-zero scalar.",
        run: "Run analysis",
        running: "Analyzing",
        observations: "Observations",
        annualReturn: "Annual return",
        annualVolatility: "Annual volatility",
        sharpe: "Sharpe",
        maxDrawdown: "Max drawdown",
        positiveRatio: "Positive ratio",
        cumulative: "Custom risk factor cumulative return",
        boundary: "This is a derived research return series. It cannot place orders unless a separately defined tradable long-short portfolio implements it.",
      }

  const chartData = useMemo(
    () => result?.dates.map((date, index) => ({ date, value: result.cumulative[index] ?? 0 })) ?? [],
    [result],
  )

  async function evaluate() {
    setRunning(true)
    setError("")
    try {
      const payload = await api.post<CustomRiskFactorResult>("/market/custom-risk-factor/evaluate", {
        profile,
        name: name.trim(),
        expression: expression.trim(),
        ...(startDate ? { start_date: monthStart(startDate) } : {}),
        ...(endDate ? { end_date: monthEnd(endDate) } : {}),
      })
      setResult(payload)
    } catch (runError) {
      setResult(null)
      setError(runError instanceof Error ? runError.message : String(runError))
    } finally {
      setRunning(false)
    }
  }

  return (
    <div className="workbench-body factor-research-lab">
      <div className="factor-custom-context">
        <Sigma aria-hidden="true" />
        <div><strong>{copy.title}</strong><span>{copy.hint}</span></div>
      </div>
      <div className="workbench-controls factor-risk-expression-controls">
        <input aria-label={copy.name} value={name} onChange={(event) => setName(event.target.value)} placeholder={copy.name} />
        <button className="primary-command" type="button" onClick={evaluate} disabled={running || !name.trim() || !expression.trim()}>
          <Play aria-hidden="true" /> {running ? copy.running : copy.run}
        </button>
      </div>
      <div className="factor-expression-editor">
        <label htmlFor="market-risk-expression">{copy.expression}</label>
        <textarea id="market-risk-expression" value={expression} onChange={(event) => setExpression(event.target.value)} spellCheck={false} />
        <small>{copy.expressionHint}</small>
      </div>
      <div className="workbench-message research-message"><AlertTriangle aria-hidden="true" /><span>{copy.boundary}</span></div>
      {error && <div className="workbench-message error">{error}</div>}
      {result && (
        <>
          <div className="analytics-kpi-grid workbench-kpis">
            <div className="analytics-kpi"><span>{copy.observations}</span><strong>{result.summary.observations}</strong></div>
            <div className="analytics-kpi"><span>{copy.annualReturn}</span><strong>{percent(result.summary.annual_return)}</strong></div>
            <div className="analytics-kpi"><span>{copy.annualVolatility}</span><strong>{percent(result.summary.annual_volatility)}</strong></div>
            <div className="analytics-kpi"><span>{copy.sharpe}</span><strong>{number(result.summary.sharpe)}</strong></div>
            <div className="analytics-kpi"><span>{copy.maxDrawdown}</span><strong>{percent(result.summary.max_drawdown)}</strong></div>
            <div className="analytics-kpi"><span>{copy.positiveRatio}</span><strong>{percent(result.summary.positive_ratio)}</strong></div>
          </div>
          <section className="factor-evidence-card">
            <h3>{copy.cumulative}: {result.name}</h3>
            <CumulativeReturnsChart data={chartData} series={[{ key: "value", name: result.name }]} height={300} yAxisFormat="percent" />
          </section>
        </>
      )}
    </div>
  )
}
