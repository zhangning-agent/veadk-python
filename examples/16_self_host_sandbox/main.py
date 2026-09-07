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

"""Run the VeADK agent with remotely dispatched sandbox tools."""

import argparse
import asyncio
import inspect
import json
import os
import re
import signal
import socket
import uuid
from pathlib import Path, PurePosixPath
from typing import Any, NoReturn

from agents.self_host_sandbox_agent.agent import (
    agent as default_agent,
)
from agents.self_host_sandbox_agent.agent import (
    dispatch_runtime,
    enable_sandbox_turn_lifecycle,
    sandbox_sessions,
)
from google.adk.tools.base_tool import BaseTool
from google.genai import types
from managed_agent_loop import ManagedAgentsLoop
from managed_session_resources import mcp_toolsets, skill_instructions
from sandbox_client import SelfHostSandboxClient

from veadk import Agent, Runner
from veadk.extensions import FeishuChannelExtension
from veadk.memory.short_term_memory import ShortTermMemory
from veadk.runtime import (
    LocalRuntimeProvider,
    RuntimeProvider,
    ToolCall,
)

APP_NAME = "self_host_sandbox_demo"


def _managed_tool_placeholder() -> NoReturn:
    raise RuntimeError(
        "Managed Agent tools must be intercepted by managed_work_tool_runtime"
    )


def managed_bash(
    command: str | None = None,
    restart: bool | None = None,
    timeout_ms: int | None = None,
) -> dict[str, Any]:
    """Run a command in the Managed Agent worker."""
    _managed_tool_placeholder()


def managed_read(file_path: str, view_range: list[int] | None = None) -> dict[str, Any]:
    """Read a file from the Managed Agent worker."""
    _managed_tool_placeholder()


def managed_write(file_path: str, content: str) -> dict[str, Any]:
    """Write a file in the Managed Agent worker."""
    _managed_tool_placeholder()


def managed_edit(
    file_path: str,
    old_string: str,
    new_string: str,
    replace_all: bool = False,
) -> dict[str, Any]:
    """Replace text in a Managed Agent worker file."""
    _managed_tool_placeholder()


def managed_glob(pattern: str, path: str | None = None) -> dict[str, Any]:
    """List paths matching a glob in the Managed Agent worker."""
    _managed_tool_placeholder()


def managed_grep(pattern: str, path: str | None = None) -> dict[str, Any]:
    """Search files in the Managed Agent worker."""
    _managed_tool_placeholder()


for _tool, _name in (
    (managed_bash, "bash"),
    (managed_read, "read"),
    (managed_write, "write"),
    (managed_edit, "edit"),
    (managed_glob, "glob"),
    (managed_grep, "grep"),
):
    _tool.__name__ = _name

_MANAGED_TOOLS = {
    "bash": managed_bash,
    "read": managed_read,
    "write": managed_write,
    "edit": managed_edit,
    "glob": managed_glob,
    "grep": managed_grep,
}
_LOCAL_BUILTIN_TOOLS = {"web_fetch", "web_search"}
_CANONICAL_MANAGED_TOOLS = (
    "bash",
    "read",
    "write",
    "edit",
    "glob",
    "grep",
    "web_fetch",
    "web_search",
)
# Backward-compatible export used by the example's existing integrations/tests.
agent = default_agent


def managed_short_term_memory() -> ShortTermMemory:
    """Create the shared session backend required by stateless workers."""
    if db_url := os.getenv("VEADK_MANAGED_SESSION_DB_URL"):
        return ShortTermMemory(db_url=db_url)
    required = (
        "DATABASE_POSTGRESQL_HOST",
        "DATABASE_POSTGRESQL_USER",
        "DATABASE_POSTGRESQL_PASSWORD",
        "DATABASE_POSTGRESQL_DATABASE",
    )
    missing = [name for name in required if not os.getenv(name)]
    if missing:
        raise ValueError(
            "Managed Agent Loop requires PostgreSQL; set "
            "VEADK_MANAGED_SESSION_DB_URL or " + ", ".join(missing)
        )
    return ShortTermMemory(backend="postgresql")


