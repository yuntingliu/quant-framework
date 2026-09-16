# Linux 服务部署

本示例使用 systemd 用户服务管理单个 AlphaLab 后端。构建、认证和数据持久化的通用要求见 [部署说明](../05_DEPLOYMENT.md)。

## 目录与配置

发布脚本的默认目录均位于运行用户的主目录，可以通过环境变量覆盖：

| 变量 | 默认值 |
| --- | --- |
| `ALPHALAB_SOURCE_DIR` | `$HOME/src/quant-framework` |
| `ALPHALAB_RELEASES_DIR` | `$HOME/releases` |
| `ALPHALAB_CURRENT_LINK` | `$HOME/apps/quant-framework` |
| `ALPHALAB_RUNTIME_DIR` | `$HOME/.local/share/alphalab/runtime` |
| `ALPHALAB_ENV_FILE` | `$HOME/.config/alphalab/alphalab.env` |
| `ALPHALAB_SERVICE_NAME` | `alphalab.service` |
| `ALPHALAB_HEALTH_URL` | `http://127.0.0.1:8000/api/health` |
| `ALPHALAB_STATE_DIR` | `$HOME/.local/state/alphalab` |
| `ALPHALAB_BACKUP_DIR` | `$HOME/backups/alphalab` |

配置文件由运行用户读取，文件权限设为 `0600`。按需设置网页认证、RQ 和 Conexus 配置。若调整运行目录，发布脚本与服务进程必须使用同一个绝对路径。

## 用户服务示例

在用户 systemd 配置目录创建 `alphalab.service`，根据所选目录调整：

```ini
[Unit]
Description=AlphaLab workstation
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=%h/apps/quant-framework
EnvironmentFile=%h/.config/alphalab/alphalab.env
Environment=ALPHALAB_RUNTIME_DIR=%h/.local/share/alphalab/runtime
ExecStart=%h/apps/quant-framework/.venv/bin/python -m uvicorn dashboard.backend.main:app --host 127.0.0.1 --port 8000 --workers 1
Restart=on-failure
RestartSec=5
UMask=0077

[Install]
WantedBy=default.target
```

`%h` 由 systemd 展开为运行用户的主目录。需要退出登录后继续运行时，由管理员为该运行用户配置 lingering。

## 发布脚本

先准备好配置文件、用户服务以及 `ALPHALAB_SOURCE_DIR` 下的 Git checkout，并确认该 checkout 的 `origin` 指向目标仓库。加载服务定义后，在源码目录执行：

```bash
systemctl --user daemon-reload
bash scripts/deploy_linux.sh origin/main
```

脚本获取指定 ref 对应的提交，在独立目录构建 Web 和 Python 环境、运行检查，再切换当前发布符号链接、重启服务并校验认证后的健康接口。失败时恢复此前的有效发布；首次发布失败则停止新服务。

构建默认设置 4 GiB Node 堆上限，可通过 `NODE_OPTIONS` 调整。脚本依赖 Linux 的 systemd、flock 和 GNU 文件工具，不能直接作为其他操作系统的安装器。

脚本目前自动备份 `app/alphalab.db`。完整迁移或恢复还需自行备份 `dataio.db`、`agent-conversations.sqlite3`、研究分区及 Conexus workspace；自动回滚代码不会替代完整数据备份。

## 运行管理

```bash
systemctl --user status alphalab.service
journalctl --user -u alphalab.service
```

服务名改变时同步调整命令。外部访问由部署环境选择 TLS 反向代理或隧道，保留 WebSocket 和事件流转发。域名、SSH 路由、访问策略及主机服务文件在环境中维护。
