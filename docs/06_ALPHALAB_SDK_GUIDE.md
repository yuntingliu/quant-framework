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
from alphalab.sdk.v1 import factor, signal, portfolio, execution
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

先保存，再运行“截面检验”确认某日排序和覆盖率；随后运行“历史检验”查看时间覆盖和稳定性。因子方向属于策略解释：动量、盈利能力常用高值优先，波动、杠杆和短期反转模板可能使用低值优先。

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

<!-- alphalab-sdk-topic:validation -->
## 验证 SDK

验证模块接收已经完成的回测数据，产出可 JSON 序列化的指标和归因结果。默认源码属于项目，可以修改并按修订保存。

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

`context` 提供策略收益、基准收益、持仓、成交、因子收益和运行元数据。官方验证模块注册：

- `performance`：总收益、年化收益、年化波动、Sharpe 和最大回撤。
- `research_quality`：项目可编辑的研究质量标准，返回 `passed`、`reasons`、`warnings`，以及阈值和证据。
- `alpha_beta`：月度 CAPM、多因子回归、Newey-West 标准误、相关矩阵和警告。

`context.diagnostics` 提供冻结的执行数据检查和紧凑信号证据副本；`context.executions` 提供每次调仓的实际成交、目标偏差和拒单记录。修改这些副本不会改变引擎或历史结果。

### 执行数据与研究评价

`execution_reliable` 表示引擎的执行数据检查是否通过，不保证供应商数据绝对准确，也不表示策略盈利。`research_valid` 来自本次冻结的 `research_quality`；普通停牌、涨跌停造成的持仓偏差默认只警告。需要严格跟踪目标时，可在项目 `validation.py` 中开启 `require_target_tracking=True` 或 `require_successful_exits=True`，并编辑偏差阈值及连续次数。连续次数统计调仓截面，不是逐日持仓。

旧项目没有 `research_quality` 时，新回测返回未评价（`research_valid=null`）；用户或 Agent 可明确添加该函数，保留其他自定义验证代码。已有冻结回测保留原来的评价。

默认 `strategy.py` 在下一开盘只尝试一次：买不到留现金，卖不出保留持仓，等待下一次明确决策。`fallback_candidates=()` 表示没有候补；可修改为事先确定的有序证券列表。跨日补单需要在 `@on_event` 中显式返回新目标，系统不自动补单或重新分配资金。

### 参数与输出

验证函数同样使用仅限关键字的字面量默认参数，界面可把这些参数安全投影回 Python。输出不得包含 `DataFrame`、`Series`、NumPy 标量、NaN 或无穷值；请在返回前转换成普通 `dict`、`list`、`int`、有限 `float`、`str`、`bool` 或 `None`。

### 研究解释

样本过少、因子覆盖不足、回归矩阵秩不足和基准缺口都应作为警告保留，而不是静默补值。稳健性页的“研究候选”不是实盘承诺；应结合样本外区间、成本敏感性、换手、集中度和多重检验校正一起判断。

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