def _field(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)


def _enabled(config: Any, default: bool) -> bool:
    value = _field(config, "enabled")
    return default if value is None else bool(value)


class ManagedCustomTool(BaseTool):
    """Expose an app-owned custom tool and wait for its Session result event."""

    def __init__(self, definition: Any, sdk: Any, session_id: str) -> None:
        self.input_schema = dict(_field(definition, "input_schema", {}) or {})
        self.sdk = sdk
        self.session_id = session_id
        super().__init__(
            name=str(_field(definition, "name", "")),
            description=str(_field(definition, "description", "") or ""),
        )

    def _get_declaration(self) -> types.FunctionDeclaration:
        return types.FunctionDeclaration(
            name=self.name,
            description=self.description,
            parameters_json_schema=self.input_schema,
        )

    async def run_async(self, *, args: dict[str, Any], tool_context: Any) -> Any:
        tool_use_id = str(
            getattr(tool_context, "function_call_id", "") or f"toolu_{uuid.uuid4().hex}"
        )
        await self.sdk.beta.sessions.events.send(
            self.session_id,
            events=[
                {
                    "type": "agent.custom_tool_use",
                    "id": tool_use_id,
                    "name": self.name,
                    "input": args,
                },
                {
                    "type": "session.status_idle",
                    "stop_reason": {
                        "type": "requires_action",
                        "action_type": "custom_tool_result",
                        "event_ids": [tool_use_id],
                    },
                },
            ],
        )
        result = await _wait_for_session_event(
            self.sdk,
            self.session_id,
            event_type="user.custom_tool_result",
            link_field="custom_tool_use_id",
            link_id=tool_use_id,
        )
        await self.sdk.beta.sessions.events.send(
            self.session_id, events=[{"type": "session.status_running"}]
        )
        content = _event_content_text(_field(result, "content"))
        return {
            "error" if bool(_field(result, "is_error", False)) else "result": content
        }


async def _wait_for_session_event(
    sdk: Any,
    session_id: str,
    *,
    event_type: str,
    link_field: str,
    link_id: str,
) -> Any:
    timeout = float(os.getenv("MANAGED_AGENT_ACTION_TIMEOUT_SECONDS", "3600"))
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        async for event in sdk.beta.sessions.events.list(
            session_id, limit=1000, order="desc"
        ):
            if (
                _field(event, "type") == event_type
                and _field(event, link_field) == link_id
            ):
                return event
        await asyncio.sleep(0.5)
    raise TimeoutError(f"timed out waiting for {event_type} for {link_id}")


def _event_content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    values = []
    for block in content or []:
        text = _field(block, "text")
        if text:
            values.append(str(text))
    return "\n".join(values) or "(no output)"


def _tool_permission_map(snapshot: Any) -> dict[str, str]:
    policies: dict[str, str] = {}
    for toolset in _field(snapshot, "tools", []) or []:
        toolset_type = str(_field(toolset, "type", ""))
        if toolset_type not in {"agent_toolset_20260401", "mcp_toolset"}:
            continue
        default = _field(toolset, "default_config", {}) or {}
        default_policy = str(
            _field(
                _field(default, "permission_policy", {}) or {}, "type", "always_allow"
            )
        )
        prefix = (
            f"mcp__{_field(toolset, 'mcp_server_name', '')}__"
            if toolset_type == "mcp_toolset"
            else ""
        )
        for config in _field(toolset, "configs", []) or []:
            name = str(_field(config, "name", ""))
            policy = str(
                _field(
                    _field(config, "permission_policy", {}) or {},
                    "type",
                    default_policy,
                )
            )
            policies[prefix + name] = policy
        policies[prefix + "*"] = default_policy
    return policies


