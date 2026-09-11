# AlphaLab SDK 使用指南

<!-- alphalab-sdk-version:1 -->

本文是 AlphaLab SDK 的统一用户文档，也是 Web 工作台“文档”抽屉的唯一内容来源。当前稳定合约版本为 `1`。代码中的 `SDK_VERSION`、`VALIDATION_SDK_VERSION` 和报告描述符 `version` 都必须与对应章节一致。

<!-- alphalab-sdk-topic:overview -->
## SDK 总览

AlphaLab 把一项量化研究拆成五个可保存、可检查、可复现的部分：数据配方、因子、策略、验证和报告。它们共享同一条研究链路：

```text
数据配方 -> 统一研究数据 -> 因子 -> 策略决策 -> 回测与验证 -> 报告
```

### 最重要的约定

- Python 源码是项目的事实来源。可视化控件只修改能被静态识别的参数，不维护第二份隐藏配置。
- 策略和因子使用 `SDK_VERSION = 1`；验证模块使用 `VALIDATION_SDK_VERSION = 1`；报告描述符使用 `version: 1`。
- `DATA_REQUIREMENTS` 声明运行前必须存在的数据集和字段。声明不完整会在保存或运行前被拒绝。
- 所有历史读取都以当前评估时点为上界。因子不得自行读取未来日期，基本面必须使用当时已经披露的数据。
- 编辑器中的 Python 是受信任的本机代码，不是安全沙箱。保存会做静态检查和探测运行；真正执行前仍需要用户确认。
- 每次正式运行固定源码修订、源码哈希、数据摘要和参数。修改后必须重新保存，旧 Run 不会被静默改写。

### 推荐工作流

1. 新建项目会一次复制与项目类型匹配的 `recipe.py`、`strategy.py`、因子和 `validation.py` 模板；先在数据工作台预览计划，再同步并检查覆盖范围。
2. 在因子工作台从默认因子库或通用因子骨架复制模板，保存后做截面和历史检验。
3. 在策略工作台配置选股、仓位、持有期风控和成交规则，然后运行可视化预览。
4. 在验证工作台设置样本区间与验证参数，保存源码后运行回测。
5. 在报告工作台检查结论、结构化数据、图表、来源与运行溯源。

### 公共入口

研究代码应优先从稳定门面导入：

```python
from alphalab.sdk.v1 import (
    execution, factor, neutralize_factor_scores, optimize_portfolio, portfolio, signal,
)
from alphalab.data_sdk.v1 import data_recipe
from alphalab.validation_sdk import analysis
```

不要从运行器、仓储实现或 Dashboard 服务层导入内部对象。需要更精确的机器合约时，可继续查阅 `docs/02_STRATEGY_SDK_V1_CONTRACT.md` 和 `docs/05_DATA_SDK_V1_CONTRACT.md`。

<!-- alphalab-sdk-topic:factor -->
## 因子 SDK

因子是在某个评估时点，为当前标的池返回一组横截面数值的纯 Python 函数。默认因子模板已经包含可运行实现和教学型注释，加入项目后会成为独立源码副本。

### 最小因子

```python
@factor(id="momentum_20d", label="20 日动量")
def momentum_20d(context, *, window: int = 20):
    """返回每个标的最近 window 个交易日的累计收益率。"""
    # 多取一个收盘价，才能形成恰好 window 段收益。
    close = context.history("close", window=window + 1)
    if len(close.index) < window + 1:
        # 历史不足时返回同索引的 NaN，而不是用不完整窗口制造信号。
        return close.mean(axis=0) * float("nan")
    return close.iloc[-1] / close.iloc[0] - 1.0
```

### 注册与参数

