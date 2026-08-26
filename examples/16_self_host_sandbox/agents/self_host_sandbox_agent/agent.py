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

"""VeADK Agent definition for Self-Hosted Sandbox Execution.

Compatible with `veadk web` / `veadk frontend` and standalone Python CLI runs.
"""

import asyncio
import logging
import os
import sys
import threading
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

# Ensure the example directory is in sys.path to resolve sandbox_client
EXAMPLE_DIR = Path(__file__).resolve().parents[2]
if str(EXAMPLE_DIR) not in sys.path:
    sys.path.insert(0, str(EXAMPLE_DIR))

# Automatically load environment variables from example .env
env_file = EXAMPLE_DIR / ".env"
if env_file.exists():
    load_dotenv(env_file, override=True)

from veadk import Agent  # noqa: E402
from veadk.memory.short_term_memory import ShortTermMemory  # noqa: E402
from veadk.runtime import DispatchRuntimeProvider, ToolCall  # noqa: E402
from sandbox_client import SelfHostSandboxClient  # noqa: E402

logger = logging.getLogger("veadk.sandbox_agent")


class SandboxSessionManager:
    """Map each VeADK session to its own remote Self-Hosted Sandbox session.

    ``veadk web`` serves many concurrent VeADK sessions from one process. A
    single shared client would funnel every session's tool calls into the same
    remote sandbox, so we keep one :class:`SelfHostSandboxClient` per VeADK
    session id and provision a distinct remote session for each.
    """

    def __init__(self) -> None:
        self._clients: dict[str, SelfHostSandboxClient] = {}
        self._lock = threading.Lock()

    def _new_client(self) -> SelfHostSandboxClient:
        # A fresh client starts without a session id so it provisions its own
        # remote session lazily / on demand. Do not inherit SANDBOX_SESSION_ID
        # here, otherwise every session would collapse back onto one sandbox.
        client = SelfHostSandboxClient()
        client.session_id = None
        return client

    def get(self, veadk_session_id: str) -> SelfHostSandboxClient:
        """Return the client bound to ``veadk_session_id``, creating one if needed."""
        key = veadk_session_id or "__default__"
        with self._lock:
            client = self._clients.get(key)
            if client is None:
                client = self._new_client()
                self._clients[key] = client
            return client

    def bind(self, veadk_session_id: str, client: SelfHostSandboxClient) -> None:
        """Attach an externally configured client to a session key.

        Used by the standalone CLI, which applies argument overrides to a single
        client and then runs one VeADK session in the process.
        """
        key = veadk_session_id or "__default__"
        with self._lock:
            self._clients[key] = client

    def create_remote_session(self, veadk_session_id: str) -> str:
        """Provision a remote sandbox session for a newly created VeADK session."""
        client = self.get(veadk_session_id)
        if not client.session_id:
            client.create_session(
                title=f"VeADK Self-Hosted Sandbox Session {veadk_session_id}"
            )
        return client.session_id or ""

    def release(self, veadk_session_id: str) -> None:
        """Mark a session idle and drop it from the registry."""
        key = veadk_session_id or "__default__"
        with self._lock:
            client = self._clients.pop(key, None)
        if client:
            client.post_status_idle()


# One manager owns all per-session sandbox clients for this process.
sandbox_sessions = SandboxSessionManager()

# Backwards-compatible default client used by the standalone CLI (main.py),
# which runs a single session per process.
sandbox_client = sandbox_sessions.get("__default__")


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


async def after_create_session(session: Any) -> None:
    """Provision a dedicated remote Session for each new VeADK Session."""
    veadk_session_id = str(getattr(session, "id", "") or "")
    logger.info(
        "⏳ Creating Self-Hosted Session for VeADK session %s...", veadk_session_id
    )
    remote_id = await asyncio.to_thread(
        sandbox_sessions.create_remote_session, veadk_session_id
    )
    logger.info(
        "✅ Session created: veadk=%s remote=%s", veadk_session_id, remote_id
    )


async def dispatch_task(tool_call: ToolCall) -> dict:
    """Convert an ADK tool call into a Managed Session event task.

    Routes the call to the sandbox client bound to the current VeADK session so
    each web session executes inside its own remote sandbox.
    """
    client = sandbox_sessions.get(tool_call.session_id)
    return await asyncio.to_thread(
        client.dispatch_tool,
        tool_call.name,
        tool_call.arguments,
        dispatch_id=tool_call.id,
    )


dispatch_runtime = DispatchRuntimeProvider(
    dispatch_task,
    # Route all non-MCP tools to the remote sandbox worker
    dispatchable_tools=None,
)

short_term_memory = ShortTermMemory(
    after_create_session_callback=after_create_session,
)

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
    short_term_memory=short_term_memory,
    before_tool_callback=dispatch_runtime.before_tool_callback,
)

# Standard entry point for VeADK / Google ADK Web UI loader
root_agent = agent