def managed_agent_config(
    snapshot: Any,
    *,
    sdk: Any | None = None,
    session_id: str | None = None,
    skill_dirs: list[Path] | None = None,
) -> dict[str, Any]:
    """Translate a frozen Managed Agent snapshot to a VeADK Agent config."""
    model = _field(snapshot, "model", "")
    model_name = _field(model, "id", model)
    if not isinstance(model_name, str) or not model_name:
        model_name = os.getenv("MODEL_AGENT_NAME", "")
    if not model_name:
        raise ValueError("Managed Session agent snapshot has no model id")

    enabled_names: list[str] = []
    unsupported: list[str] = []
    skill_refs = list(_field(snapshot, "skills", []) or [])
    if skill_refs and skill_dirs is None:
        unsupported.append("skills (not materialized)")
    for toolset in _field(snapshot, "tools", []) or []:
        toolset_type = str(_field(toolset, "type", "") or "")
        if toolset_type == "mcp_toolset":
            continue
        if toolset_type == "custom":
            if sdk is None or not session_id:
                unsupported.append(
                    f"custom tool {_field(toolset, 'name', '')} (missing Session client)"
                )
            continue
        if toolset_type != "agent_toolset_20260401":
            unsupported.append(toolset_type or "unknown tool definition")
            continue
        default_config = _field(toolset, "default_config", {}) or {}
        default_enabled = _enabled(default_config, True)
        default_policy = _field(
            _field(default_config, "permission_policy", {}) or {}, "type"
        )
        configs = _field(toolset, "configs", []) or []
        configured: dict[str, Any] = {
            str(_field(config, "name", "")): config for config in configs
        }
        names = set(configured) | (
            set(_CANONICAL_MANAGED_TOOLS) if default_enabled else set()
        )
        for config in configs:
            name = str(_field(config, "name", ""))
            if not _enabled(config, default_enabled):
                names.discard(name)
        for name in sorted(names):
            config = configured.get(name, {})
            enabled = _enabled(config, default_enabled)
            if not enabled:
                continue
            policy = (
                _field(_field(config, "permission_policy", {}) or {}, "type")
                or default_policy
            )
            if policy not in {None, "always_allow", "always_ask"}:
                unsupported.append(f"{name} (permission_policy={policy})")
            enabled_names.append(name)

    unsupported.extend(
        name
        for name in enabled_names
        if name not in _MANAGED_TOOLS and name not in _LOCAL_BUILTIN_TOOLS
    )
    if unsupported:
        raise ValueError(
            f"Unsupported Managed Agent tools for VeADK: {', '.join(sorted(set(unsupported)))}"
        )
    tools = list(
        dict.fromkeys(
            _MANAGED_TOOLS[name] for name in enabled_names if name in _MANAGED_TOOLS
        )
    )
    if "web_fetch" in enabled_names:
        from veadk.tools.builtin_tools.web_fetch import web_fetch

        tools.append(web_fetch)
    if "web_search" in enabled_names:
        from veadk.tools.builtin_tools.web_search import web_search

        tools.append(web_search)
    resolved_instruction = str(_field(snapshot, "system", "") or "")
    if skill_dirs:
        resolved_instruction += skill_instructions(skill_dirs)
    tools.extend(mcp_toolsets(snapshot))
    if sdk is not None and session_id:
        tools.extend(
            ManagedCustomTool(tool, sdk, session_id)
            for tool in _field(snapshot, "tools", []) or []
            if _field(tool, "type") == "custom"
        )
    raw_name = str(_field(snapshot, "name", "managed_agent"))
    name = re.sub(r"[^A-Za-z0-9_]", "_", raw_name).strip("_") or "managed_agent"
    if name[0].isdigit():
        name = f"agent_{name}"
    return {
        "name": name,
        "description": str(_field(snapshot, "description", "") or ""),
        "instruction": resolved_instruction,
        "model_name": model_name,
        "tools": tools,
        "before_tool_callback": dispatch_runtime.before_tool_callback
        if tools
        else None,
    }


