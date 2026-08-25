import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import {
  AlertTriangle,
  BookOpen,
  Check,
  Code2,
  ExternalLink,
  FlaskConical,
  Loader2,
  Play,
  Plus,
  Save,
  Trash2,
} from "lucide-react"

import { DistributionChart, HorizontalBarChart, RollingLineChart } from "@/components/charts"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { useFactorLab } from "@/contexts/FactorLabContext"
import { formatNumber, formatPercent } from "@/lib/utils"
import { Widget } from "@/widgets/Widget"

function sourceLabel(source: string): string {
  return source === "technical" ? "技术" : source === "fundamental" ? "基本面" : "自定义"
}

function factorDescription(name: string, fallback: string): string {
  const descriptions: Record<string, string> = {
    momentum_20d: "最近 20 个交易日价格动量",
    momentum_60d: "最近 60 个交易日价格动量",
    reversal_5d: "最近 5 个交易日短期反转",
    volatility_20d: "最近 20 日年化波动率",
    turnover_20d: "最近 20 日平均成交量",
    volume_ratio: "近 5 日与 20 日成交量之比",
    rsi_14: "14 日相对强弱指标",
    ma_deviation: "收盘价相对 20 日均线偏离",
    ep: "盈利收益率",
    bp: "账面市值比",
    roe: "净资产收益率",
    roa: "总资产收益率",
    profit_growth: "利润增长率",
    revenue_growth: "收入增长率",
    gross_margin: "毛利率",
    leverage: "财务杠杆",
  }
  return descriptions[name] ?? fallback
}

interface FactorWidgetProps {
  embedded?: boolean
}

function FactorPanel({ children, className = "", embedded = false }: { children: React.ReactNode; className?: string; embedded?: boolean }) {
  const content = <div className={`factor-lab-panel ${className}`}>{children}</div>
  return embedded ? content : <Widget headerless>{content}</Widget>
}

