# AlphaLab

AlphaLab 是以 Python 为核心的量化研究工作台，连接数据采集、因子研究、选股策略、日度事件回测、验证与研究报告。当前版本为 **0.6.2**。

使用说明、开发参考和版本记录见 [文档导航](docs/README.md)。

## 当前能做什么

| 环节 | 当前能力 |
| --- | --- |
| 数据 | RQ 股票、ETF、行情、停牌/ST、财务、日因子和历史指数成分；可编辑采集配方、分块同步、增量续跑和覆盖校验 |
| 因子 | SDK 原生因子、PIT 财务处理、中性化、Rank IC/ICIR、分组收益、衰减与时序留出诊断 |
| 技术证据 | 均线、MACD、RSI、KDJ、ATR、布林带、CCI、OBV；下影线收回、三连阳实体和放量突破模板 |
| 策略与组合 | 日/周/月日程、自定义日程、事件规则、等权、最小方差、风险平价、HRP 等组合方法 |
| 回测与验证 | 日度事件引擎、下一交易日执行、交易约束与成本、冻结源码及结果、独立验证代码和研究证据 |
| 工作台与 Agent | 六个工作台、共享 Python 编辑器；可选 Conexus Agent 通过相同项目和回测接口开展研究 |

日度事件引擎支持每日生成信号；实际调仓频率由项目日程、目标权重和执行条件决定。它不表示默认每天交易，也不是分钟级交易系统。主题轮动、图形筛选和机器学习效果仍需按具体项目检验，不能从框架支持某项功能推导出收益改善。

## 一个项目，一条执行流程

```text
研究项目（数据库保存规范源代码）
├── recipe.py               数据采集与本地发布
├── factors/<factor_id>.py   每个文件一个注册因子
├── strategy.py             股票池、日程、信号、组合、事件和执行规则
└── validation.py           冻结回测结果的研究验证

数据 → 因子 → 策略源码组装 → 日度事件回测 → 验证 → 报告
```

项目、数据、因子、策略、验证、报告六个工作台使用相同的项目状态。表单和编辑器修改同一份规范源码；保存自动记录不可变源码包，回测固定策略与验证版本。编辑器磁盘镜像只供语言服务使用。

策略使用 `alphalab.sdk.v1`；数据配方和验证代码分别使用自己的 SDK。完整示例、参数与研究命令统一维护在 [中文 SDK 指南](docs/06_ALPHALAB_SDK_GUIDE.md)，该文件也由网页内的文档面板读取。

## 本地启动

在仓库根目录运行。需要 Python >= 3.10，并使用 `.node-version` 指定的 Node.js 主版本。Python 的 `dev` 依赖包含 Pyrefly、Ruff 等编辑器工具。

### Windows PowerShell

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dashboard,dev,rq]"
npm --prefix dashboard/frontend ci
.\.venv\Scripts\python.exe -m alphalab.cli dev doctor
```

在两个终端中分别运行，均从仓库根目录开始：

```powershell
# 终端一：后端
.\.venv\Scripts\python.exe -m uvicorn dashboard.backend.main:app --host 127.0.0.1 --port 8000 --reload
```

```powershell
# 终端二：前端
npm --prefix dashboard/frontend run dev:web
```

打开 [本地开发工作台](http://localhost:5173)。Vite 将 `/api` 和语言服务 WebSocket 代理到后端 8000 端口。

### macOS / Linux

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dashboard,dev,rq]'
npm --prefix dashboard/frontend ci
.venv/bin/python -m alphalab.cli dev doctor
.venv/bin/python -m uvicorn dashboard.backend.main:app --host 127.0.0.1 --port 8000 --reload
```

另开终端，从仓库根目录执行 `npm --prefix dashboard/frontend run dev:web`。

`doctor` 只读检查环境，不打印凭据。首次安装尚无 RQ 配置或研究数据时，它可能报告未就绪；按报告补齐对应配置和数据即可。

### 使用构建后的网页