def managed_runner(
    short_term_memory: ShortTermMemory,
    snapshot: Any | None = None,
    *,
    before_tool_callback: Any | None = None,
    sdk: Any | None = None,
    session_id: str | None = None,
    skill_dirs: list[Path] | None = None,
) -> Runner:
    if snapshot is None:
        managed_agent = default_agent
    else:
        config = managed_agent_config(
            snapshot, sdk=sdk, session_id=session_id, skill_dirs=skill_dirs
        )
        if before_tool_callback is not None and config["tools"]:
            config["before_tool_callback"] = before_tool_callback
        managed_agent = Agent(**config)
    return Runner(
        agent=managed_agent,
        app_name=APP_NAME,
        short_term_memory=short_term_memory,
    )


def _managed_tool_environment() -> dict[str, str]:
    """Return a subprocess environment without control-plane credentials."""
    allowed = {"LANG", "LC_ALL", "PATH", "TERM", "TZ"}
    return {key: value for key, value in os.environ.items() if key in allowed}


async def _run_managed_tool(
    tool_name: str, arguments: dict[str, Any], *, workdir: Path
) -> dict[str, Any]:
    arguments = _confined_tool_arguments(tool_name, arguments, workdir=workdir)
    command, timeout = SelfHostSandboxClient.tool_to_bash(
        tool_name, arguments, default_timeout=120
    )
    process = await asyncio.create_subprocess_exec(
        "/bin/bash",
        "-c",
        command,
        cwd=workdir,
        env=_managed_tool_environment(),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=True,
    )

    async def kill_process_group() -> None:
        if process.returncode is None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        await asyncio.shield(process.wait())

    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
    except TimeoutError:
        await kill_process_group()
        return {
            "status": "failed",
            "exit_code": -1,
            "stdout": "",
            "stderr": f"Timed out after {timeout:g}s",
        }
    except asyncio.CancelledError:
        await kill_process_group()
        raise
    return {
        "status": "completed" if process.returncode == 0 else "failed",
        "exit_code": int(process.returncode or 0),
        "stdout": stdout.decode(errors="replace"),
        "stderr": stderr.decode(errors="replace"),
    }


def _confined_tool_arguments(
    tool_name: str, arguments: dict[str, Any], *, workdir: Path
) -> dict[str, Any]:
    """Resolve file-tool paths inside the worker's configured root."""
    confined = dict(arguments)
    if tool_name == "glob":
        pattern = str(confined.get("pattern") or "")
        if PurePosixPath(pattern).is_absolute() or ".." in PurePosixPath(pattern).parts:
            raise ValueError("glob pattern must be relative and cannot contain '..'")
    path_key = "file_path" if tool_name in {"read", "write", "edit"} else "path"
    if tool_name not in {"read", "write", "edit", "glob", "grep"}:
        return confined
    raw_path = str(confined.get(path_key) or ".")
    root = workdir.resolve()
    candidate = Path(raw_path)
    resolved = (candidate if candidate.is_absolute() else root / candidate).resolve()
    try:
        resolved.relative_to(root)
    except ValueError as error:
        raise ValueError(
            f"{tool_name} path {raw_path!r} is outside MANAGED_AGENT_WORKDIR"
        ) from error
    confined[path_key] = str(resolved)
    return confined


