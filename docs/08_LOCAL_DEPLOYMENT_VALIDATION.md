# 本地部署验证记录

验证日期：2026-09-09。修复基于 `dev_liu` 的 `17abe16`。
操作说明见 [本地部署](07_LOCAL_DEPLOYMENT.md)。

## 环境与结果

本机验证环境：Windows、Python 3.12.6、Node.js 24.14.0、npm 11.9.0。
Python 依赖在独立虚拟环境全新安装，包括 NumPy 2.5.3、Pandas 2.3.3、
FastAPI 0.141.1、Uvicorn 0.52.4、Pyrefly 1.2.0、Ruff 0.16.6。

| 验证项 | 结果 |
| --- | --- |
| Python 完整测试 | 238 passed，6 条第三方库弃用警告 |
| 前端测试 | 9 passed |
| Python Ruff、编译、公共接口、仓库卫生检查 | 通过 |
| ESLint、TypeScript、浏览器构建 | 通过 |
| Electron 源码构建 | 通过；未生成或验证桌面安装包 |
| npm 全量审计 / 生产依赖审计 | 均为 0 vulnerabilities |
| `pip check` | 无依赖冲突 |
| Conexus JSON | 16 个文件均可解析 |
| 全新运行数据目录 | 自动初始化系统模板；无历史项目污染 |
| HTTP 与 WebSocket | `/app/`、API、Pyrefly / Ruff 初始化及退出通过 |
| 浏览器操作 | Edge 无头模式验证首页、Logo、新建项目、进入数据工作台及 Monaco 编辑器，无页面运行异常 |
| 数据隔离 | 完整测试前后，本地数据库与历史样本 SHA-256 均保持不变 |
| 部署包 | 校验逐文件哈希、检查无本地用户状态；解压后从包内源码启动，网页、API、新数据库和编辑器能力检查通过 |

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
5. 补齐 WebSocket 依赖，增加 `alphalab dev serve` 单服务启动入口，修复
   `/app/` 部署前缀下 Logo 路径，提供包含网页构建产物的部署包生成命令。

## 验证边界

- macOS Intel / Apple Silicon 尚未在实机验证。CI 已增加 macOS 与 Windows 的
  Python 3.12 检查，但本地修改尚未触发远端 CI；仍需学生按部署指南复测。
- 无本次部署使用的 RQ 凭据，未连接真实 RQData 下载数据或进行真实数据回测；
  未配置 Conexus，未验证外部 Agent 服务。
- NumPy 2.5 引入 generic timedelta 弃用，Pandas 内部拼接仍触发 4 条，
  Starlette / AnyIO 触发另 2 条警告。没有屏蔽这些警告。
  参见 [NumPy 时间类型文档](https://numpy.org/doc/stable/reference/arrays.datetime.html)。
- npm 安装仍可能报告 Electron 工具链的 glob、rimraf、inflight、boolean，以及
  ESLint 9 的弃用提示。当前审计无已报告漏洞，弃用提示仍需后续维护。
- Monaco 编辑器构建文件较大，Vite 仍提示大文件；编辑器按需加载。