- `@factor(id=..., label=...)` 的 `id` 在项目内唯一，也是 `context.factor(...)` 的引用名。
- 函数第一个参数必须是 `context`；用户参数必须是仅限关键字参数，并提供字面量默认值。
- 普通 `int`、`float`、`str`、`bool` 参数可在界面编辑。需要范围和步长时使用 `Annotated[..., Parameter(...)]`。
- 因子依赖必须写成静态调用，例如 `context.factor("quality")`。系统会检查不存在的依赖和依赖环。

### 可用数据

- `context.history(field, window=..., symbols=...)`：读取截至当前评估时点的历史矩阵；索引为日期，列为证券代码。
- `context.fundamental(field)`：读取截至当前时点可见的最新基本面横截面。
- `context.factor(id, **parameters)`：复用同项目内另一个已注册因子。
- `context.combine_factors(weights=..., normalization=..., parameters=...)`：按 `raw`、`rank` 或 `zscore` 合成多个因子。
- `neutralize_factor_scores(values, exposures)`：只用同一评估时点可见的横截面暴露做残差化，并返回样本量、系数与拟合诊断。

相应字段必须出现在模块的 `DATA_REQUIREMENTS` 中，例如：

```python
DATA_REQUIREMENTS = {
    "bars": ["close", "volume", "amount"],
    "fundamentals": ["roe", "bp"],
}
```

### 返回值与缺失值

因子应返回以证券代码为索引的一维 `pandas.Series`。数值必须可转换为有限浮点数；个别标的不可计算时保留 `NaN`，不要填成 `0`。历史窗口不足、分母为零或输入缺失都应显式产生 `NaN`，让后续覆盖率与候选过滤保留真实信息。

### 检验顺序

行情终端可以按证券名称、代码片段或完整代码搜索。完整代码兼容 `000001.SZ` 和
RQData 的 `000001.XSHE`，沪市 `.XSHG`、北交所 `.XBEI` 同样会转换为 `.SH`、`.BJ`。
界面和研究数据统一显示转换后的代码。格式无效会提示“证券代码格式不支持”；格式正确
但本地没有行情时，需要检查代码或先在数据工作台同步。

先保存，再运行“截面检验”确认某日排序和覆盖率；“历史分布”确认逐期可用性；“研究证据”使用下一交易日开盘起算的前瞻收益，给出 Rank IC、ICIR、Newey-West t 值、Bootstrap 区间、1/3/6 期衰减、分组收益和 70/30 时序留出结果。缺失价格按配对样本剔除，不补成零。因子方向属于策略解释：动量、盈利能力常用高值优先，波动、杠杆和短期反转模板可能使用低值优先。

<!-- alphalab-sdk-topic:strategy -->
## 策略 SDK

策略模块把标的池、因子、调仓信号、目标仓位、持有期事件和成交假设组合成一个事件驱动流程。项目源码是唯一事实来源，可视化设置会投影回这份 Python。

### 执行顺序

```text
@universe -> @factor -> @signal -> @portfolio -> @on_event -> @execution
                                                        -> @execution_data_fill（实际下单时）
```

- `@universe` 返回 `UniverseResult`，限定当前可研究标的。
- `@signal` 按 `Daily`、`Weekly` 或 `Monthly` 日程运行，返回 `SignalResult`。
- `@portfolio` 把入选标的转换为 `PortfolioDecision.target_weights`。
- `@on_event` 可在开盘、收盘、决策、成交或拒单事件上读取和更新持久 `state`。
- `@execution` 返回 `ExecutionPolicy`，声明激活时点、费用、滑点、容量和备选标的。
- `@execution_data_fill` 直接接收并返回执行状态 `DataFrame`；补齐代码就在
  `strategy.py` 中，用户和 Agent 都能编辑。

### 关键返回对象

```python
return SignalResult(selected=selected, scores=scores, state=state)

return PortfolioDecision(
    target_weights={symbol: weight for symbol in selected},
    state=state,
    reason="monthly_rebalance",
)

return ExecutionPolicy(
    activation="next_session_open",
    commission_rate=0.00025,
    slippage_rate=0.00020,
    max_participation_rate=0.10,
)
```

