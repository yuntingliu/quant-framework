# AlphaLab 测试数据流水线报告

> 注意：本次使用的是 Conexus 生成的模拟行情测试数据，仅用于功能测试，不代表真实市场数据或投资建议。

## 生成的测试数据

- 文件：`E:\quant-framework\data\market\bars.parquet`
- 股票数：15，只模拟代码 `TEST001` ~ `TEST015`
- 行数：5,955
- 日期范围：`2025-01-02` ~ `2026-07-10`
- 字段：`date, symbol, open, high, low, close, volume`

## 流水线运行参数

- projectRoot: `E:\quant-framework`
- strategy_path: `alphalab/strategies/momentum.yaml`
- data_dir: `E:\quant-framework\data`
- universe: `all`
- start_date: `2025-11-12`
- end_date: `2026-07-10`
- as_of_date: `2026-07-10`
- lookback_days: `120`
- fields: `open, high, low, close, volume`
- run_id: `20260712_225223`
- run_dir: `E:\quant-framework\data\app\pipeline_runs\20260712_225223`

## 阶段计数

| 阶段 | 结果 |
|---|---:|
| DataEngine symbols | 15 |
| DataEngine bars | 2,595 |
| DataEngine fundamentals | 0 |
| compute_factor symbols_with_scores | 15 |
| compute_factor factor_columns | 3 |
| SignalEngine score_count | 15 |
| SignalEngine selected_count | 10 |
| run_backtest return_periods | 8 |
| run_backtest weight_dates | 8 |
| ResultStore strategies | 1 |
| ResultStore signals | 1 |
| ResultStore backtests | 1 |

## 因子

- `momentum_20d`，technical，long，weight 0.55
- `momentum_60d`，technical，long，weight 0.30
- `volatility_20d`，technical，short，weight 0.15

## 目标权重

| symbol | target_weight |
|---|---:|
| TEST008 | 0.10 |
| TEST015 | 0.10 |
| TEST014 | 0.10 |
| TEST010 | 0.10 |
| TEST002 | 0.10 |
| TEST013 | 0.10 |
| TEST004 | 0.10 |
| TEST007 | 0.10 |
| TEST011 | 0.10 |
| TEST006 | 0.10 |

权重合计：`1.00`

## 回测绩效指标

| 指标 | 值 |
|---|---:|
| total_return | 0.1208155547 |
| annual_return | 0.1865914759 |
| annual_vol | 0.0632778937 |
| sharpe | 2.9487624336 |
| max_drawdown | -0.0042365438 |
| n_periods | 8 |

## 回测收益样例

| date | strategy_return |
|---|---:|
| 2025-11-30 | 0.0033357772 |
| 2025-12-31 | 0.0137130479 |
| 2026-01-31 | 0.0582136385 |
| 2026-02-28 | -0.0042365438 |
| 2026-03-31 | 0.0023223657 |
| 2026-04-30 | 0.0078241236 |
| 2026-05-31 | 0.0234706786 |
| 2026-06-30 | 0.0115228550 |

## 保存位置 / ID

- db_path: `E:\quant-framework\data\app\alphalab.db`
- strategy_id: `momentum_test_fixture`
- signal_id: `90d99db89513`
- backtest_id: `94b5949cd112`
- receipt_path: `E:\quant-framework\data\app\pipeline_runs\20260712_225223\result_store_receipt.json`

## 数据库核验

| 表 | 行数 |
|---|---:|
| strategies | 1 |
| signals | 1 |
| signal_targets | 10 |
| backtests | 1 |
| backtest_returns | 8 |
| backtest_weights | 80 |
| orders | 0 |
| journal | 0 |

## 额外修复

运行回测时，当前 pandas 版本不支持源码中的 `resample("ME")` 月末频率别名。已将 `alphalab/engine.py` 中该处改为 `resample("M")`，并保留备份：

- `E:\quant-framework\alphalab\engine.py.bak_conexus_me_patch`

## 数据缺口

- fundamentals 仍为 0；当前 momentum 策略只使用技术因子，因此不影响本次完整功能测试。