export function FactorLibraryWidget({ embedded = false }: FactorWidgetProps) {
  const lab = useFactorLab()
  const [query, setQuery] = useState("")
  const [source, setSource] = useState<"all" | "technical" | "fundamental" | "expression">("all")
  const [libraryTab, setLibraryTab] = useState<"catalog" | "project">("catalog")
  const factors = useMemo(() => {
    const needle = query.trim().toLowerCase()
    return (lab.library?.factors ?? []).filter((factor) => (
      (source === "all" || factor.source === source)
      && (!needle || factor.name.toLowerCase().includes(needle) || factor.description.toLowerCase().includes(needle))
    ))
  }, [lab.library?.factors, query, source])

  return (
    <FactorPanel className="factor-library-panel" embedded={embedded}>
      <div className="factor-panel-header">
        <div><BookOpen size={15} /><span><strong>因子库</strong><small>{lab.project?.name ?? "未选择研究项目"}</small></span></div>
        <button className="secondary-command" type="button" onClick={lab.createExpressionFactor}><Plus size={13} />表达式因子</button>
      </div>
      <div className="factor-library-controls">
        <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索因子" aria-label="搜索因子" />
        <div>
          {(["all", "technical", "fundamental", "expression"] as const).map((item) => (
            <button className={source === item ? "active" : ""} type="button" key={item} onClick={() => setSource(item)}>
              {item === "all" ? "全部" : sourceLabel(item)}
            </button>
          ))}
        </div>
      </div>

      <Tabs className="factor-library-tabs" value={libraryTab} onValueChange={(value) => setLibraryTab(value as "catalog" | "project")}>
        <div className="factor-library-tabbar">
          <TabsList>
            <TabsTrigger value="catalog">可用因子</TabsTrigger>
            <TabsTrigger value="project">项目已用 <span>{lab.projectFactors.length}</span></TabsTrigger>
          </TabsList>
        </div>

        <TabsContent className="factor-panel-scroll factor-library-tab-content" value="catalog">
          <section className="factor-library-section">
            <div className="factor-section-heading"><span>可用因子</span><strong>{factors.length}</strong></div>
            {lab.libraryLoading ? <div className="factor-empty"><Loader2 className="spin" size={14} />正在读取因子库…</div> : null}
            <div className="factor-catalog-list">
              {factors.map((factor) => {
                const active = lab.draft.name === factor.name && lab.draft.source === factor.source && !lab.editingOriginalName
                const adopted = lab.projectFactors.some((item) => item.name === factor.name)
                const canAdopt = Boolean(lab.project?.editable && !adopted && !lab.saving)
                return (
                  <div className={`factor-catalog-row${active ? " active" : ""}`} key={`${factor.source}-${factor.name}`}>
                    <button className="factor-catalog-select" type="button" onClick={() => lab.selectLibraryFactor(factor)}>
                      <span><strong>{factor.name}</strong><small>{factorDescription(factor.name, factor.description)}</small></span>
                      <em>{sourceLabel(factor.source)}</em>
                    </button>
                    <button className="factor-catalog-insert" type="button" title={`把 ${factor.name} 加入表达式`} onClick={() => lab.requestExpressionInsert(factor.source === "expression" ? `(${factor.expression ?? ""})` : factor.name)}>
                      <Plus size={11} />表达式
                    </button>
                    <button
                      className={`factor-catalog-add${adopted ? " adopted" : ""}`}
                      type="button"
                      title={adopted ? "当前项目已使用这个因子" : lab.project?.editable ? `把 ${factor.name} 加入当前项目` : "请先选择可编辑的研究项目"}
                      disabled={!canAdopt}
                      onClick={() => void lab.addLibraryFactorToProject(factor)}
                    >
                      {adopted ? <Check size={11} /> : <Plus size={11} />}{adopted ? "已加入" : "加入项目"}
                    </button>
                  </div>
                )
              })}
            </div>
          </section>

          <section className="factor-library-section">
            <div className="factor-section-heading"><span>开源特征包</span><strong>{lab.library?.packs.length ?? 0}</strong></div>
            <div className="factor-pack-list">
              {(lab.library?.packs ?? []).map((pack) => (
                <article key={pack.id}>
                  <div><strong>{pack.name}</strong><span>{pack.feature_count} 维 · {pack.license}</span></div>
                  <p>{pack.description}</p>
                  <footer><em>需要数据适配</em><a href={pack.source_url} target="_blank" rel="noreferrer">源码 <ExternalLink size={11} /></a></footer>
                </article>
              ))}
            </div>
          </section>
        </TabsContent>

        <TabsContent className="factor-panel-scroll factor-library-tab-content" value="project">
          <section className="factor-library-section">
          <div className="factor-section-heading"><span>项目因子篮子</span><strong>{lab.projectFactors.length}</strong></div>
          {!lab.project ? <div className="factor-empty">请先在“研究项目”工作区选择研究项目。</div> : null}
          {lab.project && !lab.projectFactors.length ? <div className="factor-empty">当前项目还没有因子。可从“可用因子”直接加入，或新建并保存表达式因子。</div> : null}
          <div className="factor-project-list">
            {lab.projectFactors.map((factor) => (
              <div key={factor.name} className={lab.editingOriginalName === factor.name ? "active" : ""}>
                <button type="button" onClick={() => lab.selectProjectFactor(factor)}>
                  <span><strong>{factor.name}</strong><small>{sourceLabel(factor.source)} · {factor.direction === "long" ? "值越大越优" : "值越小越优"}</small></span>
                  <i>{factor.direction === "long" ? "正向" : "反向"}</i>
                </button>
                {lab.project?.editable ? (
                  <button type="button" className="factor-remove-button" title="从项目移除" onClick={() => { if (window.confirm(`从当前项目移除因子 ${factor.name}？`)) void lab.removeFromProject(factor.name) }}><Trash2 size={12} /></button>
                ) : null}
              </div>
            ))}
          </div>
          </section>
        </TabsContent>
      </Tabs>
    </FactorPanel>
  )
}

