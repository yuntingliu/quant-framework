# Conexus 接入配置

AlphaLab 可连接兼容的 Conexus Web Host，将研究 Agent 作为独立的 hosted Harness 运行。Conexus 可以与 AlphaLab 同机部署，也可以通过受控网络访问；部署位置不改变项目和回测的规范接口。

Agent 行为见 [功能契约](../../docs/04_CONEXUS_AGENT.md)，AlphaLab 的构建与持久化见 [通用部署](../../docs/05_DEPLOYMENT.md)。

## 连接关系

```text
浏览器 → AlphaLab 后端 → Conexus Web Host → 模型服务
                              │
                              └→ AlphaLab 工具接口 → 项目、数据、回测
```

浏览器通过 AlphaLab 后端访问 Conexus。服务凭据保存在后端，项目执行继续使用 AlphaLab 的现有进程和任务队列。研究报告由 Conexus workspace 持久化，会话文本由 AlphaLab 保存。

## 配置与凭据

| 配置 | 所在位置与用途 |
| --- | --- |
| `CONEXUS_WEB_ORIGIN` | AlphaLab 后端；Conexus 服务地址 |
| `CONEXUS_PUBLICATION_SLUG` | AlphaLab 后端；匹配要使用的 hosted Harness |
| `CONEXUS_PUBLICATION_WORKSPACE_TOKEN` | AlphaLab 后端；该 publication 的企业服务身份 |
| `ALPHALAB_INSTANCE_ID` | AlphaLab 后端及工具注册；实例一致性检查 |
| `ALPHALAB_TOOL_API_ORIGIN` | 工具注册；从 Conexus 运行环境可访问的 HTTP 回环转发地址 |

企业服务身份凭据负责请求认证；模型提供商或 publisher funding 配置负责模型调用，两者分别配置。使用 Conexus 账户付费时，需要为相应 slug 配置有效授权；不能把其他 slug 的授权视为可复用的通用凭据。共享服务的全局模型配置变更会影响其他使用方。

## 注册与实例绑定

注册脚本 `scripts/register_conexus_research_harness.mjs` 生成 Canvas 和工具资源。独立发布可使用：

- `CONEXUS_CANVAS_PATH`：独立 Canvas 文件。
- `ALPHALAB_STAGED_BUNDLE_PATH`：独立 staged bundle 目录。
- `ALPHALAB_TOOL_API_ORIGIN`：固定工具 origin。
- `ALPHALAB_INSTANCE_ID`：预期实例标识。

绑定后的工具在执行命令前检查 `/api/agent/identity`；共享主机的全局 API origin 不会替换已绑定地址。复制网页配置时也要核对工具所指向的实例，防止 Agent 与网页读写不同数据库。

使用兼容 Conexus 版本的管理员 `harness:host` 操作创建 hosted revision，并设置匹配的 slug、enterprise identity 和 publisher billing。默认本地 Canvas 的辅助命令与版本要求见 [注册说明](../../docs/04_CONEXUS_AGENT.md#registration-and-hosting)。

## 受控网络访问

同机部署使用回环访问。跨主机时，可通过受限 SSH 转发或私有网络连接；端口、账号、密钥与路由由环境配置决定。

`scripts/serve_conexus_private_api.py` 提供到现有认证后端的回环转发，可通过 `--port` 和 `--backend-port` 指定端口。它读取 `--env-file` 中 JSON 引号包围的配置值，并在本机注入网页认证；浏览器密码不进入工具 bundle。运行该转发时，不再另起一个共享同一数据库的任务执行后端。

转发地址应仅对受信任的 Conexus 运行环境可达。升级转发代码后重启对应进程，再校验实例标识。

## 验证与迁移

1. 确认 manifest 和 workspace 可以通过预期服务身份读取。
2. 确认工具返回的实例标识、项目和冻结结果与网页一致。
3. 完成一次只读 Agent 运行，验证模型接入、事件流、状态和交付。
4. 重启后确认所需报告与会话仍可读取。

迁移时分别处理 AlphaLab 数据库和 Conexus workspace，并验证目标实例后再切换配置。若创建运行返回 `public_runtime_unavailable`，应检查具体错误和模型授权，不能仅凭 manifest 成功判定服务可用。
