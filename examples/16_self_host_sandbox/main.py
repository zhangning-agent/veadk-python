# Copyright (c) 2025 Beijing Volcano Engine Technology Co., Ltd. and/or its affiliates.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""VeADK Agent with Self-Hosted Sandbox Execution.

Session-backed Worker Protocol:
1. POST /v1/sessions: Creates session in managed_selfhost_sessions & enqueues work item.
2. POST /v1/sessions/{session_id}/events: Publishes agent.tool_use events.
3. Worker streams tool calls and posts matching user.tool_result events.
"""

import argparse
import asyncio
import os

from agents.self_host_sandbox_agent.agent import (
    agent,
    sandbox_client,
    sandbox_sessions,
)
from veadk import Runner


async def main() -> None:
    parser = argparse.ArgumentParser(description="VeADK Self-Hosted Sandbox Agent Demo")
    parser.add_argument(
        "--base-url",
        default=os.getenv("ANTHROPIC_BASE_URL") or os.getenv("SANDBOX_BASE_URL"),
        help="Base URL of the APIG Sandbox Server (reads from ANTHROPIC_BASE_URL or SANDBOX_BASE_URL)",
    )
    parser.add_argument(
        "--agent-id",
        default=os.getenv("SANDBOX_AGENT_ID", "agent_011CSd8hFhXGpz33bM1pBw7y"),
        help="Managed Agent ID",
    )
    parser.add_argument(
        "--env-id",
        default=os.getenv("ANTHROPIC_ENVIRONMENT_ID") or os.getenv("SANDBOX_ENVIRONMENT_ID", "env_01SLqXHseguCmohifEqeUAYu"),
        help="Self-Hosted Environment ID (reads from ANTHROPIC_ENVIRONMENT_ID or SANDBOX_ENVIRONMENT_ID)",
    )
    parser.add_argument(
        "--session-id",
        default=os.getenv("SANDBOX_SESSION_ID"),
        help="Existing Session ID (optional; if not provided, creates a new one via POST /v1/sessions)",
    )
    parser.add_argument(
        "--bearer-token",
        default=os.getenv("ANTHROPIC_ENVIRONMENT_KEY") or os.getenv("ANTHROPIC_API_KEY") or os.getenv("SANDBOX_BEARER_TOKEN"),
        help="Bearer Token / API Key for Gateway authorization (reads from ANTHROPIC_ENVIRONMENT_KEY / SANDBOX_BEARER_TOKEN)",
    )
    parser.add_argument(
        "--account-id",
        default=os.getenv("X_TOP_ACCOUNT_ID"),
        help="Volcengine User Account ID (reads from X_TOP_ACCOUNT_ID in .env)",
    )
    parser.add_argument(
        "--bash-tool-name",
        default=os.getenv("SANDBOX_BASH_TOOL_NAME", "bash"),
        help="Worker-recognized bash tool name used in agent.tool_use events",
    )
    parser.add_argument(
        "--prompt",
        default="请在沙箱工作区 /workspace 下创建一个 quick_sort.py 文件，编写快速排序代码并运行验证。",
        help="User instruction to send to the Agent",
    )
    args = parser.parse_args()

    # Apply any CLI argument overrides to sandbox client
    if args.base_url:
        sandbox_client.base_url = args.base_url.rstrip("/")
    if args.env_id:
        sandbox_client.environment_id = args.env_id
    if args.agent_id:
        sandbox_client.agent_id = args.agent_id
    if args.session_id:
        sandbox_client.session_id = args.session_id
    if args.bearer_token:
        token = args.bearer_token
        if token.startswith("Bearer "):
            token = token[7:].strip()
        sandbox_client.bearer_token = token
    if args.account_id:
        sandbox_client.account_id = args.account_id
    if args.bash_tool_name:
        sandbox_client.remote_bash_tool_name = args.bash_tool_name

    print("=" * 60)
    print("🚀 VeADK Self-Hosted Sandbox Agent Initializing")
    print(f"📍 APIG Base URL:          {sandbox_client.base_url}")
    print(f"🤖 Agent ID:               {sandbox_client.agent_id}")
    print(f"📦 Environment ID:         {sandbox_client.environment_id}")
    print(
        f"🆔 Session ID:             "
        f"{sandbox_client.session_id or 'will be created automatically'}"
    )
    print(f"🔑 Account ID:             {sandbox_client.account_id}")
    print(f"🔧 Remote bash tool:       {sandbox_client.remote_bash_tool_name}")
    print(f"💬 Task Prompt:            {args.prompt}")
    print("=" * 60)

    runner = Runner(
        agent=agent,
        app_name="self_host_sandbox_demo",
    )

    session_id = sandbox_client.session_id or f"self-host-{os.getpid()}"

    # Bind the CLI-configured client to this run's session id so dispatch_task
    # routes tool calls to the sandbox we configured above (one sandbox / run).
    sandbox_sessions.bind(session_id, sandbox_client)

    # Ensure the session exists (this triggers after_create_session, which
    # provisions the remote sandbox) before streaming events through run_async.
    if runner.short_term_memory:
        await runner.short_term_memory.create_session(
            app_name=runner.app_name,
            user_id=runner.user_id,
            session_id=session_id,
        )

    run_config = RunConfig(
        streaming_mode=StreamingMode.SSE,
        max_llm_calls=int(os.getenv("MODEL_AGENT_MAX_LLM_CALLS", 100)),
    )
    new_message = types.Content(role="user", parts=[types.Part(text=args.prompt)])

    print("\n" + "=" * 60)
    print("🎯 Agent 流式输出:")
    print("=" * 60)

    try:
        final_output = ""
        async for event in runner.run_async(
            user_id=runner.user_id,
            session_id=session_id,
            new_message=new_message,
            run_config=run_config,
        ):
            if not (event.content and event.content.parts):
                continue
            for part in event.content.parts:
                if part.thought:
                    continue
                if part.text:
                    # Stream incremental model text as it arrives.
                    print(part.text, end="", flush=True)
                    if not event.partial:
                        final_output = part.text
                elif part.function_call:
                    fc = part.function_call
                    print(f"\n🔧 [tool call] {fc.name}({fc.args})", flush=True)
                elif part.function_response:
                    resp = part.function_response.response
                    print(f"\n📤 [tool result] {resp}", flush=True)

        print("\n" + "=" * 60)
        print("✅ 最终结果:")
        print("=" * 60)
        print(final_output)
    finally:
        # Notify the remote worker that the session turn has completed so it can release its lease
        sandbox_sessions.release(session_id)


if __name__ == "__main__":
    asyncio.run(main())