export function FactorEditorWidget({ embedded = false }: FactorWidgetProps) {
  const lab = useFactorLab()
  const functions = lab.library?.expression_functions ?? []
  const draftComplete = Boolean(lab.draft.name.trim() && (lab.draft.source !== "expression" || lab.draft.expression.trim()))
  const savesToLibrary = lab.draft.source === "expression" && !lab.editingOriginalName
  const availableToSave = Boolean(draftComplete && (savesToLibrary || lab.project?.editable))
  const expressionRef = useRef<HTMLTextAreaElement>(null)
  const handledInsertId = useRef(0)
  const draftExpression = lab.draft.expression
  const updateDraft = lab.updateDraft

  const insertExpressionTerm = useCallback((term: string, cursorInTerm = term.length) => {
    const textarea = expressionRef.current
    const current = draftExpression
    const start = textarea?.selectionStart ?? current.length
    const end = textarea?.selectionEnd ?? start
    const prefix = start === end && start === current.length && current.trim() ? " + " : ""
    const next = `${current.slice(0, start)}${prefix}${term}${current.slice(end)}`
    const cursor = start + prefix.length + cursorInTerm
    updateDraft({ expression: next })
    window.requestAnimationFrame(() => {
      expressionRef.current?.focus()
      expressionRef.current?.setSelectionRange(cursor, cursor)
    })
  }, [draftExpression, updateDraft])

  const insertFunction = useCallback((name: string) => {
    const textarea = expressionRef.current
    const current = draftExpression
    const start = textarea?.selectionStart ?? current.length
    const end = textarea?.selectionEnd ?? start
    const selected = current.slice(start, end)
    const term = `${name}(${selected})`
    insertExpressionTerm(term, selected ? term.length : name.length + 1)
  }, [draftExpression, insertExpressionTerm])

  useEffect(() => {
    const request = lab.expressionInsertRequest
    if (!request || request.id === handledInsertId.current) return
    handledInsertId.current = request.id
    insertExpressionTerm(request.token)
  }, [insertExpressionTerm, lab.expressionInsertRequest])

  return (
    <FactorPanel className="factor-editor-panel" embedded={embedded}>
      <div className="factor-panel-header">
        <div><Code2 size={15} /><span><strong>因子定义</strong><small>{sourceLabel(lab.draft.source)}因子</small></span></div>
        <div className="factor-editor-actions">
          {!embedded ? <button className="secondary-command" type="button" onClick={() => void lab.evaluate()} disabled={lab.running}>
            {lab.running ? <Loader2 className="spin" size={13} /> : <Play size={13} />}{lab.running ? "评估中" : "运行评估"}
          </button> : null}
          <button className="primary-command" type="button" onClick={() => void (savesToLibrary ? lab.saveCustomFactor() : lab.saveToProject())} disabled={lab.saving || !availableToSave} title={availableToSave ? savesToLibrary ? "保存到可用因子库" : "保存到当前项目" : "请填写完整因子定义；项目因子还需要选择可编辑项目"}>
            {lab.saving ? <Loader2 className="spin" size={13} /> : <Save size={13} />}{lab.editingOriginalName ? "保存项目修改" : savesToLibrary ? "保存到因子库" : "加入项目"}
          </button>
        </div>
      </div>
      <div className="factor-panel-scroll factor-editor-body">
        {!embedded && lab.error ? <div className="workbench-message error">{lab.error}</div> : null}
        {!lab.project?.editable && lab.project ? <div className="factor-readonly-note"><AlertTriangle size={13} />当前项目只读；可以运行研究，保存前请在“研究项目”工作区复制项目。</div> : null}

        <div className="factor-form-grid">
          <label><span>因子名称</span><input value={lab.draft.name} readOnly={lab.draft.source !== "expression"} onChange={(event) => lab.updateDraft({ name: event.target.value })} /></label>
          <label><span>来源</span><input value={sourceLabel(lab.draft.source)} readOnly /></label>
          <label><span>方向</span><select value={lab.draft.direction} onChange={(event) => lab.updateDraft({ direction: event.target.value as "long" | "short" })}><option value="long">值越大越优</option><option value="short">值越小越优</option></select></label>
          <label><span>双侧缩尾</span><input type="number" min="0" max="0.24" step="0.01" value={lab.draft.winsorize} onChange={(event) => lab.updateDraft({ winsorize: Number(event.target.value) })} /></label>
          <label className="factor-checkbox"><span>中性化</span><button type="button" className={lab.draft.neutralize.includes("market_cap") ? "active" : ""} onClick={() => lab.updateDraft({ neutralize: lab.draft.neutralize.includes("market_cap") ? [] : ["market_cap"] })}>{lab.draft.neutralize.includes("market_cap") ? <Check size={12} /> : null}市值</button></label>
        </div>

        {lab.draft.source === "expression" ? (
          <section className="factor-expression-workspace">
            <div><strong>安全向量表达式</strong><span>从左侧或上方观察区把因子插入当前光标位置</span></div>
            <textarea ref={expressionRef} spellCheck={false} value={lab.draft.expression} onChange={(event) => lab.updateDraft({ expression: event.target.value })} />
            <div className="factor-function-list">{functions.map((name) => <button type="button" key={name} onClick={() => insertFunction(name)}>{name}()</button>)}</div>
          </section>
        ) : null}
      </div>
    </FactorPanel>
  )
}