```powershell
npm --prefix dashboard/frontend run build:web
.\.venv\Scripts\python.exe -m uvicorn dashboard.backend.main:app --host 127.0.0.1 --port 8000 --workers 1
```

打开 [本地构建工作台](http://127.0.0.1:8000/app/)。macOS/Linux 将 Python 路径替换为 `.venv/bin/python`。持续运行的配置与更新流程见 [通用部署](docs/05_DEPLOYMENT.md)。

## 数据准备

在忽略的 `.env` 中配置 `RQ_USER`、`RQ_PASSWORD`、`RQ_HOST`。RQ 使用 SDK 直连配置的地址。应用只有 `runtime` 数据档案，不会把内置测试样本作为真实研究数据回退使用。

默认数据目录为 `data/runtime/`。独立部署通过启动进程的 `ALPHALAB_RUNTIME_DIR` 指定持久目录，必须在 Python 导入应用前设置；每个实例使用独立数据库。

先查看模板和计划，再同步选定范围。以下是 Windows 的小范围示例；日期是历史示例，不代表最新行情：

```powershell
.\.venv\Scripts\python.exe -m alphalab.cli data templates
.\.venv\Scripts\python.exe -m alphalab.cli data plan rq --template rq.a_share_daily --symbols 000001.SZ --start 2025-01-01 --end 2025-12-31
.\.venv\Scripts\python.exe -m alphalab.cli data sync rq --template rq.a_share_daily --symbols 000001.SZ --start 2025-01-01 --end 2025-12-31
.\.venv\Scripts\python.exe -m alphalab.cli data validate --datasets rq.bars,rq.paused,rq.is_st --start 2025-01-01 --as-of 2025-12-31 --fail-on-gap
```

省略 `--symbols` 会按模板解析完整范围；默认研究模板包含更多财务与因子数据。上述 CLI 校验面向所选数据集中的本地数据，不是仅按前一条同步命令的股票过滤。大范围同步、强制回补、财报口径和历史成分限制见 [数据操作](docs/04_DATA_OPERATIONS.md)。已有旧财务缓存需要显式重建才能采用修正后的 ROE/TTM 口径。

## 可选的 Conexus Agent

Conexus 是可选的 Agent 运行服务。AlphaLab 后端代理其接口，项目源码、数据和回测仍由 AlphaLab 保存与执行；研究报告 Documents 由 Conexus 持久化，会话文本保存在 AlphaLab。

每个独立实例需匹配自己的数据库、实例标识、Conexus slug、服务凭据和工具目标地址。服务身份凭据与模型付费授权是两项配置；接口可读不代表 Agent 已能调用模型。

功能与工具契约见 [Conexus Agent](docs/04_CONEXUS_AGENT.md)，连接参数、实例绑定和模型授权见 [接入配置](deploy/conexus-cloud/README.md)。

## 研究结果的边界

技术形态、因子诊断和组合方法是研究工具，效果需要在明确的样本、调仓与成本假设下检验。比例权重回测与整手现金账户的收益不可直接比较。质量组合复现实验及其限制见 [0.6.2 研究记录](docs/releases/0.6.2-dev-liu.md)。

Python 源码在本地子进程中执行，有超时、日志和契约检查，但不是安全沙箱。研究时使用可信源码，并在结论中说明股票池、数据可用时点、缺失值、交易约束与成本假设。

## 验证与贡献

```powershell
.\.venv\Scripts\python.exe -m pytest tests/contracts -q --basetemp=artifacts/pytest-contracts
.\.venv\Scripts\python.exe scripts/check_facade_imports.py
.\.venv\Scripts\python.exe scripts/check_repository_hygiene.py
npm --prefix dashboard/frontend run lint
npm --prefix dashboard/frontend test
npm --prefix dashboard/frontend run build:web
```

按变更范围补充测试；完整规则见 [开发指南](docs/03_DEVELOPMENT_GUIDE.md)。内置样本用于测试，个人数据、运行数据库、凭据、构建产物和研究附件留在 Git 之外。
