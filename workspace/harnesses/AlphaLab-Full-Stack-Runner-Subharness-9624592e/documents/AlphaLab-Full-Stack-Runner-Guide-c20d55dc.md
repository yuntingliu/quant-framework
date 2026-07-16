# AlphaLab Full-Stack Runner Subharness

用于在 `E:\quant-framework` 运行 AlphaLab 量化框架的前后端：

- **后端**：FastAPI / Uvicorn，默认 `http://127.0.0.1:8000`
- **前端**：Vite React Web，默认 `http://localhost:5173`
- **状态与日志**：`E:\quant-framework\.conexus\alphalab-runner\`

## 内部工具

1. **Start Full Stack**（默认入口）
   - 安装缺失的前端依赖（如果 `node_modules` 不存在）
   - 后台启动后端：`python -m uvicorn dashboard.backend.main:app --reload --host 127.0.0.1 --port 8000`
   - 后台启动前端：`npm run dev:web -- --host 127.0.0.1 --port 5173`
   - 等待并检查两个 HTTP 服务可访问

2. **Verify Full Stack Health**
   - 检查后端 `/`
   - 检查后端 `/docs`
   - 检查前端 `/`

3. **Stop Full Stack**
   - 按 `.conexus/alphalab-runner/state.json` 记录的 PID 停止前后端进程树

4. **Validate Project Build**
   - 运行 `python -m pytest tests/contracts -q`
   - 运行 `npm --prefix dashboard/frontend run build`

## 当前验证结果

已在创建 Harness 前完成：

- `python -m pytest tests/contracts -q`：通过，`5 passed`
- `npm --prefix dashboard/frontend run build`：通过
- 前端依赖：已通过 `npm ci --no-audit --no-fund` 安装；该命令被外部超时截断，但后续 `node_modules` 存在且 build 成功。

## 使用方式

- 运行整个 Harness，默认会调用 **Start Full Stack**。
- 或单独运行内部工具：
  - 先跑 **Validate Project Build** 做构建验证
  - 再跑 **Start Full Stack** 启动服务
  - 跑 **Verify Full Stack Health** 确认服务
  - 使用完跑 **Stop Full Stack** 停止后台进程

## 打开地址

当前已通过 Harness 启动并验证：

- 前端：<http://localhost:5174>
- API 文档：<http://127.0.0.1:8000/docs>
- 后端根路径：<http://127.0.0.1:8000/>

说明：本机 `localhost:5173` 已被另一个 Vite 服务占用，Harness 自动选择了 `5174` 并通过 `/src/Workspace.tsx` 源码特征确认该服务确实是 AlphaLab 前端。

最新健康检查：

- 后端 `/`：HTTP 200，返回 `{"status":"ok","name":"AlphaLab Barebone API"}`
- 后端 `/docs`：HTTP 200
- 前端 `/`：HTTP 200
- 前端源码探针 `/src/Workspace.tsx`：HTTP 200，确认为 AlphaLab 前端

当前状态文件：`E:\quant-framework\.conexus\alphalab-runner\state.json`