export function FactorValidationSettings() {
  const lab = useFactorLab()
  return (
    <section className="factor-validation-settings">
      <label><span>开始日期</span><input type="date" value={lab.draft.startDate} onChange={(event) => lab.updateDraft({ startDate: event.target.value })} /></label>
      <label><span>结束日期</span><input type="date" value={lab.draft.endDate} onChange={(event) => lab.updateDraft({ endDate: event.target.value })} /></label>
      <label><span>频率</span><select value={lab.draft.frequency} onChange={(event) => lab.updateDraft({ frequency: event.target.value as "monthly" | "weekly" })}><option value="monthly">月频</option><option value="weekly">周频</option></select></label>
      <label><span>分组</span><select value={lab.draft.quantiles} onChange={(event) => lab.updateDraft({ quantiles: Number(event.target.value) })}>{[3, 5, 10].map((value) => <option value={value} key={value}>{value} 组</option>)}</select></label>
    </section>
  )
}

export function FactorSnapshotWidget({ embedded = false }: FactorWidgetProps) {
  const lab = useFactorLab()
  const [side, setSide] = useState<"top" | "bottom">("top")
  const snapshot = lab.result?.snapshot
  const rows = side === "top" ? snapshot?.top ?? [] : snapshot?.bottom ?? []
  return (
    <FactorPanel className="factor-snapshot-panel" embedded={embedded}>
      <div className="factor-panel-header">
        <div><FlaskConical size={15} /><span><strong>最近验证截面</strong><small>{snapshot?.date ?? "等待评估"}</small></span></div>
        {lab.resultStale ? <em className="factor-stale-badge">定义已变化</em> : null}
      </div>
      <div className="factor-panel-scroll">
        {!lab.result ? <div className="factor-empty large">在“因子定义”中运行评估后，这里显示最后一个有下一期收益的历史截面。</div> : null}
        {lab.result && !snapshot ? <div className="factor-empty large">当前范围没有形成有效截面，请检查数据、日期和研究范围。</div> : null}
        {snapshot ? (
          <>
            <div className="factor-kpi-grid">
              <div><span>有效证券</span><strong>{snapshot.observations}</strong></div>
              <div><span>覆盖率</span><strong>{formatPercent(snapshot.coverage, 0)}</strong></div>
              <div><span>均值</span><strong>{formatNumber(snapshot.mean, 3)}</strong></div>
              <div><span>标准差</span><strong>{formatNumber(snapshot.std, 3)}</strong></div>
            </div>
            <div className="factor-chart-frame"><DistributionChart data={snapshot.values} bins={Math.min(20, Math.max(5, Math.ceil(Math.sqrt(snapshot.values.length))))} height={210} /></div>
            <div className="factor-range-row"><span>最小 {formatNumber(snapshot.minimum, 3)}</span><span>中位 {formatNumber(snapshot.median, 3)}</span><span>最大 {formatNumber(snapshot.maximum, 3)}</span></div>
            <div className="factor-table-tabs"><button className={side === "top" ? "active" : ""} type="button" onClick={() => setSide("top")}>高分组</button><button className={side === "bottom" ? "active" : ""} type="button" onClick={() => setSide("bottom")}>低分组</button></div>
            <div className="stage-table-wrap compact"><table className="stage-table"><thead><tr><th>#</th><th>证券</th><th>因子值</th><th>下一期收益</th></tr></thead><tbody>{rows.map((row, index) => <tr key={row.symbol}><td>{index + 1}</td><td><strong>{row.symbol}</strong></td><td>{formatNumber(row.value, 4)}</td><td className={row.forward_return < 0 ? "negative" : ""}>{formatPercent(row.forward_return, 2)}</td></tr>)}</tbody></table></div>
            <div className="factor-footnote">这是历史验证截面，下一期收益只用于研究评估，不会进入实时因子计算。</div>
          </>
        ) : null}
      </div>
    </FactorPanel>
  )
}

type EvidenceTab = "ic" | "quantiles" | "decay"

function warningText(value: string): string {
  if (value.includes("Fewer than 24")) return "有效评估期少于 24 期，统计推断不稳定"
  if (value.includes("coverage is below")) return "平均因子覆盖率低于 60%"
  if (value.includes("bootstrap interval")) return "平均 IC 的 95% Bootstrap 区间未严格大于零"
  if (value.includes("PIT instrument snapshots")) return "缺少证券主数据历史快照，研究范围来自行情历史"
  if (value.includes("post-dates")) return "至少一个证券主数据快照晚于因子观测日期"
  return value
}

