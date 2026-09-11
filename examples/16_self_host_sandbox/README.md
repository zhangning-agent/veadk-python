# 16. Self-Hosted Sandbox Agent Demo

This example runs the conversation and model loop in `veadk web`. Each new VeADK Web session creates exactly one remote Managed Session. Only VeADK-generated `agent.tool_use` events are sent to the Self-Hosted Runtime dispatcher, which creates a TAE Tool session for the configured Sandbox.

---

## 🎯 Architecture

```text
       [ User Prompt ]
              │
              ▼
   ┌───────────────────────┐
   │      VeADK Web        │
   │ model + agent loop    │
   └──────────┬────────────┘
              │ POST agent.tool_use
              ▼
   ┌───────────────────────────┐
   │ Runtime 7hw8g3yr          │
   │ dispatcher only           │
   └──────────┬────────────────┘
              │ creates one tool session
              ▼
   ┌────────────────────────────────────────┐
   │ TAE Sandbox Tool m3m24zxs              │
   │ executes the Runtime's tool calls      │
   └────────────────────────────────────────┘
```

---

## 🚀 Quick Start

### 1. Configure Environment Variables

Copy the example environment configuration file and update it with your credentials:

```bash
cp examples/16_self_host_sandbox/.env.example examples/16_self_host_sandbox/.env
```

Alternatively, export variables directly:

```bash
export ANTHROPIC_BASE_URL="https://<runtime-gateway>"
export ANTHROPIC_ENVIRONMENT_ID="env_01SLqXHseguCmohifEqeUAYu"
export ANTHROPIC_ENVIRONMENT_KEY="your-runtime-token"
export SANDBOX_AGENT_ID="agent_your_managed_agent_id"
export X_TOP_ACCOUNT_ID="your-account-id"
```

The local VeADK process needs its normal model configuration. Model reasoning happens in VeADK; only tool execution is delegated to the remote Runtime and Sandbox.


### 2. Run the Demo

#### Option A: Web UI Mode (`veadk web`)

Run the visual web debugging interface in your browser (defaults to port **8067**, configurable via `PORT` in `.env` or `--port`):

```bash
# Via run.sh helper (automatically switches to agents dir and applies port 8067)
bash examples/16_self_host_sandbox/run.sh --web

# Or directly in the agents directory
cd examples/16_self_host_sandbox/agents
veadk web --port 8067
```




#### Option B: CLI Mode

```bash
# Run with run.sh helper
bash examples/16_self_host_sandbox/run.sh

# Or using uv
uv run --extra sandbox python examples/16_self_host_sandbox/main.py

# Or select an existing local-to-remote session mapping
python examples/16_self_host_sandbox/main.py \
  --session-id "my-local-session" \
  --prompt "Create a Python script in /workspace and run it with pytest."
```

#### Option C: Long-running Feishu bot channel

Enable the bot's WebSocket connection and message event subscription in the
Feishu developer console, then configure `.env`:

```bash
TOOL_FEISHU_CHANNEL_APP_ID=cli_your_feishu_app_id
TOOL_FEISHU_CHANNEL_APP_SECRET=your_feishu_app_secret
TOOL_FEISHU_CHANNEL_TRANSPORT=ws
TOOL_FEISHU_CHANNEL_STREAMING=true
TOOL_FEISHU_CHANNEL_REACTIONS=true
TOOL_FEISHU_CHANNEL_SHOW_THINKING=false # Disabled by default. Set to true or use --show-thinking to enable.
TOOL_FEISHU_CHANNEL_SHOW_TOOL_CALLS=true
TOOL_FEISHU_CHANNEL_SHOW_TOOL_RESULTS=true
TOOL_FEISHU_CHANNEL_SEPARATE_TOOL_CALL_CARDS=true
TOOL_FEISHU_CHANNEL_SEPARATE_THINKING_CARD=true
TOOL_FEISHU_CHANNEL_CREATE_TOPIC=true
```

Start the long-running process:

```bash
# Thinking display is disabled by default
bash examples/16_self_host_sandbox/run.sh --feishu

# To display model thinking process, add --show-thinking
python examples/16_self_host_sandbox/main.py --feishu --show-thinking
```

The process keeps the Feishu WebSocket connected and reconnects automatically.
Feishu users and conversations map to VeADK `user_id` and `session_id` values,
so messages in the same conversation share context. `Ctrl+C` or `SIGTERM` stops
new messages and drains in-flight replies before exiting.

For each user message, this demo creates a Feishu topic containing one thinking
card when thinking is available, one dedicated card for every tool call and its
result, and a separate final-answer card. Missing stages do not create empty
cards. Tool payloads are redacted for common credential fields and truncated
to keep each card bounded. Sending `/new` (or `/reset`, `/clear`) resets the
current session and releases the previous sandbox so subsequent messages
provision and run in a fresh remote sandbox session.


`ShortTermMemory.after_create_session_callback` creates one remote Managed Session
for each newly created VeADK session. VeADK handles the user message and model
loop locally. `DispatchRuntimeProvider` intercepts each model tool call, posts an
`agent.tool_use` through `POST /v1/sessions/{session_id}/events`, waits for its
matching tool result, and returns that result to
the local VeADK model loop. Creating the remote Session enqueues its first turn.
At the beginning of each text turn, the lifecycle wrapper sends the actual user
text as `user.message` through the same `/events` endpoint to start or resume
work. `agent.tool_use` alone only appends a tool event and does not wake an idle
session on the current service. Further tool calls reuse the worker. Every turn finishes with one `session.status_idle` event.

## Lifecycle test

Configure the Runtime connection variables and `MODEL_AGENT_API_KEY` in the
process environment, then run:

```bash
python examples/16_self_host_sandbox/lifecycle_test.py \
  --tae-sandbox-id 44nffoq7 \
  --command-timeout-seconds 600 \
  --reclaim-timeout-seconds 420
```

The test sends two VeADK messages and polls TAE for reclamation between them.
Both turns require a successful matching remote tool result and final reply.
VeADK and Managed Session IDs must stay the same; the second TAE instance ID
must differ. JSON progress includes instance IDs and elapsed times. The final
`status: passed` indicates success; failures exit nonzero. The final turn marks
the session idle for normal dispatcher cleanup without explicitly deleting it.

`ANTHROPIC_BASE_URL`, `ANTHROPIC_ENVIRONMENT_ID`, and `ANTHROPIC_ENVIRONMENT_KEY`
select the Runtime; use the connection settings for `s764q7yu` when testing it.
Avoid a duplicate `/v1` prefix in the SDK base URL. TAE observation defaults to
the BOE endpoint and reuses local `bytedcli` authentication, or `TAE_JWT_TOKEN`.
Use `--mode tool` for a direct tool dispatch test without model inference.

## Docker and Kubernetes deployment

Use the repository root as the Docker build context:

```bash
docker build \
  -f examples/16_self_host_sandbox/Dockerfile \
  -t <registry>/veadk-self-host-sandbox:<tag> \
  .
docker push <registry>/veadk-self-host-sandbox:<tag>
```

Replace the image and Ingress host in `k8s.yaml` for the target environment. Then
create an allowlisted Secret from the local `.env` and deploy the workload:

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

Do not create the Secret from the entire `.env`. The Deployment only injects
the allowlisted Runtime connection values above.

## Development verification

The repository virtual environment may omit test dependencies when it was
created for runtime use only. Install the development dependency group before
running the focused dispatch tests:

```bash
uv sync --group dev --extra sandbox
uv run pytest -q tests/runtime/test_self_host_sandbox_client.py \
  tests/runtime/test_self_host_sandbox_agent.py
```
