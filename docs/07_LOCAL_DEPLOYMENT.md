# AlphaLab 本地部署（macOS / Windows）

本地浏览器版包含 Python 后端、策略运行环境和网页工作台。前端构建一次后，
日常使用只需启动一个 Python 服务，访问 `http://127.0.0.1:8000/app/`。
Conexus Agent 是可选的外部服务；未配置时仍可手动编辑项目、同步数据和运行回测。

## 1. 取得部署版本

使用老师提供的部署源码包或指定 Git 分支，解压到自己的目录。下文所有命令均从
项目根目录运行（能看到 `pyproject.toml`）。路径带空格时请加引号。
Git 用户可用 `git branch --show-current` 和 `git rev-parse --short HEAD` 记录版本。
不要用新版本目录覆盖旧目录里的 `.env`、`.venv` 或 `data`。

环境要求：Python 3.12；从源码构建网页时需要 Node.js 22（至少 22.18）或 24，及 npm。
仓库 `.node-version` 指定 Node 22。不要混用 Intel 和 Apple Silicon 的 Python、Node
与虚拟环境；切换架构后重新创建 `.venv` 和安装前端依赖。

若收到的部署包已经包含 `dashboard/frontend/dist/index.html`，可以跳过 Node 安装
和第 3 步。该网页构建产物可用于 macOS 和 Windows；Python 环境仍须在各自电脑创建。

## 2. 安装 Python 环境

macOS 终端：

```bash
python3.12 --version
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dashboard,dev,rq]"
python -c "import alphalab; print(alphalab.__version__)"
```

Windows PowerShell：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e ".[dashboard,dev,rq]"
.\.venv\Scripts\python.exe -c "import alphalab; print(alphalab.__version__)"
```

Windows 直接调用虚拟环境中的程序即可，无须更改 PowerShell 执行策略。
`dashboard` 包含 HTTP 和 WebSocket 服务依赖；`dev` 提供 Python 编辑器使用的
Pyrefly、Ruff 和测试工具；`rq` 安装 RQData 客户端。

## 3. 从源码构建网页

两个系统均可使用：

```bash
node --version
npm --version
npm --prefix dashboard/frontend ci
npm --prefix dashboard/frontend run build
```

`npm ci` 严格使用锁文件。`build` 默认只构建浏览器版。
若 Electron 下载失败，浏览器部署可跳过其二进制下载后重试安装：

```bash
# macOS
ELECTRON_SKIP_BINARY_DOWNLOAD=1 npm --prefix dashboard/frontend ci
```

```powershell
# Windows
$env:ELECTRON_SKIP_BINARY_DOWNLOAD = "1"
npm --prefix dashboard/frontend ci
Remove-Item Env:ELECTRON_SKIP_BINARY_DOWNLOAD
```

之后照常运行 `npm --prefix dashboard/frontend run build`。
需要桌面开发时，先安装 Electron 二进制
（`node dashboard/frontend/node_modules/electron/install.js`），再使用
`dev:desktop` / `build:desktop`。`pack` 为桌面打包命令，不是本地浏览器部署步骤。

## 4. 启动与停止

macOS（已激活 `.venv`）：

```bash
alphalab dev doctor
alphalab dev serve
```

Windows：

```powershell
.\.venv\Scripts\alphalab.exe dev doctor
.\.venv\Scripts\alphalab.exe dev serve
```

网页：<http://127.0.0.1:8000/app/>；API 文档：<http://127.0.0.1:8000/docs>。
浏览器、API 和 Python 编辑器 WebSocket 共用这个端口。
首次访问会自动创建本地数据库和系统策略模板。
终端按 `Control+C` / `Ctrl+C` 停止。下次使用只需进入目录，macOS 激活 `.venv`，
再运行 `alphalab dev serve`，不需要重新安装或构建。

若 8000 端口被其他程序占用，使用 `alphalab dev serve --port 8100`，并打开
`http://127.0.0.1:8100/app/`。不要为释放端口而结束不明进程。
服务默认只监听本机 `127.0.0.1`。

## 5. 安装成功与研究数据就绪是两件事

`doctor` 输出中的 `python`、`node`、`frontend`、`pyrefly`、`ruff` 帮助检查安装；
`rq` 和 `runtime_execution` 检查数据配置与基本数据集。
首次安装未配置 RQ 或尚未同步数据时，总状态 `degraded`、退出码 1 是预期现象，
请继续检查对应条目。已有网页构建的部署包不需要 Node；doctor 的 Node 缺失提示
也不影响该包启动。doctor 中数据集存在不保证特定研究范围的覆盖完整。