export function FactorEvidenceWidget({ embedded = false }: FactorWidgetProps) {
  const lab = useFactorLab()
  const [tab, setTab] = useState<EvidenceTab>("ic")
  const summary = lab.result?.summary
  const longShort = summary?.long_short ?? {}
  const quantileData = useMemo(() => {
    const rows = lab.result?.rows ?? []
    const groups = new Map<string, number[]>()
    rows.forEach((row) => Object.entries(row.quantile_returns).forEach(([key, value]) => {
      groups.set(key, [...(groups.get(key) ?? []), value])
    }))
    return [...groups.entries()]
      .sort((left, right) => Number(left[0]) - Number(right[0]))
      .map(([name, values]) => ({ name: `Q${name}`, value: values.reduce((sum, value) => sum + value, 0) / values.length }))
  }, [lab.result?.rows])
  const decayData = useMemo(() => Object.entries(lab.result?.decay ?? {}).map(([name, value]) => ({ name: `${name} 期`, value: value.mean_ic ?? 0 })), [lab.result?.decay])
  const confidence = summary?.bootstrap_ic_95

  return (
    <FactorPanel className="factor-evidence-panel" embedded={embedded}>
      <div className="factor-panel-header">
        <div><FlaskConical size={15} /><span><strong>单因子历史证据</strong><small>{lab.result ? `${lab.result.periods} 个截面` : "等待评估"}</small></span></div>
        <div className="factor-evidence-tabs">{(["ic", "quantiles", "decay"] as const).map((item) => <button className={tab === item ? "active" : ""} type="button" key={item} onClick={() => setTab(item)}>{item === "ic" ? "IC" : item === "quantiles" ? "分组" : "衰减"}</button>)}</div>
      </div>
      <div className="factor-panel-scroll">
        {!lab.result ? <div className="factor-empty large">运行因子评估后，这里显示 IC、分组单调性、衰减和换手证据。</div> : (
          <>
            <div className="factor-kpi-grid evidence">
              <div><span>IC 均值</span><strong>{summary?.ic_mean == null ? "—" : formatNumber(summary.ic_mean, 3)}</strong></div>
              <div><span>年化 ICIR</span><strong>{summary?.icir == null ? "—" : formatNumber(summary.icir, 2)}</strong></div>
              <div><span>Newey-West t</span><strong>{summary?.ic_t_stat == null ? "—" : formatNumber(summary.ic_t_stat, 2)}</strong></div>
              <div><span>正 IC 占比</span><strong>{summary?.ic_positive_ratio == null ? "—" : formatPercent(summary.ic_positive_ratio, 0)}</strong></div>
              <div><span>Top 换手</span><strong>{summary?.top_turnover_mean == null ? "—" : formatPercent(summary.top_turnover_mean, 0)}</strong></div>
              <div><span>多空年化</span><strong>{typeof longShort.annual_return === "number" ? formatPercent(longShort.annual_return, 1) : "—"}</strong></div>
            </div>
            {confidence ? <div className="factor-confidence"><span>平均 IC 的 95% Bootstrap 区间</span><strong>{confidence.lower == null ? "—" : formatNumber(confidence.lower, 3)} ～ {confidence.upper == null ? "—" : formatNumber(confidence.upper, 3)}</strong></div> : null}
            <div className="factor-chart-frame evidence">
              {tab === "ic" ? <RollingLineChart data={lab.result.rows.map((row) => ({ date: row.date, ic: row.ic }))} series={[{ key: "ic", name: "Rank IC", color: "#2962ff" }]} referenceLine={0} height={250} /> : null}
              {tab === "quantiles" ? <HorizontalBarChart data={quantileData} height={Math.max(220, quantileData.length * 34)} format="percent" /> : null}
              {tab === "decay" ? <HorizontalBarChart data={decayData} height={220} format="number" /> : null}
            </div>
            {lab.result.warnings.length ? <div className="factor-warning-list">{lab.result.warnings.map((warning) => <div key={warning}><AlertTriangle size={13} /><span>{warningText(warning)}</span></div>)}</div> : <div className="factor-pass-note"><Check size={13} />当前评估没有触发数据覆盖或统计稳定性警告。</div>}
          </>
        )}
      </div>
    </FactorPanel>
  )
}
