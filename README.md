# AlphaLab

AlphaLab 是面向 Python 量化研究的工作台：将数据配方、因子、选股策略和验证代码放在同一个项目中，通过统一的日度事件引擎生成可追溯的回测结果。当前版本为 **0.6.2**。

适合需要检查因子定义、数据时点、交易规则和回测证据的研究者。内置 RQ 数据接入，也可通过 Data SDK 扩展数据源。

## 研究流程

```text
数据准备 → 因子研究 → 策略选股与组合 → 回测与验证 → 研究报告
```

| 工作台 | 用途 |
| --- | --- |
| 项目 | 创建研究项目，管理规范源码和不可变版本 |
| 数据 | 编辑、运行数据配方，检查同步进度与数据覆盖 |
| 因子 | 编写因子，查看截面快照、历史 Rank IC 等诊断 |
| 策略 | 定义股票池、调仓日程、信号、组合与执行规则 |
| 验证 | 运行回测，检查收益、交易约束和冻结的源码及结果 |
| 报告 | 通过可选的 Conexus Agent 整理并保存研究文档 |

一个项目包含 `recipe.py`、`factors/<factor_id>.py`、`strategy.py` 和 `validation.py`。表单和编辑器修改同一份规范源码，保存时记录版本，回测固定策略与验证版本。日度引擎支持日、周、月及自定义调仓日程；实际成交还取决于目标权重、交易约束和执行条件。

## 快速启动

需要 Git、Python >= 3.10 和 Node.js 22（见 [`.node-version`](.node-version)）。以下命令构建网页并由一个后端进程提供服务；启动界面不需要 RQ 或 Conexus 凭据。Python 的 `dev` 依赖包含编辑器使用的 Pyrefly 和 Ruff。

```bash
git clone https://github.com/yuntingliu/quant-framework.git
cd quant-framework
```

### Windows PowerShell

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dashboard,dev,rq]"
npm --prefix dashboard/frontend ci
npm --prefix dashboard/frontend run build:web
.\.venv\Scripts\python.exe -m uvicorn dashboard.backend.main:app --host 127.0.0.1 --port 8000 --workers 1
```

### macOS / Linux

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dashboard,dev,rq]'
npm --prefix dashboard/frontend ci
npm --prefix dashboard/frontend run build:web
.venv/bin/python -m uvicorn dashboard.backend.main:app --host 127.0.0.1 --port 8000 --workers 1
```

打开 [AlphaLab 工作台](http://127.0.0.1:8000/app/)。[健康检查](http://127.0.0.1:8000/api/health) 应返回 `status: "ok"` 和 `frontend: "ready"`。首次启动没有行情缓存，需要完成下一节的数据准备。若要修改前端并使用热更新，见 [开发指南](docs/03_DEVELOPMENT_GUIDE.md)。

## 完成第一次研究

1. 在「项目」中新建项目。内置项目是只读模板，可用来了解代码结构。
2. 在仓库根目录的 `.env` 中配置 `RQ_USER`、`RQ_PASSWORD`、`RQ_HOST`，然后重启后端。需要具有相应数据权限的 RQ 账户。
3. 在「数据」中选择模板，先填写少量标的，日期覆盖因子回看窗口和待测区间，保存代码后点击「运行并同步」。确认任务完成且所需数据有覆盖；标的留空会使用模板的完整范围。
4. 在「因子」和「策略」中查看、修改并保存代码，选择调仓日程、组合和执行规则。
5. 在「验证」中运行覆盖已准备数据区间的回测，检查交易明细和验证结果，再扩大样本。

数据权限、同步范围、财务口径及历史成分限制见 [数据操作](docs/04_DATA_OPERATIONS.md)；源码结构、可运行示例和研究命令见 [中文 SDK 指南](docs/06_ALPHALAB_SDK_GUIDE.md)。网页内的 SDK 文档面板读取同一份指南。

## 部署与可选服务

默认运行数据保存在 Git 忽略的 `data/runtime/`。持续运行时应将数据库、缓存和编辑器临时文件放入独立的持久目录，通过启动环境中的 `ALPHALAB_RUNTIME_DIR` 指定。认证、配置、备份与更新见 [通用部署](docs/05_DEPLOYMENT.md)；Linux 的发布脚本和服务管理见 [Linux 部署](docs/guides/linux-deployment.md)。

Conexus Agent 是可选服务，需要兼容的 Conexus Web Host、服务身份以及有效的模型调用授权。手动编辑代码、同步数据和运行回测不依赖 Agent。接入要求见 [Conexus 配置](deploy/conexus-cloud/README.md)。

## 使用边界

技术形态、因子诊断和组合方法是研究工具；支持某项功能不代表已经证明收益改善。比较结果时需要统一股票池、数据时点、调仓、交易成本和账户模型。

项目 Python 代码在本地子进程中执行，有超时、日志和契约检查，但不是安全沙箱。仅执行可信源码；凭据、个人数据、运行数据库和生成的研究附件不要提交到 Git。

## 进一步阅读

- [文档导航](docs/README.md)：按使用、开发和部署查找说明。
- [架构](docs/01_ARCHITECTURE.md)：模块边界与公开接口。
- [开发指南](docs/03_DEVELOPMENT_GUIDE.md)：开发环境与按变更范围选择检查。
- [贡献指南](CONTRIBUTING.md)：提交与文档维护约定。