`SignalResult.selected` 决定候选顺序，`scores` 保存解释用评分；`PortfolioDecision` 表达目标而不是订单；`ExecutionPolicy` 决定目标如何在市场约束下尝试成交。

SDK 也提供显式、无隐藏回退的组合优化器：

```python
history = context.history("close", window=121, symbols=signal.selected)
returns = history.pct_change().dropna()
optimized = optimize_portfolio(
    returns,
    method="risk_parity",  # equal_weight / minimum_variance / hrp / max_sharpe
    max_weight=0.20,
    current_weights={holding.symbol: holding.weight for holding in context.portfolio.positions},
    max_turnover=0.30,
)
return PortfolioDecision(
    target_weights=optimized.weights,
    diagnostics=dict(optimized.diagnostics),
    state=state,
)
```

`max_sharpe` 必须显式传入年化 `expected_returns`。样本不足、约束不可行或求解不收敛会抛出 `PortfolioOptimizationError`；系统不会悄悄改用等权。默认项目仍采用等权，只有用户或 Agent 明确修改 `@portfolio` 后才启用高级优化。

缺失状态的处理不是隐藏模式，而是项目内可编辑的普通 Python。例如：

```python
@execution_data_fill(id="fill_missing_market_state")
def fill_missing_market_state(
    context,
    rows,
    *,
    main_board_limit_rate=0.10,
    star_market_limit_rate=0.20,
    chinext_limit_rate=0.20,
    etf_limit_rate=0.10,
    ipo_unlimited_sessions=5,
):
    filled = rows.copy()
    symbols = list(filled["symbol"])
    previous_close = context.history("raw_close", window=1, symbols=symbols).iloc[-1]
    missing_up = filled["limit_up"].isna()
    missing_down = filled["limit_down"].isna()
    filled.loc[missing_up, "limit_up"] = filled.loc[missing_up, "symbol"].map(
        (previous_close * (1.0 + main_board_limit_rate)).round(2)
    )
    filled.loc[missing_down, "limit_down"] = filled.loc[missing_down, "symbol"].map(
        (previous_close * (1.0 - main_board_limit_rate)).round(2)
    )
    return filled
```

正式默认项目还会读取上一交易日 `is_st`、证券类型和上市日期，区分主板、ST、创业板、
科创板、北交所、ETF 和股票上市初期。规则作为该函数的可编辑关键字默认参数展示在策略工作台。
制度上没有涨跌停的上市初期交易日用 `limit_up == limit_down == 0` 成对标记；单边 0
会被拒绝，数据源自身的非正数也会先按缺失处理。停牌状态默认在最长 120 个历史交易日
内取最近已知值；整个窗口都没有记录时，模板可通过可编辑开关按“未停牌”补齐。

旧项目不会被后台静默改写。缺少当前默认项目基础组件时，可显式选择迁移；系统只更新
默认标的池和交易状态补齐函数并保存为新版本，历史版本和历史回测保持原样。

### 时点与可交易性

- 收盘生成的信号默认不能用同一收盘价成交；官方模板使用 `next_session_open`。
- `context.universe` 是运行时点全部有效证券，并不隐含股票类型过滤。所有新项目都复制 `sdk-v1-default`；其默认 `research_universe` 按 `context.instruments()` 中的 `asset_type == "CS"` 过滤普通股票。用户明确要求固定、指数、行业或 ETF 范围时，Agent 在首次保存前替换这个已注册函数，而不是选择另一套项目模板。
- 严格执行模式不会因状态缺失而提前清空信号证券池。只有实际订单进入成交阶段时，才运行项目的 `@execution_data_fill`，随后校验停牌、涨停价和跌停价。
- 补齐函数只能填 `is_suspended`、`limit_up` 和 `limit_down` 的缺失值，不能覆盖已知值或修改行情。传入 Context 的行情历史严格结束在成交日前；普通涨跌停价使用前一交易日未复权 `raw_close`，开盘/收盘成交限制分别使用当日未复权 `raw_open`/`raw_close` 比较，返回值仍缺失时该订单被拒绝。
- 已持仓标的无法成交时保留原持仓；只有确认到达退市日才按零价值核销。
- 成交参与率和冲击成本依赖成交额。数据缺失时应查看 Run 的执行审计和警告，不要把未成交误认为零收益交易。

