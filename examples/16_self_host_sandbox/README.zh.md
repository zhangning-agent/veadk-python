# 16. 自建沙箱 Agent 示例 (Self-Hosted Sandbox)

本示例演示了如何基于 **VeADK Agent** 连接和调度 **自建沙箱环境（Self-Hosted Sandbox）**。

示例使用 Managed Sessions 事件协议：高层 Session hook 创建远端 Session；`DispatchRuntimeProvider` 拦截所有非 MCP 工具，把文件、搜索和 Python 工具转换成 bash 命令，再向 Session 投递 `agent.tool_use`。MCP 工具保留原有 ADK 执行链路。

---

## 🎯 架构拓扑

```text
       【用户指令】: "在沙箱中写个快速排序并运行测试"
              │
              ▼
   ┌───────────────────────┐
   │      VeADK Agent      │
   │   (大模型核心推理大脑)   │
   └──────────┬────────────┘
              │ 调用 bash/read/write/edit/list/search/python
              ▼
   ┌───────────────────────────┐
   │ DispatchRuntimeProvider   │
   │ 非 MCP → 远端 bash          │
   │ MCP → 原 ADK 工具           │
   └──────────┬────────────────┘
              │ POST /v1/sessions/{id}/events
              │ type: agent.tool_use
              ▼
   ┌────────────────────────────────────────┐
   │ 远程沙箱服务端 (Managed Agents Server) │
   │ • Base URL: http://localhost:8080      │
   │ • Env ID:   env_01SLqXH...             │
   └────────────────────────────────────────┘
```

---

## 🚀 运行方法
 
### 1. 配置环境变量

复制环境配置文件示例并填入对应的配置与密钥：

```bash
cp examples/16_self_host_sandbox/.env.example examples/16_self_host_sandbox/.env
```

或直接在终端中导出环境变量：

```bash
export ANTHROPIC_BASE_URL="http://localhost:8080"
export ANTHROPIC_ENVIRONMENT_ID="env_01SLqXHseguCmohifEqeUAYu"
export ANTHROPIC_ENVIRONMENT_KEY="ebk_xxx"
export X_TOP_ACCOUNT_ID="your-account-id"
```


### 2. 启动示例

#### 方式一：Web 界面交互模式（`veadk web`）

在浏览器中启动可视化 Web 调试交互界面（默认端口为 **8067**，支持在 `.env` 中通过 `PORT` 或 `--port` 自定义）：

```bash
# 方式 A：通过 run.sh 快捷启动（自动切换到 agents 目录并使用 8067 端口）
bash examples/16_self_host_sandbox/run.sh --web

# 方式 B：进入 agents 目录直接运行
cd examples/16_self_host_sandbox/agents
veadk web --port 8067
```




#### 方式二：CLI 命令行模式

```bash
# 使用 run.sh 运行
bash examples/16_self_host_sandbox/run.sh

# 或使用 uv
uv run --extra sandbox python examples/16_self_host_sandbox/main.py

# 或通过命令行参数传递
python examples/16_self_host_sandbox/main.py \
  --base-url "http://localhost:8080" \
  --env-id "env_01SLqXHseguCmohifEqeUAYu" \
  --bearer-token "ebk_xxx" \
  --prompt "请在 /workspace 下创建一个 quick_sort.py 并运行验证"
```


未提供 `--session-id` 时，`ShortTermMemory.after_create_session_callback` 会先通过
`POST /v1/sessions` 创建远端 Session。每次被拦截的工具调用都会向
`POST /v1/sessions/{id}/events` 投递 `agent.tool_use`。Worker 消费事件、执行
bash，并写回匹配的 `user.tool_result`。

示例不会把工具任务伪装成 `user.message`，也不依赖远端大模型再次生成工具调用。
事件的 `id` 直接使用 VeADK 的 function call ID；Worker 必须将它作为
`tool_use_id` 原样回传。

## 开发验证

如果仓库虚拟环境仅安装了运行时依赖，其中可能没有 `pytest`。请先同步开发依赖组，
再运行 Runtime Provider 的定向测试：

```bash
uv sync --group dev --extra sandbox
uv run pytest -q tests/runtime/test_runtime_provider.py
```
