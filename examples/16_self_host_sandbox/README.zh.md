# 16. 自建沙箱 Agent 示例 (Self-Hosted Sandbox)

本示例由 `veadk web` 处理用户消息和模型循环。每个新建的 VeADK Web Session 只创建一个远端 Managed Session；仅将 VeADK 模型产生的 `agent.tool_use` 发送给 Self-Hosted Runtime dispatcher，再由它为配置的 TAE Sandbox 创建 Tool Session。

---

## 🎯 架构拓扑

```text
       【用户指令】: "在沙箱中写个快速排序并运行测试"
              │
              ▼
   ┌───────────────────────┐
   │      VeADK Web        │
   │   模型与 Agent 循环     │
   └──────────┬────────────┘
              │ POST agent.tool_use
              ▼
   ┌───────────────────────────┐
   │ Runtime 7hw8g3yr          │
   │ 仅负责 dispatcher          │
   └──────────┬────────────────┘
              │ 为每个 Session 创建 Tool Session
              ▼
   ┌────────────────────────────────────────┐
   │ TAE Sandbox Tool m3m24zxs              │
   │ 执行 Runtime 产生的工具调用              │
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
export ANTHROPIC_BASE_URL="https://<runtime-gateway>"
export ANTHROPIC_ENVIRONMENT_ID="env_01SLqXHseguCmohifEqeUAYu"
export ANTHROPIC_ENVIRONMENT_KEY="your-runtime-token"
export SANDBOX_AGENT_ID="agent_your_managed_agent_id"
export X_TOP_ACCOUNT_ID="your-account-id"
```

本地 VeADK 进程需要正常的模型配置。模型推理由 VeADK 完成，只有 Tool 执行委托给远端 Runtime 与 Sandbox。


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

# 或指定一个本地到远端的 Session 映射 ID
python examples/16_self_host_sandbox/main.py \
  --session-id "my-local-session" \
  --prompt "请在 /workspace 下创建一个 quick_sort.py 并运行验证"
```

#### 方式三：飞书机器人常驻模式

在飞书开放平台为机器人启用 WebSocket 长连接，并订阅接收消息事件。然后在 `.env`
中配置：

```bash
TOOL_FEISHU_CHANNEL_APP_ID=cli_your_feishu_app_id
TOOL_FEISHU_CHANNEL_APP_SECRET=your_feishu_app_secret
TOOL_FEISHU_CHANNEL_TRANSPORT=ws
TOOL_FEISHU_CHANNEL_STREAMING=true
TOOL_FEISHU_CHANNEL_REACTIONS=true
TOOL_FEISHU_CHANNEL_SHOW_THINKING=true
TOOL_FEISHU_CHANNEL_SHOW_TOOL_CALLS=true
TOOL_FEISHU_CHANNEL_SHOW_TOOL_RESULTS=true
TOOL_FEISHU_CHANNEL_SEPARATE_TOOL_CALL_CARDS=true
TOOL_FEISHU_CHANNEL_SEPARATE_THINKING_CARD=true
TOOL_FEISHU_CHANNEL_CREATE_TOPIC=true
```

启动常驻进程：

```bash
bash examples/16_self_host_sandbox/run.sh --feishu
```

进程会保持飞书 WebSocket 连接，断开后自动重连。飞书用户和会话分别映射为
VeADK 的 `user_id` 和 `session_id`，因此同一个飞书会话会复用上下文。使用
`Ctrl+C` 或发送 `SIGTERM` 时，进程会停止接收新消息，并等待在途回复完成后退出。
每条用户消息会创建一个飞书话题。话题内的思考摘要使用一张独立卡片，
每次 Tool call 及其对应的 Tool result 共用一张独立卡片，最终回答再使用
一张独立卡片。没有产生的阶段不会创建空卡片。工具参数和结果会自动脱敏并截断，
避免单张卡片超出飞书限制。

#### 方式四：Managed Agents Agent Loop 模式

该模式不创建新 Session，也不发送伪造的唤醒消息。控制面收到真实
`user.message` 后创建 WorkItem，沙箱启动时将对应 Session ID 注入
`ANTHROPIC_SESSION_ID`。VEADK 监听该 Session 的 SDK SSE，处理一轮后写入
`session.status_idle` 并退出：

```bash
export ANTHROPIC_SESSION_ID="session_existing_managed_session"
bash examples/16_self_host_sandbox/run.sh --managed-agent-loop
```

Agent Loop 的监听和所有写入都使用扩展后的 Anthropic Python SDK：
`client.beta.sessions.events.stream()` 与 `events.send()`。本地开发时可将修改后的
SDK 以 editable 方式装入本仓库虚拟环境：

```bash
uv pip install --python .venv/bin/python -e \
  /home/mofanke/github/agent-ma/anthropic-sdk-python
```

无需真实服务即可运行 SDK → HTTP/SSE → VEADK Loop 的完整联调脚本：

```bash
PYTHONPATH=/home/mofanke/github/agent-ma/anthropic-sdk-python/src:$PWD \
  uv run --extra sandbox python \
  examples/16_self_host_sandbox/local_agent_loop_test.py
