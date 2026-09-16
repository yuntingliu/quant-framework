# 通用部署

本页说明从源码构建和持续运行 AlphaLab 的通用要求。首次启动见 [README](../README.md)，开发热更新见 [开发指南](03_DEVELOPMENT_GUIDE.md)，Linux 服务示例见 [Linux 部署](guides/linux-deployment.md)。

## 构建与启动

在仓库根目录准备 Python 虚拟环境，并使用 `.node-version` 指定的 Node.js 主版本：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dashboard,dev,rq]'
npm --prefix dashboard/frontend ci
npm --prefix dashboard/frontend run build:web
.venv/bin/python -m uvicorn dashboard.backend.main:app --host 127.0.0.1 --port 8000 --workers 1
```

Windows 将 Python 路径替换为 `.\.venv\Scripts\python.exe`。构建后的页面位于 `/app/`。使用单个后端 worker，使后台任务和进程内状态保持一致；持续运行时由操作系统服务管理器负责启动和重启。

## 配置

| 配置 | 用途 |
| --- | --- |
| `ALPHALAB_RUNTIME_DIR` | 持久运行目录；默认 `data/runtime/`，应在进程启动前设置 |
| `ALPHALAB_ENV_FILE` | 指定本地配置文件；进程已有环境变量优先 |
| `ALPHALAB_WEB_AUTH_ENABLED` | 启用网页 HTTP Basic 认证 |
| `ALPHALAB_WEB_USERNAME`、`ALPHALAB_WEB_PASSWORD` | 网页认证凭据 |
| `RQ_USER`、`RQ_PASSWORD`、`RQ_HOST` | RQ 数据访问配置 |
| `ALPHALAB_INSTANCE_ID` | 使用独立 Agent 工具绑定时的实例标识 |
| `CONEXUS_WEB_ORIGIN`、`CONEXUS_PUBLICATION_SLUG`、`CONEXUS_PUBLICATION_WORKSPACE_TOKEN` | 可选 Conexus 接入配置 |

运行目录与源码发布目录分开。环境文件、凭据和运行数据不进入 Git。对外访问由 TLS 反向代理或隧道转发到回环地址，并保留 WebSocket 与事件流支持；网页认证也必须覆盖 API 访问。

## 持久化

| 内容 | 位置 |
| --- | --- |
| 项目、源码包、冻结回测 | `runtime/app/alphalab.db` |
| 数据配方、同步任务、分区登记 | `runtime/app/dataio.db` |
| Agent 会话与研究检查点 | `runtime/app/agent-conversations.sqlite3` |
| 行情、财务及派生数据 | runtime 下的数据分区 |
| 可重建的语言服务镜像 | `runtime/editor/` |
| Conexus 报告 Documents | Conexus 自己的持久 workspace |

表中的 `runtime` 指配置后的运行目录。每个独立实例使用自己的可写数据库。迁移 SQLite 使用在线备份或停机后一致性备份；Conexus 报告需要单独迁移。应用升级不会自动同步两个实例的数据。

## 更新流程

1. 选择明确的源码提交，在独立目录安装依赖并构建。
2. 运行 [开发指南](03_DEVELOPMENT_GUIDE.md) 要求的检查。
3. 确认后台任务状态，备份数据库和需要保留的研究数据。
4. 切换至已验证的发布目录，重启相关服务。
5. 核对应用版本、数据访问和需要启用的 Agent 能力；失败时回退代码发布。

发布目录、服务配置、域名、隧道和回滚记录由部署环境管理。回退代码不应覆盖更新后产生的研究数据。

## 运行检查

先检查应用部署，再检查所需的外部服务。空数据目录能够启动应用，但不具备真实行情研究条件；RQ 和 Conexus 的可用性需要分别验收。

| 检查入口 | 含义 |
| --- | --- |
| `/api/health` | `status: "ok"`、`frontend: "ready"`、版本和结果库状态；不扫描完整数据覆盖 |
| `/app/` | 页面及脚本、样式资源可加载，可新建项目并打开编辑器 |
| `/api/data/providers`、`/api/data-sync/health` | 数据目录、新鲜度和同步状态 |
| `/api/python-editor/capabilities` | Python 编辑器工具是否可用 |
| `/api/agent/identity` | 工具请求是否命中预期实例 |
| 完整的只读 Agent 运行 | 同时验证模型、工具、事件流和结果交付 |

启用认证后应使用认证请求检查受保护接口。数据可访问仍需按研究区间检验覆盖；Conexus manifest 可读也不能替代完整运行检查。接入细节见 [Conexus 配置](../deploy/conexus-cloud/README.md)。

## 部署验收标准

| 层次 | 实际验收动作 |
| --- | --- |
| 安装与构建 | 从指定 Git 提交和空虚拟环境安装；执行 `python -m pip check`，使用 Node 22 和 `npm ci` 构建网页 |
| 应用运行 | 打开网页，新建可编辑项目；启用认证时，匿名页面与 API 请求返回 401，正确认证后可以访问 |
| 编辑器 | 打开 Python 文档，验证 Pyrefly 和 Ruff 的 WebSocket 初始化与诊断；确认镜像写入配置的 `runtime/editor/` |
| 持久化与更新 | 保存项目后重启或更新服务，核对项目源码仍一致；运行目录独立于发布目录 |
| 数据研究 | 配置有权限的 RQ 账户，完成少量标的同步、区间覆盖检查与一次回测 |
| 可选 Agent | 配置 Conexus 后完成一次只读运行，检查工具目标、模型调用、事件流和文档持久化 |

自动测试验证接口与执行规则，不能替代真实安装和服务启动；健康接口通过也不代表后两层已经通过。维护者应把所测提交、Python/Node 版本、结果和未验证项保存在验收记录中。主机名、域名、凭据和现场日志由部署环境保存，不进入项目文档。