回测摘要和任务终态都直接给出警告、研究有效性、尝试成交数、成功成交数、补齐值数量和
状态缺失拒单数。信号分析只保存每个调仓截面的标的计数、覆盖率、换手和到下一截面的
秩 IC；完整股票评分只在当次回测进程内计算，不进入摘要。稳健性分析根据收益日期判断
日频、周频或月频年化，调仓频率不再代替收益频率。

### 状态管理

`state` 是跨事件保存的普通可序列化字典。把止盈锁定、峰值收益或冷却期等策略状态写回返回对象；不要用模块全局变量保存回测状态。持仓真相来自 `context.portfolio`，不要在 `state` 中复制一套虚拟持仓。

### 保存与预览

保存时系统检查装饰器、参数、依赖、数据要求和最小探测运行。策略预览依次展示评分、入选证券、目标权重、事件结果和执行策略；只有保存后的修订可以进入正式回测。

### 默认等权配置与现金

“月末动量 Top N”的 `top_n` 控制最多选取多少只证券；“仓位分配 → 等权配置”的
`max_weight` 单独控制单只目标权重上限，默认 `0.10`（10%）。实际权重为：

```python
weight = min(max_weight, 1.0 / len(selected)) if selected else 0.0
```

| 实际入选数量 | 单只上限 | 每只目标权重 | 目标现金比例 |
| --- | --- | --- | --- |
| 10 | 10% | 10% | 0% |
| 8 | 10% | 10% | 20% |
| 8 | 12.5% | 12.5% | 0% |

若希望 8 只证券满仓等权，可把单标的最大权重改为 `0.125`，应用全部设置后重新预览。
这是策略参数示例；改变选取数量不会自动修改权重上限。预览展示目标总仓位和目标现金
比例，实际成交后的现金还会受费用、停牌、价格限制和成交容量影响。

<!-- alphalab-sdk-topic:data -->
## 数据配方 SDK

数据配方是完整、可编辑的 Python 同步程序。内置模板直接调用 `rq.*`，再通过 `context` 预览步骤、分批增量同步、规范化和发布数据；后端不会按模板名偷偷替换源码逻辑。

### 基本结构

```python
from alphalab.data_sdk.v1 import data_recipe, rq


@data_recipe(id="research_data", label="研究数据", template="rq.a_share_research")
def research_data(context, *, start: str, end: str, symbols=None):
    """声明计划，并在运行模式下把数据发布到统一研究仓库。"""
    if context.mode == "plan":
        context.expect("rq.bars", "rq.get_price", start=start, end=end)
        return

    raw = rq.get_price(symbols, start_date=start, end_date=end)
    # 实际配方应先调用 SDK 的 normalize_* 工具，再发布规范化数据。
    context.publish("rq.bars", raw)
```

### `plan` 与 `run`

- `context.mode == "plan"` 时只调用 `context.expect(...)` 描述步骤，不访问数据供应商、不落库。
- 运行模式可以调用供应商 API，并用 `context.publish(dataset, frame)` 写入统一数据集。
- `context.sync_batches(...)` 根据水位、上市日期、分批大小、日期分块和重叠天数生成增量请求。
- `context.watermark(...)` 读取已有进度；`force=True` 才从头覆盖指定范围。
- `context.require_coverage(..., fail_on_gap=True)` 在完成前检查日期和标的覆盖。

### 数据边界