def managed_work_tool_runtime(
    sdk: Any, session_id: str, *, workdir: Path, snapshot: Any | None = None
) -> RuntimeProvider:
    """Execute tools inside this worker and persist canonical tool events."""

    policies = _tool_permission_map(snapshot) if snapshot is not None else {}
    local_runtime = LocalRuntimeProvider()

    async def confirm(tool_call: ToolCall, event: dict[str, Any]) -> bool:
        wildcard = (
            tool_call.name.rsplit("__", 1)[0] + "__*" if "__" in tool_call.name else "*"
        )
        policy = policies.get(tool_call.name, policies.get(wildcard, "always_allow"))
        if policy != "always_ask":
            return True
        if event["type"] == "agent.tool_use":
            event["evaluated_permission"] = "ask"
        await sdk.beta.sessions.events.send(
            session_id,
            events=[
                event,
                {
                    "type": "session.status_idle",
                    "stop_reason": {
                        "type": "requires_action",
                        "action_type": "tool_confirmation",
                        "event_ids": [event["id"]],
                    },
                },
            ],
        )
        verdict = await _wait_for_session_event(
            sdk,
            session_id,
            event_type="user.tool_confirmation",
            link_field="tool_use_id",
            link_id=str(event["id"]),
        )
        await sdk.beta.sessions.events.send(
            session_id, events=[{"type": "session.status_running"}]
        )
        return _field(verdict, "result") == "allow"

    async def publish_result(
        kind: str, tool_use_id: str, result: Any, is_error: bool
    ) -> None:
        content = (
            result
            if isinstance(result, str)
            else json.dumps(result, ensure_ascii=False, default=str)
        )
        await sdk.beta.sessions.events.send(
            session_id,
            events=[
                {
                    "type": f"agent.{kind}_result",
                    f"{kind}_use_id": tool_use_id,
                    "content": content,
                    "is_error": is_error,
                }
            ],
        )

    async def execute(tool_call: ToolCall) -> Any:
        tool_use_id = tool_call.id or f"toolu_{uuid.uuid4().hex}"
        if isinstance(tool_call.tool, ManagedCustomTool):
            return await local_runtime.execute(tool_call)
        is_mcp = hasattr(tool_call.tool, "_mcp_tool") or hasattr(
            tool_call.tool, "_mcp_session_manager"
        )
        if is_mcp:
            server_name = str(
                getattr(tool_call.tool, "managed_agents_mcp_server_name", "mcp")
            )
            prefix = f"mcp__{server_name}__"
            original_name = (
                tool_call.name.removeprefix(prefix)
                if tool_call.name.startswith(prefix)
                else tool_call.name
            )
            event = {
                "type": "agent.mcp_tool_use",
                "id": tool_use_id,
                "mcp_server_name": server_name,
                "name": original_name or tool_call.name,
                "input": tool_call.arguments,
            }
            if not await confirm(tool_call, event):
                return {"error": "tool call denied by user"}
            if (
                policies.get(
                    tool_call.name, policies.get("mcp__" + server_name + "__*")
                )
                != "always_ask"
            ):
                await sdk.beta.sessions.events.send(session_id, events=[event])
            try:
                result = await local_runtime.execute(tool_call)
            except Exception as error:
                await publish_result("mcp_tool", tool_use_id, str(error), True)
                raise
            await publish_result("mcp_tool", tool_use_id, result, False)
            return result

        if (
            tool_call.name not in _MANAGED_TOOLS
            and tool_call.name not in _LOCAL_BUILTIN_TOOLS
        ):
            return await local_runtime.execute(tool_call)

        event = {
            "type": "agent.tool_use",
            "id": tool_use_id,
            "name": tool_call.name,
            "input": tool_call.arguments,
            "evaluated_permission": "allow",
        }
        if not await confirm(tool_call, event):
            return {"error": "tool call denied by user"}
        if event["evaluated_permission"] != "ask":
            await sdk.beta.sessions.events.send(session_id, events=[event])
        try:
            if tool_call.name in _MANAGED_TOOLS:
                result = await _run_managed_tool(
                    tool_call.name, tool_call.arguments, workdir=workdir
                )
            else:
                result = await local_runtime.execute(tool_call)
        except Exception as error:  # noqa: BLE001 -- tool failures become result events
            result = {
                "status": "failed",
                "exit_code": -1,
                "stdout": "",
                "stderr": str(error),
            }
        if (
            isinstance(result, dict)
            and {"exit_code", "stdout", "stderr"} <= result.keys()
        ):
            content = "\n".join(
                part
                for part in (
                    f"exit={result['exit_code']}",
                    str(result["stdout"]),
                    str(result["stderr"]),
                )
                if part
            )
            is_error = result.get("status") == "failed"
        else:
            content = (
                result
                if isinstance(result, str)
                else json.dumps(result, ensure_ascii=False, default=str)
            )
            is_error = False
        await sdk.beta.sessions.events.send(
            session_id,
            events=[
                {
                    "type": "agent.tool_result",
                    "tool_use_id": tool_use_id,
                    "content": content,
                    "is_error": is_error,
                }
            ],
        )
        return result

    class ManagedWorkRuntime(RuntimeProvider):
        def __init__(self) -> None:
            super().__init__(name="managed_agents_work_runtime")

        async def execute(self, tool_call: ToolCall) -> Any:
            return await execute(tool_call)

    return ManagedWorkRuntime()


