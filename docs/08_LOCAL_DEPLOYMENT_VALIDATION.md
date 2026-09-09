# 本地部署验证记录

验证日期：2026-09-09。部署修复基于 `dev_liu` 的 `17abe16`；
本地 Agent 使用 Conexus Core `dcc74445382d73be7f1ff06b7bde147c95e7240d`。
操作说明见 [本地部署](07_LOCAL_DEPLOYMENT.md)。

## 环境与结果

本机验证环境：Windows、Python 3.12.6、Node.js 24.14.0、npm 11.9.0。
Python 依赖在独立虚拟环境全新安装，包括 NumPy 2.5.3、Pandas 2.3.3、
FastAPI 0.141.1、Uvicorn 0.52.4、Pyrefly 1.2.0、Ruff 0.16.6。

| 验证项 | 结果 |
| --- | --- |
| Python 完整测试 | 240 passed，6 条第三方库弃用警告；含真实本地 Agent 工具调用与重启持久化 |
| 前端测试 | 9 passed |
| Python Ruff、编译、公共接口、仓库卫生检查 | 通过 |
| ESLint、TypeScript、浏览器构建 | 通过 |
| Electron 源码构建 | 通过；未生成或验证桌面安装包 |
| npm 全量审计 / 生产依赖审计 | 均为 0 vulnerabilities |
| `pip check` | 无依赖冲突 |
| Conexus 核心 | 独立源码安装、构建通过；188 个核心测试、13 个 OS 适配器测试、4 个本地 Host 测试通过 |
| 核心边界 | 仅 runtime-protocol、runtime-core、node-host-runtime、local-host；静态依赖检查通过 |
| Conexus 生产依赖审计 | 0 vulnerabilities；不含企业 Web Host 或 Canvas UI |
| Agent 生命周期 | 本地模型可不配置云端密钥；交互回答、取消、中断恢复、单目录占用、报告跨升级保留均通过 |
| 全新运行数据目录 | 自动初始化系统模板；无历史项目污染 |
| HTTP 与 WebSocket | `/app/`、API、Pyrefly / Ruff 初始化及退出通过 |
| 浏览器操作 | 原工作台验证通过；新增 Edge 无头浏览器从解压包发送研究请求，经过 3 次受控模型响应调用 Python 工具并保存和显示报告，无页面运行异常 |
| 数据隔离 | 完整测试前后，本地数据库与历史样本 SHA-256 均保持不变 |
| 部署包 | 校验逐文件哈希；不含本地用户状态、node_modules、企业代码或 Canvas UI；从包内源码启动并安装本平台依赖，网页、Python API、本地 Agent 连通 |

## 学生反馈对应修复

1. `data/app/alphalab.db` 移出 Git；保留冻结历史样本用于复制后执行迁移测试。
   测试收集前设置独立应用、缓存和运行数据目录，子进程继承相同隔离设置；
   编辑器镜像也遵守运行目录配置。
2. 首页显示“源码检查通过”，另行读取和展示行情同步状态，不据此承诺指定研究
   范围可执行；只读模板提供新建项目入口，再引导进入数据工作台。
3. AlphaLab 源码中的 `pd.Timedelta(days=...)` 改为显式 `unit="D"`，
   保持日期计算语义，消除源码触发的 generic timedelta 弃用警告。
4. Electron Builder 升至 26.15.3，刷新锁定依赖；全量审计由 21 个漏洞降为 0。
   浏览器成为默认开发与构建入口，桌面入口单独保留。
5. 补齐 WebSocket 依赖，增加 `alphalab dev serve` 单命令启动入口，修复
   `/app/` 部署前缀下 Logo 路径，提供包含网页构建产物的部署包生成命令。
6. 将企业版 Web Host 原型替换为独立 Conexus 本地 Host，自动管理端口、凭据和进程，
   模型和可选网页检索直接连接所配置服务。核心源码以提交及逐文件哈希固定，
   维护者通过白名单导出更新；构建器拒绝旧企业 Host 和未审查运行文件。

## 验证边界

- macOS Intel / Apple Silicon 尚未在实机验证。CI 已增加 macOS 与 Windows 的
  Python 3.12 检查，但本地修改尚未触发远端 CI；仍需学生按部署指南复测。
- 无本次部署使用的 RQ 凭据，未连接真实 RQData 下载数据或进行真实数据回测。
  Agent 验证使用本机可控 Chat Completions 响应，执行引擎和 Python 工具均为真实代码；
  未验证实际模型质量、远程模型服务、Brave 检索或外部企业 Agent 部署。
- Conexus 的独立核心已拆出并接入，Canvas 编辑器和企业版仍保留私有；核心许可证尚待
  所有者选择，本次产物为内部部署和开源审查候选版。未改变任何仓库可见性或推送远端。
- NumPy 2.5 引入 generic timedelta 弃用，Pandas 内部拼接仍触发 4 条，
  Starlette / AnyIO 触发另 2 条警告。没有屏蔽这些警告。
  参见 [NumPy 时间类型文档](https://numpy.org/doc/stable/reference/arrays.datetime.html)。
- npm 安装仍可能报告 Electron 工具链的 glob、rimraf、inflight、boolean，以及
  ESLint 9 的弃用提示。当前审计无已报告漏洞，弃用提示仍需后续维护。
- Monaco 编辑器构建文件较大，Vite 仍提示大文件；编辑器按需加载。