规范化函数负责代码、日期、字段名和单位转换。日线研究价格与未复权 OHLC（`raw_open`、`raw_high`、`raw_low`、`raw_close`）应同时保留；停牌/ST 状态必须与标的、日期主键对齐；财务报表必须保留披露信息以构建 PIT 基本面。不要在配方中用回测结束日可见的财务值回填整个历史。

### 模板与自定义副本

切换内置模板会替换当前编辑器源码；修改内置模板后应另存为自定义模板。`template=...` 只供工作台识别来源，不参与运行时分发。供应商函数参数请同时参考数据工作台中的 RQData 官方文档链接。

### 同步日期与本地覆盖

同步表单的结束日期可选到当天，用于继续获取本地最后日期之后的数据；已保存配方中的
结束日期会保留，需要按本次同步范围修改。RQData 连接测试显示供应商的最新交易日，
连接成功本身不会下载行情。修改日期会更新 `recipe.py`，执行“运行并同步”后才扩展
本地覆盖。因子检验和回测的日期范围仍以已同步数据为准。

<!-- alphalab-sdk-topic:validation -->
## 验证 SDK

验证模块接收已经完成的回测数据，产出可 JSON 序列化的指标和归因结果。默认源码属于项目，可以修改并按修订保存。

### 选择策略并运行回测

在“验证与回测 → 回测配置 → 回测策略”选择项目。该选择与其他工作台共享，右侧加载
该项目的 `validation.py`，下方历史回测只显示该项目的记录。切换前先保存验证代码和
参数；有未保存修改时，界面会提示确认是否放弃。选择历史结果不会重置正在编辑的验证
代码或回测日期。运行回测使用所选项目已保存的策略与验证源码；修改源码后，旧结果
仍保留运行时的内容，需要重新运行才能验证修改。

### 分析入口

```python
from alphalab.validation_sdk import ValidationContext, analysis

VALIDATION_SDK_VERSION = 1


@analysis(id="performance", label="收益与回撤")
def performance(context: ValidationContext, *, periods_per_year: int = 252) -> dict:
    """根据策略周期收益计算界面展示的核心绩效指标。"""
    returns = context.returns.dropna().astype(float)
    # 返回值必须只包含可 JSON 序列化的标量、列表和字典。
    return {"n_periods": int(len(returns))}
```

`context` 提供策略收益、基准收益、持仓、成交、因子收益、设置和只读 `diagnostics`。后者来自同一冻结 Run，只用于研究解释，不能修改成交或核算。官方验证模块注册：

- `performance`：总收益、年化收益、年化波动、Sharpe 和最大回撤。
- `research_quality`：项目可编辑的研究质量标准，返回 `passed`、`reasons`、`warnings`，以及阈值和证据。
- `alpha_beta`：月度 CAPM、多因子回归、Newey-West 标准误、相关矩阵和警告。
- `risk`：历史 VaR/CVaR、下行波动、回撤持续期、集中度、有效持仓数和换手。

研究质量同时包含信号 IC、覆盖率、执行保真度及 `evidence_status`（pass/fail/insufficient）。默认信号证据作为诊断，`passed` 与 `status` 始终遵循项目选定的质量门槛；开启 `require_signal_evidence=True` 后，证据不足或不达标也会阻止研究质量通过。质量通过不等于盈利。

`context.diagnostics` 提供冻结的执行数据检查和紧凑信号证据副本；`context.executions` 提供每次调仓的实际成交、目标偏差和拒单记录。修改这些副本不会改变引擎或历史结果。

### 执行数据与研究评价

`execution_reliable` 表示引擎的执行数据检查是否通过，不保证供应商数据绝对准确，也不表示策略盈利。`research_valid` 来自本次冻结的 `research_quality`；普通停牌、涨跌停造成的持仓偏差默认只警告。需要严格跟踪目标时，可在项目 `validation.py` 中开启 `require_target_tracking=True` 或 `require_successful_exits=True`，并编辑偏差阈值及连续次数。连续次数统计调仓截面，不是逐日持仓。

