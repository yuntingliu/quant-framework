# 部署与开发实例

本页记录 2026-09-11 最近验收的部署结构，并提供现有实例的操作入口。每个实例的当前源码提交以其认证后的 `/api/health` 为准；同为 0.6.2 不代表运行同一提交。

## 实例地图

| 实例 | 入口 | 宿主与后端 | 说明 |
| --- | --- | --- | --- |
| 本地开发 | http://localhost:5173 | Vite → 本地 8000 | 源码开发；启动见 [README](../README.md) |
| dev2 | https://dev2.acetoken.net | Pop!_OS，127.0.0.1:8200 | 独立 runtime 与 systemd 用户服务 |
| dev3 | https://dev3.acetoken.net | Mac mini，127.0.0.1:8300 | 独立 runtime、LaunchAgents 和 Mac 自己的 Cloudflare Tunnel |

dev3 最近验收的运行提交为 `ef97e8e`；后续文档提交不会自动重启服务。dev2 的版本需单独查询，不能从 dev3 的验收推断。网页配置了 HTTP Basic 登录，直接无凭据探测返回 401 是预期行为。

## 持久数据与发布目录

源码 checkout、按提交生成的 release、当前 release 链接、runtime、配置和日志分开保存。升级只切换已验收的 release；研究数据留在 runtime。进程启动前设置 `ALPHALAB_RUNTIME_DIR`，不要依赖应用导入之后再改变路径。

| 位置 | 内容 |
| --- | --- |
| `runtime/app/alphalab.db` | 项目、策略和验证源码包、冻结回测等 |
| `runtime/app/dataio.db` | 数据配方、同步任务与分区登记等 |
| `runtime/app/agent-conversations.sqlite3` | AlphaLab 会话文本与研究检查点 |
| runtime 中的研究分区 | 行情、状态、财务和派生数据 |
| `runtime/editor/` | 可重建的语言服务镜像，不是规范源码 |
| Conexus workspace | 原生报告 Documents；不在上述三个数据库中 |

迁移数据库使用 SQLite 在线备份并核对记录；复制实时 `.db` 文件时不能忽略 WAL。报告需通过 Conexus workspace 单独迁移。每个独立实例持有自己的可写数据库；代码升级不等于持续双向同步数据。

## Mac mini / dev3

已安装环境使用 Python 3.12、Node.js 22 和一个 Uvicorn worker。以下路径相对 Mac 的部署用户目录：

```text
~/src/quant-framework-dev3                       源码 checkout
~/releases/alphalab-dev3/<commit-sha>             已构建 release
~/apps/alphalab-dev3                             当前 release 符号链接
~/.config/alphalab-dev3/alphalab.env              主机配置（0600）
~/.local/share/alphalab-dev3/runtime              持久研究数据
~/.local/state/alphalab-dev3                      构建日志、服务日志、备份
~/Library/LaunchAgents/net.acetoken.alphalab-dev3*.plist
```

现有部署用户会话中可以只读查看服务：

```bash
launchctl print "gui/$(id -u)/net.acetoken.alphalab-dev3"
launchctl print "gui/$(id -u)/net.acetoken.alphalab-dev3-tunnel"
launchctl print "gui/$(id -u)/net.acetoken.alphalab-dev3-agent-api"
launchctl print "gui/$(id -u)/net.acetoken.alphalab-dev3-agent-tunnel"
```

它们分别管理网页后端、公网隧道、私有 Agent 转发和 Agent SSH 隧道。LaunchAgents 属于部署用户会话；不要把它们描述为无需用户会话的系统 LaunchDaemons。

现有 Mac 已安装主机专用的 `~/.local/bin/alphalab-dev3-deploy`，它调用 `~/.config/alphalab-dev3/deploy.py`。这些是主机本地运维工具，不是本仓库提供的通用 macOS 安装器。对这台已配置的 Mac，更新入口为：

```bash
~/.local/bin/alphalab-dev3-deploy origin/main
```

更新前检查运行中的数据同步和回测任务。部署工具创建独立 release，完成依赖、前端与后端检查，备份三个 SQLite 数据库，原子切换链接并验收认证后的健康接口；失败则恢复前一链接。构建日志保存在 state 目录。

主部署工具重启网页后端。若更新了 `serve_conexus_private_api.py`，还需在切换后重启 `net.acetoken.alphalab-dev3-agent-api` 并重新验证实例标识；不要假定主服务重启会连带更新独立转发进程。Cloudflare 和 SSH 隧道配置、私钥、登录/RQ/Conexus 凭据留在主机上。

Linux 实例的安装和部署脚本见 [Linux 与 Cloudflare](05_LINUX_CLOUDFLARE.md)。其中旧单实例路径是示例，dev2 使用自己的端口、环境文件和服务名。

## Conexus 接入与状态

当前 dev3 仍连接共享的 https://adminer.cloud，使用独立 slug `alphalab-research-agent-dev3`。工具路径为：

```text
Conexus 工具 → 香港 loopback 18003 → SSH → Mac loopback 8301
                                                    ↓ 本地认证转发
                                            AlphaLab loopback 8300
```

发布工具内固定目标 origin，并先校验 `/api/agent/identity` 返回 `dev3`。仅复制网页环境变量不足以迁移 Agent；旧工具若仍访问另一台机器，就会出现“Agent 已创建，网页却找不到项目”的问题。

dev3 的项目及原冻结回测已恢复，11 份历史报告已复制并核对。工具读取已验收；**新 Agent 完整运行尚未通过验收**，目前错误为 `public_runtime_unavailable`，原因是新 slug 的模型授权未配置。企业服务身份凭据负责接口访问，不等于模型付费凭据。

Conexus 与 AlphaLab 同机部署是候选方案。现有 Conexus 源码提供 Web Host；用户提到的 `local` 分支尚未完成源码审查，不能宣布可直接替换。候选环境必须验证运行/SSE/取消/workspace 接口、模型调用、报告迁移及重启持久化后再切换。详细规则见 [Conexus 部署](../deploy/conexus-cloud/README.md)。

## 验收与排障

| 现象或检查 | 判读与下一步 |
| --- | --- |
| `/api/health` 为正常 | 应用、版本和结果库可访问；数据覆盖仍是 `not_checked` |
| 行情是否完整/新鲜 | 查看 `/api/data/providers`、`/api/data-sync/health`，再按数据集和区间校验 |
| `strategy project not found` | 核对网页数据库、工具 origin、实例标识与项目 ID 是否一致 |
| Agent manifest 可读但启动 503 | 检查新 slug 的模型授权；不能把 manifest 成功当作完整运行成功 |
| `正在加载 Python 编辑器…` | 浏览器仍在加载编辑器模块；检查静态资源传输和缓存，再检查语言服务 |
| 编辑器可见但无补全/格式化 | 检查 `/api/python-editor/capabilities`、Pyrefly/Ruff 和语言服务 WebSocket |

截至最近一次数据验收，dev3 行情最新日期为 2026-08-24，实时行情未配置；这是一份历史状态，后续以数据接口为准。2026-09-11 的一次公网测量中，编辑器 JavaScript 压缩传输约 2.98 MB，耗时 16.4 秒；本机读取约 41 毫秒。该样本来自 Mac 对公网域名的请求，不是用户浏览器测量，编辑器传输优化尚未完成。
