# AlphaLab 文档导航

当前软件版本为 0.6.2；本导航于 2026-09-11 对照源码和部署验收记录更新。网站运行版本以 `/api/health` 的 `version` 和 `commit_sha` 为准，分支最新提交与已部署提交可能不同。

## 使用与研究

| 文档 | 内容 |
| --- | --- |
| [项目 README](../README.md) | 能力总览、Windows/macOS/Linux 启动、数据准备、已知边界 |
| [中文 SDK 指南](06_ALPHALAB_SDK_GUIDE.md) | 网页文档面板的同一来源；策略、因子、数据、验证和技术证据示例 |
| [数据操作](04_DATA_OPERATIONS.md) | RQ 模板、同步、续跑、PIT 财务、覆盖校验与失败语义 |
| [Conexus Agent](04_CONEXUS_AGENT.md) | 项目读写、研究工具、会话、报告和运行结果交付契约 |

## 开发与部署

| 文档 | 内容 |
| --- | --- |
| [架构](01_ARCHITECTURE.md) | 源码归属、公共接口、工作台、编辑器与执行边界 |
| [策略 SDK 契约](02_STRATEGY_SDK_V1_CONTRACT.md) | 事件、日程、输入输出、状态和回测规则 |
| [数据 SDK 契约](05_DATA_SDK_V1_CONTRACT.md) | 自定义数据源和统一 DataEngine 接口 |
| [开发指南](03_DEVELOPMENT_GUIDE.md) | 安装、改动位置、前后端一致性和质量检查 |
| [部署与实例](05_DEPLOYMENT.md) | dev2/dev3、Mac LaunchAgents、运行目录和验收 |
| [Linux 与 Cloudflare](05_LINUX_CLOUDFLARE.md) | Linux systemd、独立发布目录和隧道操作 |
| [Conexus 部署](../deploy/conexus-cloud/README.md) | 共享服务、实例工具绑定、服务身份和模型授权 |

## 版本与验收

- [2026-09-11 工作台与部署更新](releases/2026-09-11-workstation.md)：远端分支、行情性能、项目路由和仍待完成的验收。
- [0.6.2](releases/0.6.2-dev-liu.md)：PIT 财报修正与质量组合股票级复现。
- [0.6.1](releases/0.6.1-dev-liu.md)：技术证据、形态因子和讲义年度表格审计。
- [0.6.0](releases/0.6.0-integration.md)：dongfang/dev_liu 整合与初始质量检查。

历史发布记录描述各自发布时的状态；后续推送和部署进展以日期更新记录为准。测试数量和性能数字属于注明版本及环境的验收记录，不是当前任意环境的承诺。

## 文档维护约定

功能或接口改变时更新对应契约；入口、运行方式或部署改变时同时更新 README 和部署说明。新版本保留历史研究结论的口径和限制。修复后再将已知问题标记为完成，并记录验证方式。源码说明中不包含凭据、机器私钥或个人数据附件。
