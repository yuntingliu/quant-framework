/** Domain copy shared by option views, including labels retained in basket state. */
export const optionStrings: Record<string, string> = {
  "有效收益次数（基准 / 压力）": "Payoff draws (base / stress)",
  "收益有效样本量（基准 / 压力）": "Payoff ESS (base / stress)",
  "使用匹配的校准扩散波动率与跳跃参数；结果是 Q 测度条件估值，抽样区间不包含模型误差。": "Uses matching calibrated diffusion volatility and jump parameters; conditional Q values and sampling intervals exclude model error.",
  "部分期限可用；定价时检查相邻期限": "Some maturities usable; pricing checks adjacent tenors",
  "远期平值 · 总方差插值": "Forward ATM \u00b7 total-variance interpolation",
  "曲面模型": "Surface model",
  "SSVI · 静态约束": "SSVI \u00b7 static constraints",
  "SVI · 诊断拟合": "SVI \u00b7 diagnostic fit",
  "SSVI 约束拟合": "Constrained SSVI fit",
  "正在更新合约名称…": "Updating dated contract names\u2026",
  "所选日期合约不可用": "Contract unavailable on the selected date",
  "抽样方法": "Sampling method",
  "标准 Monte Carlo": "Standard Monte Carlo",
  "混合重要性抽样": "Defensive mixture importance sampling",
  "尾部倾斜强度": "Tail tilt",
  "使用匹配的单 ETF 校准版本": "Use matching single-ETF calibration",
  "有效收益次数": "Nonzero payoff draws",
  "收益有效样本量": "Payoff effective sample size",
  "抽样不足": "Insufficient sampling",
  "稀有收益样本不足，未报告": "Insufficient rare-payoff samples; withheld",

  "合约名称 / 代码": "Contract name / code",
  "ETF 复权行情会统一刷新已保存历史，避免分红拼接跳变；历史指标只重算输入已变化的日期。": "Adjusted ETF prices refresh retained history consistently to avoid distribution splice jumps; historical features rebuild only when inputs change.",
  "数据任务执行中：下载、增量入库和质量校验；完成后显示结果。": "Data task running: acquisition, incremental writes and quality checks; results appear on completion.", "最近数据任务：": "Latest data task: ", "指标缺失单元格": "Missing indicator cells", "计算失败记录": "Failed calculations",
  "ETF 期权": "ETF options", "五分钟ETF行情": "ETF five-minute prices", "五分钟已实现方差": "Five-minute realized variance",
  "期权链": "Option chain", "波动率": "Volatility", "组合压力": "Portfolio stress", "数据质量": "Data quality", "文献与预测": "Literature & prediction",
  "可分析": "Eligible", "价格边界异常": "Invalid price bounds", "无成交/持仓不足": "No trade / low open interest", "远期配对不足": "Forward unavailable", "到期日": "Expiry date", "零价格": "Zero price",
  "期权合约日快照": "Daily option snapshots", "国债收益率曲线": "Government yield curve", "ETF 复权历史": "Adjusted ETF history", "历史期权因子": "Historical option features", "样本外预测": "Out-of-sample forecasts",
  "已校验记录": "Validated rows", "交易日": "Sessions", "零成交参考价": "Zero-volume references", "无成交收盘/区间差异": "Non-trading close/range differences", "无成交开盘/区间差异": "Non-trading open/range differences", "日内证据核验的异常开盘价": "Anomalous opens with intraday HLCV evidence", "调整后合约": "Adjusted contracts", "日内证据恢复收盘价": "Closes recovered from intraday evidence", "最早样本": "First observation", "最新样本": "Last observation",
  "组合最多支持 20 个合约": "A portfolio supports at most 20 contracts", "购": "Call", "沽": "Put",
  "同步 ETF 期权数据": "Synchronize ETF option data", "将执行可查看的内置数据配方，同步": "Run the visible built-in recipe for ", "个 ETF，": " ETFs, ", "至": " to ", "；已有完整日期会增量复用。": "; complete saved dates are reused incrementally.", "开始同步": "Start sync",
  "ETF 期权研究": "ETF option research", "日线 · 真实数据": "Daily · market data", "期权链、隐含远期、波动率与组合全量重估": "Chains, implied forwards, volatility and full portfolio revaluation",
  "刷新": "Refresh", "历史同步": "History sync", "标的 ETF": "Underlying ETF", "分析日期": "Analysis date", "价格口径": "Price reference", "收盘价": "Close", "结算价": "Settlement", "贴现曲线": "Discount curve", "同期中债国债曲线": "Dated China government curve", "手动年化利率": "Manual annual rate", "利率 %": "Rate %",
  "个已同步交易日": " synchronized sessions", "条合约日记录": " contract-day observations", "开始日期": "Start date", "结束日期": "End date", "全部 9 个 ETF 标的": "All 9 ETF underlyings", "预览配方": "Preview recipe", "开始增量同步": "Start incremental sync",
  "分段批量获取、逐日历史合约发现；同步真实利率与 ETF 复权价格。失败后可重跑续传，最近三个交易日重拉修订。": "Batched acquisition with daily historical contract discovery, market rates and adjusted ETF prices. Resume failed jobs; refresh the last three sessions for revisions.",
  "查看将执行的完整 Python 配方（": "View the complete Python recipe (", "类数据）": " datasets)", "历史同步正在运行，进度": "History sync running, progress ", "取消任务": "Cancel job", "最近同步：": "Latest sync: ", "完成": "completed", "失败，可重跑续传": "failed; rerun to resume", "已取消": "cancelled", "已中断，可重跑续传": "interrupted; rerun to resume", "· 进度": "· progress ",
  "正在计算期权链与波动率…": "Computing option chain and volatility…", "当前标的尚无数据。打开“历史同步”选择日期范围后运行。": "No saved data for this underlying. Open History sync, select a date range and run it.",
  "标的价格": "Underlying price", "收盘": "close", "结算": "settlement", "可用 IV / 合约数": "Eligible IV / contracts", "异常报价独立标记": "Invalid references flagged separately", "约 30 天 ATM IV": "Approx. 30d ATM IV", "OTM 数据不足": "Insufficient OTM data", "20 日已实现波动率": "20d realized volatility", "ETF 复权对数收益 · 年化": "Adjusted ETF log returns · annualized", "持仓量 PCR": "Open-interest PCR", "成交量 PCR": "Volume PCR", "贴现数据日期": "Curve observation date", "手动": "Manual", "沿用前": "Using curve from ", "日曲线": " days earlier", "按到期日插值贴现": "Discount interpolation by expiry",
  "到期月份": "Expiry month", "全部到期日": "All expiries", "认购 + 认沽": "Calls + puts", "认购": "Calls", "认沽": "Puts", "按当日乘数计算；+ 加入组合，张数正买负卖": "Actual dated multipliers; + adds a position; positive contracts buy, negative sell", "导出": "Export", "合约": "Contract", "到期": "Expiry", "方向": "Type", "行权价": "Strike", "价格": "Price", "Theta/日": "Theta/day", "成交量": "Volume", "持仓量": "Open interest", "乘数": "Multiplier", "状态": "Status", "组合": "Portfolio", "加入合约": "Add contract ",
  "日线收盘价可能不同步；IV 采用认购认沽平价隐含远期。贴现曲线为国债收益率的零息近似，Greeks 固定当前有效持有成本。": "Daily closes may be asynchronous. IV uses put-call implied forwards. Government yields are zero-rate proxies; Greeks hold effective carry fixed.",
  "波动率微笑": "Volatility smile", "SVI 拟合": "SVI fit", "市场 OTM IV": "Market OTM IV", "个 OTM 执行价 · IV RMSE": " OTM strikes · IV RMSE ", "可用 OTM 执行价不足，未生成拟合曲线。": "Insufficient eligible OTM strikes to fit a smile.", "ATM 期限结构": "ATM term structure", "天": "d", "SVI 总方差拟合；超出观测范围不作翼部外推。": "SVI total-variance fit; no wing extrapolation beyond observed strikes.", "波动率曲面 · 远期对数价内外程度": "Volatility surface · forward log moneyness", "；总方差": "; total variance ", "曲面诊断：": "Surface diagnostics: ", "可插值": "Interpolation permitted", "保留诊断，不自动用于定价": "Review required; not used automatically for pricing", "近 40 个交易日 · 约 30 天 ATM IV": "Last 40 sessions · approximately 30d ATM IV", "正在计算历史 IV…": "Computing historical IV…", "历史 ATM IV": "Historical ATM IV",
  "在期权链点击 + 添加合约，可跨 ETF 组合。张数正买负卖；按当日真实乘数重估。": "Use + in the chain to add contracts across ETFs. Positive quantities buy, negative sell; valuation uses dated multipliers.", "组合为空，请先从期权链添加合约。": "Portfolio is empty. Add contracts from the option chain.", "张数": "quantity", "张": "contracts", "推进天数": "Days forward", "模拟路径": "Simulation paths", "ETF 相关系数": "ETF correlation", "年跳跃次数": "Annual jump intensity", "Log 跳跃均值": "Mean log jump", "跳跃标准差": "Jump standard deviation", "全量重估": "Revalue portfolio", "组合或参数已变化，请重新重估。": "Portfolio or parameters changed. Revalue to update the result.", "点击全量重估，计算组合 Greeks、33 个压力情景及联合 Monte Carlo。": "Revalue to calculate portfolio Greeks, 33 stress scenarios and joint Monte Carlo.",
  "组合市值 CNY": "Portfolio market value CNY", "现金 Delta": "Cash Delta", "Vega / 波动率 +1pp": "Vega / IV +1pp", "Theta / 天": "Theta / day", "最差情景 P&L": "Worst scenario P&L", "BS 全量重估 · 组合盈亏 CNY": "Full BS revaluation · portfolio P&L CNY", "标的冲击": "Underlying shock", "联合 Monte Carlo · 风险中性条件情景": "Joint Monte Carlo · risk-neutral conditional scenarios", "跳跃模型基准价值": "Jump-model base value", "标准误": "Standard error ", "跌 30% / IV +20pp 价值": "Value at spot -30% / IV +20pp", "同模型情景 P&L": "Scenario P&L vs same-model base", "同一 ETF 各合约共用路径": "Contracts share paths within each ETF", "基准 95% 抽样区间：": "Base 95% sampling interval: ",  "尾部有效样本不足：本次模拟不能据此认定极端风险为零，建议增加路径或调整抽样方法。": "Too few effective tail samples: this simulation cannot establish zero extreme risk. Increase paths or change the sampling method.",
  "每个 ETF 使用约 30 天 ATM 波动率和隐含远期曲线，相关性及系统性跳跃由上方参数指定。结果为 Q 测度下贴现到期收益，抽样区间不包含模型误差，不等同于真实世界 VaR 或保证金压力。": "Each ETF uses approximately 30d ATM volatility and its implied forward curve. Correlation and systemic jumps are scenario inputs. Discounted Q terminal payoffs and sampling intervals exclude model error; these are not physical VaR or margin requirements.",
  "正在核对分区覆盖与字段质量…": "Checking partition coverage and field quality…", "通过": "Passed", "待修复": "Needs attention", "行 ·": " rows · ", "个分区": " partitions", "数据同步": "Data sync",
  "上证50ETF": "SSE 50 ETF", "沪深300ETF·沪市": "CSI 300 ETF · Shanghai", "沪深300ETF·深市": "CSI 300 ETF · Shenzhen", "中证500ETF·沪市": "CSI 500 ETF · Shanghai", "中证500ETF·深市": "CSI 500 ETF · Shenzhen", "创业板ETF": "ChiNext ETF", "深证100ETF": "SZSE 100 ETF", "科创50ETF·华夏": "STAR 50 ETF · ChinaAMC", "科创50ETF·易方达": "STAR 50 ETF · E Fund",
}

