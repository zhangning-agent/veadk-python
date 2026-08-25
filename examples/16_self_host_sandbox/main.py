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
from pathlib import Path

from dotenv import load_dotenv

# 1. Automatically load all credentials and domain configurations from .env
env_file = Path(__file__).resolve().parent / ".env"
if env_file.exists():
    load_dotenv(env_file, override=True)

from veadk import Agent, Runner  # noqa: E402
from veadk.memory.short_term_memory import ShortTermMemory  # noqa: E402
from veadk.runtime import DispatchRuntimeProvider, ToolCall  # noqa: E402
from sandbox_client import SelfHostSandboxClient  # noqa: E402


# ─────────────────────────────────────────────────────────────
# 2. Global Sandbox Client instance
# ─────────────────────────────────────────────────────────────
sandbox_client: SelfHostSandboxClient = None


def bash(command: str, timeout: float = 120) -> dict:
    """Execute bash in the registered Self-Hosted Sandbox.

    Args:
        command: The shell command to run, e.g. "pytest tests/", "ls -la", "python script.py".
        timeout: Maximum number of seconds to wait for the remote tool result.

    Returns:
        A dictionary containing exit_code, stdout, and stderr from the sandbox.
    """
    raise RuntimeError("Remote tools must be intercepted by DispatchRuntimeProvider")


def read_file(
    file_path: str, offset: int | None = None, limit: int | None = None
) -> dict:
    """Read a text file in the remote sandbox, optionally by line range."""
    raise RuntimeError("Remote tools must be intercepted by DispatchRuntimeProvider")


def write_file(file_path: str, content: str) -> dict:
    """Create or overwrite a text file in the remote sandbox."""
    raise RuntimeError("Remote tools must be intercepted by DispatchRuntimeProvider")


def edit_file(
    file_path: str,
    old_string: str,
    new_string: str,
    replace_all: bool = False,
) -> dict:
    """Replace an exact string in a remote sandbox file."""
    raise RuntimeError("Remote tools must be intercepted by DispatchRuntimeProvider")


def list_files(path: str = "/workspace", max_depth: int = 4) -> dict:
    """List files below a directory in the remote sandbox."""
    raise RuntimeError("Remote tools must be intercepted by DispatchRuntimeProvider")


def search_files(
    pattern: str,
    path: str = "/workspace",
    glob: str | None = None,
    case_insensitive: bool = False,
) -> dict:
    """Search remote sandbox files with ripgrep or grep."""
    raise RuntimeError("Remote tools must be intercepted by DispatchRuntimeProvider")


def python(code: str, workdir: str = "/workspace", timeout: float = 120) -> dict:
    """Run Python code in the remote sandbox."""
    raise RuntimeError("Remote tools must be intercepted by DispatchRuntimeProvider")


# ─────────────────────────────────────────────────────────────
# 3. Main Entry Point
# ─────────────────────────────────────────────────────────────
async def main() -> None:
    parser = argparse.ArgumentParser(description="VeADK Self-Hosted Sandbox Agent Demo")
    parser.add_argument(
        "--base-url",
        default=os.getenv("SANDBOX_BASE_URL"),
        help="Base URL of the APIG Sandbox Server (reads from SANDBOX_BASE_URL in .env by default)",
    )
    parser.add_argument(
        "--agent-id",
        default=os.getenv("SANDBOX_AGENT_ID", "agent_011CSd8hFhXGpz33bM1pBw7y"),
        help="Managed Agent ID",
    )
    parser.add_argument(
        "--env-id",
        default=os.getenv("SANDBOX_ENVIRONMENT_ID", "env_01SLqXHseguCmohifEqeUAYu"),
        help="Self-Hosted Environment ID (reads from SANDBOX_ENVIRONMENT_ID in .env)",
    )
    parser.add_argument(
        "--session-id",
        default=os.getenv("SANDBOX_SESSION_ID"),
        help="Existing Session ID (optional; if not provided, creates a new one via POST /v1/sessions)",
    )
    parser.add_argument(
        "--bearer-token",
        default=os.getenv("SANDBOX_BEARER_TOKEN"),
        help="Bearer Token for APIG Gateway authorization (reads from SANDBOX_BEARER_TOKEN in .env)",
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

    # Initialize client with configurations from .env / args
    global sandbox_client
    sandbox_client = SelfHostSandboxClient(
        base_url=args.base_url,
        environment_id=args.env_id,
        agent_id=args.agent_id,
        session_id=args.session_id,
        bearer_token=args.bearer_token,
        account_id=args.account_id,
        remote_bash_tool_name=args.bash_tool_name,
    )

    async def after_create_session(_session) -> None:
        """Create the remote Session after VeADK creates its local Session."""
        if sandbox_client.session_id:
            return
        print(
            "⏳ 正在通过 POST /v1/sessions 在服务端创建 Self-Hosted Session 并推入 WorkQueue..."
        )
        await asyncio.to_thread(sandbox_client.create_session)
        print(f"✅ Session 创建成功! Session ID: {sandbox_client.session_id}")

    async def dispatch_task(tool_call: ToolCall) -> dict:
        """Convert an ADK tool call into a Managed Session event task."""
        return await asyncio.to_thread(
            sandbox_client.dispatch_tool,
            tool_call.name,
            tool_call.arguments,
            dispatch_id=tool_call.id,
        )

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

    # Step 2: Define the VeADK Agent equipped with Sandbox tools
    agent = Agent(
        name="self_host_sandbox_agent",
        description="An engineering agent dispatching tool calls to a Self-Hosted Worker.",
        instruction=(
            f"You are an autonomous engineering assistant connected to a remote Self-Hosted Sandbox (Env ID: {sandbox_client.environment_id}). "
            "Use the provided bash, file, search, and Python tools for every operation in the sandbox. "
            "When the task is accomplished, stop calling tools and output your final result."
        ),
        tools=[
            bash,
            read_file,
            write_file,
            edit_file,
            list_files,
            search_files,
            python,
        ],
    )

    short_term_memory = ShortTermMemory(
        after_create_session_callback=after_create_session,
    )
    dispatch_runtime = DispatchRuntimeProvider(
        dispatch_task,
        # Route every resolved non-MCP tool to the Worker. MCP tools retain
        # their original ADK implementation inside DispatchRuntimeProvider.
        dispatchable_tools=None,
    )
    runner = Runner(
        agent=agent,
        app_name="self_host_sandbox_demo",
        short_term_memory=short_term_memory,
        plugins=[dispatch_runtime],
    )

    run_kwargs = {}
    if sandbox_client.session_id:
        run_kwargs["session_id"] = sandbox_client.session_id

    try:
        answer = await runner.run(messages=args.prompt, **run_kwargs)
        print("\n" + "=" * 60)
        print("🎯 Agent 最终执行结果:")
        print("=" * 60)
        print(answer)
    finally:
        # Notify the remote worker that the session turn has completed so it can release its lease
        sandbox_client.post_status_idle()


if __name__ == "__main__":
    asyncio.run(main())