首页“源码检查通过”表示代码满足策略契约。首页单独展示行情是否已同步，
并提供“检查研究数据”入口。即使已有行情，评估和回测仍需要所选标的、日期、
预热区间与所需字段的数据；不会自动改用仓库示例数据。

在项目根目录把 `.env.example` 复制为 `.env`，本地填写 `RQ_USER`、`RQ_PASSWORD`、
`RQ_HOST`（由数据账号提供方给出），然后重启后端。凭据不应提交到 Git。
首次只有只读系统模板时，在首页点击“新建研究项目”并填写名称，创建自己的项目。
随后点击“检查研究数据”进入数据工作台，检查数据配方的标的、起止日期和数据集，再规划、同步。
首次建议用少量标的与较短研究区间，并为因子的回看窗口保留足够历史。
成功后再到因子或验证工作台执行。详见 [数据操作](04_DATA_OPERATIONS.md)。

出现 `Runtime data coverage is insufficient for this request.` 时，先检查该项目
数据配方是否同步成功、日期是否包含预热区间、标的和字段是否一致。
重新安装 Python 或反复保存策略不会补齐数据。

## 6. 测试与更新

macOS 激活虚拟环境后运行；Windows 将 `python` 替换为
`.\.venv\Scripts\python.exe`：

```bash
python -m pytest tests -q --basetemp=data/pytest
python scripts/check_facade_imports.py
python scripts/check_repository_hygiene.py
python -m ruff check alphalab dashboard/backend tests scripts
npm --prefix dashboard/frontend test
npm --prefix dashboard/frontend run lint
npm --prefix dashboard/frontend run build
npm --prefix dashboard/frontend audit
npm --prefix dashboard/frontend audit --omit=dev
git diff --check
git status --short
```

源码包未包含 Git 元数据时，跳过 Git 和 `check_repository_hygiene.py` 检查。
完整测试使用临时数据库、缓存与数据目录，不应修改自己的项目数据库。
数据存放位置：`data/app/` 为项目和结果，`data/runtime/` 为下载的数据、同步任务与
编辑器镜像，`data/cache/` 为缓存。这些目录均不进入 Git。
测试中的 `tests/fixtures/legacy_app.db` 是只读历史迁移样本，不能当作运行数据库。

升级旧版本前先停止服务，备份整个 `data/app/`、`data/runtime/` 和 `.env` 到项目
目录外。旧分支曾跟踪 `data/app/alphalab.db`，更新移除跟踪时可能删除干净文件或被
本地修改阻止；请先确认备份，再切换版本，并在首次启动前将备份放回原位置。
保留旧虚拟环境、旧源码包和数据备份直到验证新版本，避免丢失本地项目。
更新依赖后重新构建网页并重启后端。不要使用 `npm audit fix --force` 直接跨版本升级。

## 7. 修改前端时的开发模式

终端一启动后端（macOS 使用已激活的 `python`，Windows 使用 `.venv` 中的 Python）：

```bash
python -m uvicorn dashboard.backend.main:app --reload --host 127.0.0.1 --port 8000
```

终端二运行 `npm --prefix dashboard/frontend run dev:web`，打开
<http://127.0.0.1:5173>。Vite 将 API 和 WebSocket 代理到 8000。
若修改后端端口，在启动 Vite 的同一终端设置 `ALPHALAB_API_ORIGIN` 指向该地址。
多个分支请分别建立 worktree、虚拟环境并分配端口。
需要覆盖数据位置时，在启动 Python **之前**设置 shell 环境变量
`ALPHALAB_APP_DATA_DIR`、`ALPHALAB_RUNTIME_DIR`、`ALPHALAB_CACHE_DIR`。

## 8. 维护者生成学生部署包

前端构建并通过上述检查后，在 Git 工作区运行：

```bash
python scripts/build_local_bundle.py
```

产物为忽略目录下的 `artifacts/AlphaLab-local-deployment.zip` 和校验文件。
包内含当前源码、冻结测试样本和已构建网页，排除本地运行数据库、下载数据、
凭据、缓存、虚拟环境与 `node_modules`。`LOCAL_BUILD.json` 记录基础提交和逐文件
SHA-256，包含未提交的本地源码修改。学生解压后按第 2、4、5 步操作即可。