```

分布式本地模式使用官方 Environment Work API 作为唯一执行权边界。本地 Environment
ID 的 Work API 由 ma-server 实现，Session/Event 仍保存在云 Task Server。每个进程
使用不同的 worker ID；ma-server 原子完成 poll/ack，SDK 在处理期间维护
租约，并在租约丢失时取消 handler。VeADK 对话状态写入 PostgreSQL，因此后续轮次
可以由另一个进程恢复并继续。

本地栈只监听 loopback：

- `127.0.0.1:18080`：Nginx 统一入口。Agent 路由及 session 创建经过
  `ma-server`；session 查询/事件由它代理至 Task Server。Environment Work 仅供
  集群内 Worker 使用，不通过浏览器 Gateway 暴露。
- `127.0.0.1:18081`：仅监听 localhost 的 ma-server 官方 Environment Work 入口，
  供三个 Agent Loop 进程使用。
- `127.0.0.1:55432`：VeADK `DatabaseSessionService` 使用的 PostgreSQL。

`ma-server` 在本地保存 Agent 定义，并在创建 Task Server session 时将 Agent ID
转换为不可变的 `agent_with_overrides` 快照。配置现有 `.env` 后执行：

```bash
# 修改本地 Anthropic SDK 后首次执行。
uv pip install --python .venv/bin/python -e \
  /home/mofanke/github/agent-ma/anthropic-sdk-python

# 启动 PostgreSQL、ma-server 和 Nginx；Worker API 仅监听 127.0.0.1:18081。
bash examples/16_self_host_sandbox/local_managed_agents_stack.sh up

# 先启动三个平等 Worker，再创建 Agent/Session，执行三轮并验证跨进程恢复。
bash examples/16_self_host_sandbox/local_managed_agents_stack.sh test

# 停止容器；用于持久化的 named volume 会保留。
bash examples/16_self_host_sandbox/local_managed_agents_stack.sh down
```

测试使用随机隔离的 environment queue，并在 Session 存在之前先启动三个 Worker；
每个 Worker 先完成一次真实的空队列 poll，并带 `--max-work-items 1`。创建 Session
时直接携带第一条 `user.message`，三轮对话因此自然产生三个不同 work，并由三个
平等竞争的 Worker 各领取一次，测试不会指定或主动杀死获胜者。第三个 Worker
必须从 PostgreSQL 恢复前两个进程分别写入的 codeword。成功输出以
`Distributed stateless VeADK Agent Loop test passed.` 结束。
分布式 Worker 会从冻结的 `Session.agent` 快照重建能力。`bash`、`read`、
`write`、`edit`、`glob` 和 `grep` 在 Session 独立工作目录执行；
`web_fetch`/`web_search` 走本地 VeADK 实现；URL MCP Server 转换为 ADK
`McpToolset` 并记录对应 Managed Agents 工具事件。自定义工具和 `always_ask`
会进入 `requires_action`，等待匹配的用户结果或确认后继续。Worker 在心跳启动后、
从 PostgreSQL 恢复对话前，根据 Session 固定版本从 ma-server 下载 Skills 到
`<MANAGED_AGENT_WORKDIR>/sessions/<session-id>/skills/`。本地运行时该进程就在
宿主机上，因此应使用隔离目录；生产环境应将 Worker 部署在目标沙箱内。

如果 SDK 测试在发请求前因宿主机启用了 SOCKS 代理、但未安装 `socksio` 而失败，
可以安装 SDK 的 SOCKS extra，或在无凭据 mock 测试中清除代理变量。生成的 API
测试还依赖仓库 mock server 和可选 `aiohttp` 测试依赖；聚焦 Agent Loop 测试不依赖它们。


每个新建的 VeADK Session 都通过 `POST /v1/sessions` 创建一个远端 Managed
Session。用户消息和模型循环留在 VeADK；`DispatchRuntimeProvider` 拦截模型生成的
Tool call，以 `agent.tool_use` 发送给 Runtime，等待匹配的 Tool result 后交还给本地
VeADK 模型继续生成最终回复。远端 Session 首次创建时会自动入队；后续每个 turn
以第一个真实的 `agent.tool_use` 事件让控制面重新入队并拉起空闲的 sandbox，
同一 turn 内的其他 Tool call 复用已有 Worker。示例不会为了唤醒 sandbox 而伪造
`user.message`；每个 turn 结束时发送一次 `session.status_idle`。

## Docker 与 Kubernetes 部署

Docker 构建上下文必须使用仓库根目录：

```bash
docker build \
  -f examples/16_self_host_sandbox/Dockerfile \
  -t <registry>/veadk-self-host-sandbox:<tag> \
  .
docker push <registry>/veadk-self-host-sandbox:<tag>
```

将 `k8s.yaml` 中的镜像和 Ingress 域名替换成目标环境的值。然后从本地 `.env`
显式创建白名单 Secret，再部署工作负载：

```bash
set -a
source examples/16_self_host_sandbox/.env
set +a

kubectl create secret generic veadk-self-host-sandbox-env \
  --from-literal=ANTHROPIC_BASE_URL="$ANTHROPIC_BASE_URL" \
  --from-literal=ANTHROPIC_ENVIRONMENT_ID="$ANTHROPIC_ENVIRONMENT_ID" \
  --from-literal=ANTHROPIC_ENVIRONMENT_KEY="$ANTHROPIC_ENVIRONMENT_KEY" \
  --from-literal=SANDBOX_AGENT_ID="$SANDBOX_AGENT_ID" \
  --from-literal=X_TOP_ACCOUNT_ID="$X_TOP_ACCOUNT_ID" \
  --dry-run=client -o yaml | kubectl apply -f -

kubectl apply -f examples/16_self_host_sandbox/k8s.yaml
kubectl rollout status deployment/veadk-self-host-sandbox
```

不要使用整个 `.env` 创建 Secret；Deployment 只注入上述白名单内的 Runtime 连接配置。

## 开发验证

如果仓库虚拟环境仅安装了运行时依赖，其中可能没有 `pytest`。请先同步开发依赖组，
再运行 Tool dispatch 的定向测试：

```bash
uv sync --group dev --extra sandbox
uv run pytest -q tests/runtime/test_self_host_sandbox_client.py \
  tests/runtime/test_self_host_sandbox_agent.py \
  tests/runtime/test_managed_agent_loop.py
```