旧项目没有 `research_quality` 时，新回测返回未评价（`research_valid=null`）；用户或 Agent 可明确添加该函数，保留其他自定义验证代码。已有冻结回测保留原来的评价。

默认 `strategy.py` 在下一开盘只尝试一次：买不到留现金，卖不出保留持仓，等待下一次明确决策。`fallback_candidates=()` 表示没有候补；可修改为事先确定的有序证券列表。跨日补单需要在 `@on_event` 中显式返回新目标，系统不自动补单或重新分配资金。

### 参数与输出

验证函数同样使用仅限关键字的字面量默认参数，界面可把这些参数安全投影回 Python。输出不得包含 `DataFrame`、`Series`、NumPy 标量、NaN 或无穷值；请在返回前转换成普通 `dict`、`list`、`int`、有限 `float`、`str`、`bool` 或 `None`。

### 研究解释

样本过少、因子覆盖不足、回归矩阵秩不足和基准缺口都应作为警告保留，而不是静默补值。尾部风险样本不足时返回 `None` 和 `insufficient`，不能用 `0` 表示未知风险。历史 Run 的验证页直接读取当时冻结的命名输出，不使用当前 `validation.py` 重算。稳健性页的“研究候选”不是实盘承诺；应结合样本外区间、成本敏感性、换手、集中度和多重检验校正一起判断。

<!-- alphalab-sdk-topic:report -->
## 报告结果协议

报告工作台接收 Agent 生成的版本化研究结果。报告不是任意 HTML 页面，而是由 Markdown 文档、可选数据表、可选图表、来源和溯源组成的结构化描述符。

### 最小报告

```json
{
  "version": 1,
  "reportId": "momentum-study-20260901",
  "kind": "document",
  "title": "动量因子研究",
  "markdown": "# 结论\n\n这里写研究结论与限制。",
  "sources": []
}
```

`reportId` 必须稳定且唯一；同一 ID 的新结果会替换工作区中的旧版本。Markdown 会经过安全过滤，不能依赖脚本或危险 HTML。

### 数据表

`table.columns` 中每列必须提供唯一 `key`、显示名 `label` 和格式 `text | number | percent | date | datetime`。`table.rows` 的每一行都要为所有列提供字符串、有限数字、布尔值或 `null`。工作台支持筛选、排序和 CSV 导出。

### 图表

每个图表提供唯一 `id`、`type`、`title`、`xKey`、`series` 和 `rows`。类型支持 `line`、`bar`、`area`、`scatter`、`pie`；序列格式支持 `number` 和 `percent`。饼图只能包含一个数值序列。

### 来源与溯源

- `sources` 记录数据、网页或内部文档来源；不能用它代替正文中的方法说明。
- `profile` 固定为当前 `runtime` 数据环境。
- `backtestId`、`runId`、`artifactId` 和 `provenance` 用于把结论追溯到具体运行、源码和数据。
- 报告正文应明确研究区间、基准、成本、缺失数据处理、主要限制和不可外推的部分。

建议让表格和图表承载可交互数据，让 Markdown 专注于结论、证据、方法和风险说明；三者应来自同一组结果，而不是手工复制出相互矛盾的数字。

### 技术证据与讲义复算

`alphalab.analytics` 提供 `TechnicalMetadata`、`technical_evidence`、
`render_technical_evidence` 和 `audit_annual_return_table`，用于行情证据和结果审计。
它们不生成订单，也不替代 SDK 回测。技术证据仅使用 `as_of` 当日及之前的数据，
要求 OHLC 使用相同复权口径；未知指标返回 `None`，不补齐历史。单日 TR 和
Wilder ATR(14) 分开报告，Bollinger(20,2) 中轨与 MA20 使用同一序列。

本地 CSV/parquet 可以通过以下命令输出 JSON、Markdown 或复算表：