const metacharacters = "^$.*+?()[]{}|" + String.fromCharCode(92)
const escapePattern = (value: string) => [...value].map(c => metacharacters.includes(c) ? String.fromCharCode(92)+c : c).join("")
const fragments = new RegExp(Object.keys(optionStrings).sort((a, b) => b.length-a.length).map(escapePattern).join("|"), "g")
const diagnosticZh: Record<string, string> = {
  "Option feature version changed; rebuild the requested history before research": "期权因子版本已更新，请先重建所选历史再检验",
  "Saved calibration is unavailable": "所选校准版本不可用",
  "Saved runs have no common return dates": "所选回测没有共同收益日期",
  "Benchmark coverage is insufficient on the shared dates": "共同日期内的基准覆盖不足",
  "Surface calibration is unreliable at the queried maturity": "查询期限相邻节点的校准质量未通过",
  "Surface query outside shared observed moneyness or maturities": "查询超出共享执行价或期限观测范围",
  "Surface query outside observed maturities": "查询超出已观测期限范围",
  "Surface diagnostics do not permit automatic interpolation": "曲面诊断不允许自动插值定价",
  "SSVI calibration did not converge": "SSVI 校准未收敛",
  "No common observed moneyness interval": "没有共享的价内外程度观测区间",
  "Calibration diagnostics or valuation context do not match this single-ETF basket": "校准诊断或日期、口径与当前单 ETF 组合不匹配",
  "Merton calibration requires at least 12 OTM quotes across two expiries": "Merton 校准需要至少两个到期日、12 个价外合约报价",

  "Option feature calculations failed for ": "期权指标计算失败，观测数：", " observations": " 条",
  "Inconsistent source closes reconstructed from complete intraday trades; original prices and evidence retained": "异常源收盘价已用完整日内成交重建，原始价格和证据均已保留",
  "No eligible IV observations": "没有可用的 IV 观测",
  "Fitted total variance decreases across maturities at shared forward moneyness": "相同远期价内外程度下，拟合总方差随期限下降",
  "fit quality or strike-arbitrage diagnostic requires review": "拟合质量或执行价套利诊断需要复核",
  "insufficient OTM strikes for SVI": "可用 OTM 执行价不足以拟合 SVI",
  "insufficient observed moneyness span": "观测价内外程度跨度不足",
  "源报价异常（隔离）": "Invalid source quote (quarantined)", "隔离的源报价": "Quarantined source quotes",
  "Unresolved source OHLC; excluded from valuation": "源 OHLC 无法核实，已排除估值",
  "Unresolved source OHLC quotes retained in quarantine and excluded from forwards, IV, surfaces and portfolio valuation": "无法核实的源 OHLC 报价已保留并隔离，不参与远期、IV、曲面及组合估值",
  "Unresolved source opens retained; daily high, low, close and volume verified against complete intraday evidence; do not use opens as fills": "未解决的原始开盘价已保留；高低价、收盘价和成交量已用完整日内证据核验；请勿用这些开盘价模拟成交",
  "Zero-volume reference opens differ from reference high/low; raw values retained and excluded from IV analysis": "零成交参考开盘价与参考高低价不一致；已保留原值并排除 IV 分析",
  "Zero-volume reference closes differ from non-trading OHLC; excluded from IV analysis": "零成交参考收盘价与占位 OHLC 不一致；已排除 IV 分析",
  "Insufficient dated feature coverage for the requested purged training window": "历史因子覆盖不足，无法满足已隔离标签的训练窗口",
  "Build dated option features before return research": "请先构建历史期权因子，再运行收益研究",
}
export function translateOptions(text: string, language: "zh" | "en"): string {
  if (language === "zh") {
    let value = text
    for (const [en, zh] of Object.entries(diagnosticZh)) value = value.replace(en, zh)
    return value
  }
  return optionStrings[text] ?? text.replace(fragments, key => optionStrings[key])
}
