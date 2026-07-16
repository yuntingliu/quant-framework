# AlphaLab 量化研究流水线运行诊断

## 运行参数

- projectRoot: `E:\quant-framework`
- strategy_path: `alphalab/strategies/momentum.yaml`
- data_dir: `E:\quant-framework\data`
- universe: `all`
- symbols: 未指定，按策略 / DataEngine universe 解析
- start_date: `2025-11-14`
- end_date: `2026-07-12`
- as_of_date: `2026-07-12`
- lookback_days: `120`（工具默认）
- fields: `open, high, low, close, volume`

## 阶段结果

### 1. DataEngine Fetch Data

- stage: `DataEngine`
- run_id: `20260712_224957`
- run_dir: `E:\quant-framework\data\app\pipeline_runs\20260712_224957`
- bars_path: `E:\quant-framework\data\app\pipeline_runs\20260712_224957\bars.json`
- fundamentals_path: `E:\quant-framework\data\app\pipeline_runs\20260712_224957\fundamentals.json`

计数：

| 项目 | 数量 |
|---|---:|
| symbols | 0 |
| bars | 0 |
| fundamentals | 0 |

样例：无。

### 2-5. 后续阶段

未执行：

2. compute_factor
3. SignalEngine Generate Weights
4. run_backtest
5. ResultStore Save Results

原因：DataEngine 返回 `symbols=0` 且 `bars=0`。根据流水线规则，不能伪造因子、信号、回测或保存结果。

## 数据缺口

本地未发现预期行情文件：

- `E:\quant-framework\data\market\bars.parquet` 不存在

当前只生成了本次空运行快照：

- `bars.json`，长度 2 字节，内容为空数组
- `fundamentals.json`，长度 2 字节，内容为空数组

## 结论

本次流水线已启动，但在第 1 阶段因本地行情数据缺失而停止。需要先放入本地 market/bars.parquet 或配置可用数据源后再运行完整流程。