```powershell
python -m scripts.technical_review bars.csv --symbol STOCK --market SSE --currency CNY --price-basis unadjusted --volume-unit shares --source local-snapshot --as-of 2026-09-04 --output artifacts/technical-review
python -m scripts.audit_factor_slides annual.csv --benchmark benchmark --input-unit percent --output artifacts/replication/audit.csv
```

技术输入含 `date,open,high,low,close,volume`；日期为交易所日线日期，调用者负责
核对交易日连续性。基准需同时提供文件、名称、来源和价格口径，比较使用相同起止日期，
不对缺失基准价格前向填充。年度复算输入首列为连续完整年份，每列为一个收益系列。
复算公开年度表格只能验证算术，不能证明股票层面的独立复现。

新增可编辑因子模板 `lower_shadow_recovery`、`three_white_soldiers` 和
`volume_confirmed_breakout` 会出现在现有因子目录；形态输出为 0/1，缺失为 NaN。
前两者使用显式下影线/实体比例，突破条件使用当日前 20 日高点和均量。
它们可以作为选股候选过滤器，默认参数没有经过收益最大化调参，也没有已验证的胜率。
原始价格跨除权日可能制造虚假形态，选择研究用价格口径时需先核对公司行动。

`skills/alphalab-technical-evidence-review` 提供技术复盘流程，
`skills/alphalab-factor-replication-audit` 提供讲义、论文与指数表现对照流程。
在其他机器上将对应文件夹复制到用户的 Codex skills 目录后即可发现；当前仓库保存版本来源。

### 质量组合的股票级复现

`python -m scripts.research_quality_replication` 将已有本地 RQ 缓存转换为冻结快照，
然后通过当前 SDK 项目保存和事件引擎运行固定对照。它不调用旧版回测器。

```powershell
python -m scripts.research_quality_replication prepare --data-root C:/path/to/legacy/data --snapshot artifacts/quality/snapshot --start 2018-12-28 --end 2020-12-31
python -m scripts.research_quality_replication run --snapshot artifacts/quality/snapshot --output artifacts/quality/runs --start 2018-12-28 --end 2020-12-31
```

输入约定是 `rq` 下的原始日线、财报、证券主表、日度因子、历史行业与指数成分，
以及 `sector_rotation/rq` 下的历史复权因子及其覆盖清单。输出保留逐日收益、实际权重、
执行记录、源码和输入指纹。起点要包含前一年的最后交易日，以便首次月末信号在次年开盘生效。

财报处理使用原始披露、至少下一日可见、精确报告季度的 TTM，不再将四条观测当作四个连续季度。
`roe` 的分母是本期与去年同期的平均正净资产；`roe_latest_equity` 保留期末净资产口径用于对照。
`roa` 使用相应平均总资产，分子仍为 TTM 归母利润，是明确的研究代理。
缺失历史、晚披露依赖、负净资产及只有调整后记录的季度不会被补成高质量观测。
这些规则适用于重新生成的基本面快照；已有旧缓存需要显式重建，不会自动变成新口径。

新增 `quality_profitability` 模板把 ROE、ROA 与低负债率的横截面排名等权合成，
任一字段缺失或无穷时剔除。先设定点时有效股票池，再进行排名；它没有盈利稳定性维度，
不等同于 MSCI、中证或讲义未指明的质量指数，也没有通过样本外择时或机器学习验证。

研究组合采用月末选股、下一交易日开盘执行，固定前50名，并区分等权和总市值加权。
SDK 的比例权重不包含100股整手、最低佣金及印花税账户账本；容量名义资金参数不能当作真实初始现金。
因此不能把结果直接称为20万元账户可实现收益。历史成分缓存按月提供，涨跌停价由保存的默认
`execution_data_fill` 规则补齐，均要在报告中披露；严格执行检查通过不代表这些估计成为供应商实测值。