async def close_managed_runner(runner: Runner) -> None:
    for tool in getattr(runner.agent, "tools", []) or []:
        close = getattr(tool, "close", None)
        if not callable(close):
            continue
        result = close()
        if inspect.isawaitable(result):
            await result


async def serve_managed_agent_worker(
    *,
    worker_id: str | None = None,
    max_work_items: int | None = None,
    readiness_probe: bool = False,
) -> int:
    """Poll Task Server work and execute claimed sessions with VeADK."""
    session_client = SelfHostSandboxClient()
    short_term_memory = managed_short_term_memory()
    resolved_worker_id = worker_id or f"{socket.gethostname()}-{uuid.uuid4().hex[:12]}"
    default_workdir = (
        Path(__file__).resolve().parent / ".local-managed-agents" / "workspace"
    )
    workdir = Path(os.getenv("MANAGED_AGENT_WORKDIR", default_workdir)).resolve()
    workdir.mkdir(parents=True, exist_ok=True)
    ready_file = Path(
        os.getenv("MANAGED_AGENT_READY_FILE", "/tmp/managed-agent-worker-ready")
    )
    ready_file.unlink(missing_ok=True)

    try:
        async with session_client.create_async_client() as sdk:
            if readiness_probe:
                pending = await sdk.beta.environments.work.poll(
                    session_client.environment_id,
                    block_ms=1,
                    anthropic_worker_id=resolved_worker_id,
                    extra_headers=session_client._default_headers,
                )
                if pending is not None:
                    raise RuntimeError(
                        "readiness probe requires an empty isolated work queue"
                    )

            async def handle(work_item: Any, scoped_sdk: Any) -> None:
                if getattr(getattr(work_item, "data", None), "type", None) != "session":
                    return
                session_id = str(work_item.data.id)
                print(
                    f"MANAGED_AGENT_WORK_CLAIM worker_id={resolved_worker_id} "
                    f"work_id={work_item.id} session_id={session_id}",
                    flush=True,
                )
                try:
                    await scoped_sdk.beta.environments.work.update(
                        work_item.id,
                        environment_id=session_client.environment_id,
                        metadata={"managed_agent_worker_id": resolved_worker_id},
                    )
                    session = await scoped_sdk.beta.sessions.retrieve(session_id)
                    from managed_session_resources import (
                        cleanup_session_skills,
                        materialize_session_skills,
                        session_workdir,
                    )

                    item_workdir = session_workdir(workdir, session_id)
                    downloaded_skills = await materialize_session_skills(
                        session, item_workdir, client=scoped_sdk
                    )
                    tool_runtime = managed_work_tool_runtime(
                        scoped_sdk,
                        session_id,
                        workdir=item_workdir,
                        snapshot=session.agent,
                    )
                    runner = managed_runner(
                        short_term_memory,
                        session.agent,
                        before_tool_callback=tool_runtime.before_tool_callback,
                        sdk=scoped_sdk,
                        session_id=session_id,
                        skill_dirs=downloaded_skills,
                    )
                    try:
                        await ManagedAgentsLoop(
                            runner=runner,
                            session_id=session_id,
                        ).run_pending(scoped_sdk, max_turns=1)
                    finally:
                        await close_managed_runner(runner)
                        await cleanup_session_skills(downloaded_skills)
                except asyncio.CancelledError:
                    raise
                except Exception as error:
                    await scoped_sdk.beta.sessions.events.send(
                        session_id,
                        events=[{"type": "session.error", "error": str(error)}],
                    )
                    raise

            dispatcher = sdk.beta.environments.work.dispatcher(
                handler=handle,
                environment_id=session_client.environment_id,
                environment_key=session_client.bearer_token,
                worker_id=resolved_worker_id,
                extra_headers=session_client._default_headers,
            )
            ready_file.parent.mkdir(parents=True, exist_ok=True)
            ready_file.write_text(resolved_worker_id + "\n")
            print(
                f"MANAGED_AGENT_WORKER_READY worker_id={resolved_worker_id} "
                f"environment_id={session_client.environment_id}",
                flush=True,
            )
            return await dispatcher.run(max_items=max_work_items)
    finally:
        ready_file.unlink(missing_ok=True)


