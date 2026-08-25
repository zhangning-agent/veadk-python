# 16. Self-Hosted Sandbox Agent Demo

This example connects a **VeADK Agent** to a **Self-Hosted Sandbox Environment** using the Managed Sessions event protocol. A high-level session hook creates the remote session. `DispatchRuntimeProvider` intercepts every non-MCP tool, converts file/search/Python tools into bash commands, and posts an `agent.tool_use` event for the Worker. MCP tools retain their original ADK implementation.

---

## 🎯 Architecture

```text
       [ User Prompt ]
              │
              ▼
   ┌───────────────────────┐
   │      VeADK Agent      │
   │  (LLM Reasoning Loop) │
   └──────────┬────────────┘
              │ Calls bash/read/write/edit/list/search/python
              ▼
   ┌───────────────────────────┐
   │ DispatchRuntimeProvider   │
   │ non-MCP → remote bash     │
   │ MCP → original ADK tool   │
   └──────────┬────────────────┘
              │ POST /v1/sessions/{id}/events
              │ type: agent.tool_use
              ▼
   ┌────────────────────────────────────────┐
   │ Remote Sandbox Server & Worker Pool    │
   │ (e.g. Base URL: http://host:8080)      │
   │ (Env ID: env_01SLqXH...)               │
   └────────────────────────────────────────┘
```

---

## 🚀 Quick Start

### 1. Configure Environment Variables

Copy the example environment configuration file and update it with your credentials:

```bash
cp examples/16_self_host_sandbox/.env.example examples/16_self_host_sandbox/.env
```

Alternatively, export variables directly or pass CLI flags:

```bash
export ANTHROPIC_BASE_URL="http://localhost:8080"
export ANTHROPIC_ENVIRONMENT_ID="env_01SLqXHseguCmohifEqeUAYu"
export ANTHROPIC_ENVIRONMENT_KEY="your-sandbox-token"
export X_TOP_ACCOUNT_ID="your-account-id"
```


### 2. Run the Demo

You can run the demo directly with the provided `run.sh` script (which uses `uv` automatically):

```bash
bash examples/16_self_host_sandbox/run.sh
```

Or using `uv run` / `python`:

```bash
# Using uv
uv run --extra sandbox python examples/16_self_host_sandbox/main.py

# Or passing CLI arguments
python examples/16_self_host_sandbox/main.py \
  --base-url "http://localhost:8080" \
  --env-id "env_01SLqXHseguCmohifEqeUAYu" \
  --bearer-token "your-token" \
  --prompt "Create a Python script in /workspace and run it with pytest."
```

If `--session-id` is omitted, `ShortTermMemory.after_create_session_callback`
creates the remote session through `POST /v1/sessions`. Each intercepted tool
posts an `agent.tool_use` to `POST /v1/sessions/{id}/events`. The Worker consumes
that event, runs the bash command, and posts a matching `user.tool_result`.

The example does not post tool tasks as `user.message`, and it does not expect a
remote LLM to create a second tool call. The event's `id` is the VeADK function
call ID and must be returned by the Worker as `tool_use_id`.