async def serve_feishu_channel(stop_event: asyncio.Event | None = None) -> None:
    """Serve Feishu conversations until the process receives a stop signal."""
    runner = enable_sandbox_turn_lifecycle(
        Runner(agent=default_agent, app_name=APP_NAME)
    )
    channel = FeishuChannelExtension(
        runner=runner,
        streaming=True,
        show_thinking=True,
        show_tool_calls=True,
        show_tool_results=True,
        separate_tool_call_cards=True,
        separate_thinking_card=True,
        create_topic=True,
    )
    loop = asyncio.get_running_loop()
    shutdown_event = stop_event or asyncio.Event()

    if stop_event is None:
        for signal_number in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(signal_number, shutdown_event.set)
            except NotImplementedError:  # pragma: no cover - Windows fallback
                signal.signal(signal_number, lambda *_: shutdown_event.set())

    channel.start(loop)
    print("Feishu Channel is running. Press Ctrl+C to stop.")
    try:
        await shutdown_event.wait()
    finally:
        await channel.shutdown()


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--prompt",
        default=(
            "Use the bash tool to run: printf 'veadk-self-host-ok'. "
            "Then reply with the exact output."
        ),
    )
    parser.add_argument("--session-id", default=None)
    parser.add_argument(
        "--managed-agent-loop",
        action="store_true",
        help=(
            "Consume one pending user.message from the existing Managed Session with "
            "PostgreSQL-backed state, then exit."
        ),
    )
    parser.add_argument(
        "--managed-agent-worker",
        action="store_true",
        help="Continuously poll and execute Managed Agent session work.",
    )
    parser.add_argument("--worker-id", default=None)
    parser.add_argument("--max-work-items", type=int, default=None)
    parser.add_argument(
        "--managed-agent-readiness-probe",
        action="store_true",
        help="Require one successful empty work poll before logging worker readiness.",
    )
    parser.add_argument(
        "--feishu",
        action="store_true",
        help="Keep running and serve conversations through the Feishu bot channel.",
    )
    args = parser.parse_args()

    if args.managed_agent_loop:
        session_client = SelfHostSandboxClient(session_id=args.session_id)
        if not session_client.session_id:
            parser.error(
                "--managed-agent-loop requires --session-id, ANTHROPIC_SESSION_ID, or SANDBOX_SESSION_ID"
            )
        sandbox_sessions.bind(session_client.session_id, session_client)
        runner = managed_runner(managed_short_term_memory())
        turns = await ManagedAgentsLoop(
            runner=runner,
            session_client=session_client,
        ).run(max_turns=1)
        print(f"Managed Agents loop completed {turns} turn(s).")
        return

    if args.managed_agent_worker:
        count = await serve_managed_agent_worker(
            worker_id=args.worker_id,
            max_work_items=args.max_work_items,
            readiness_probe=args.managed_agent_readiness_probe,
        )
        print(f"Managed Agents worker completed {count} work item(s).")
        return

    if args.feishu:
        await serve_feishu_channel()
        return

    session_id = args.session_id or f"veadk-{uuid.uuid4()}"
    runner = enable_sandbox_turn_lifecycle(
        Runner(agent=default_agent, app_name=APP_NAME)
    )
    output = await runner.run(messages=args.prompt, session_id=session_id)
    print(output)


if __name__ == "__main__":
    asyncio.run(main())
