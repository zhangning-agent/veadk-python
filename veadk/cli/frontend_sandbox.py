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

"""Reusable AgentKit Sandbox access for temporary Studio conversations."""

from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import json
import os
import re
import secrets
import shlex
import time
import uuid
import urllib.error
import urllib.request

from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from fastapi import Request, WebSocket, WebSocketDisconnect
from fastapi.responses import Response

from veadk.utils.logger import get_logger

logger = get_logger(__name__)

STUDIO_SANDBOX_TOOL_NAME = "veadk-studio-codex"
STUDIO_SANDBOX_TTL_SECONDS = 3_600
STUDIO_SANDBOX_MAX_ACTIVE = 20
_SANDBOX_CHAT_TOOL_ENV = "SANDBOX_CHAT_CODEX"
_SANDBOX_OPENCLAW_TOOL_ENV = "SANDBOX_OPENCLAW_TOOL"
_SANDBOX_HERMES_TOOL_ENV = "SANDBOX_HERMES_TOOL"
_SANDBOX_MODEL_ENV_KEYS = (
    "ARK_BASE_URL",
    "MODEL_AGENT_API_KEY",
    "MODEL_AGENT_NAME",
)
_CREATE_SESSION_START_FAIL_CODE = "ErrCreateSessionFail"
_SESSION_NOT_FOUND_CODE = "InvalidResource.NotFound"
_SENSITIVE_PATTERN = re.compile(
    r"(?i)((?:api[_-]?key|access[_-]?key|secret|token|authorization|password)"
    r"\s*[:=]\s*)(?:[\"'][^\"']*[\"']|[^\s,;]+)"
)
_OPENCLAW_PROXY_RESET_SCRIPT = (
    "try{for(const key of Object.keys(localStorage)){"
    "if(key.startsWith('openclaw.control.settings.v1'))localStorage.removeItem(key)"
    "}}catch{}"
)
_OPENCLAW_PROXY_RESET_TAG = (
    f"<script>{_OPENCLAW_PROXY_RESET_SCRIPT}</script>".encode()
)
_OPENCLAW_PROXY_RESET_HASH = (
    "'sha256-"
    + base64.b64encode(
        hashlib.sha256(_OPENCLAW_PROXY_RESET_SCRIPT.encode()).digest()
    ).decode()
    + "'"
)


class SandboxError(RuntimeError):
    """Base error safe to translate at the HTTP boundary."""

    code = "SANDBOX_ERROR"
    retryable = False


class SandboxConfigurationError(SandboxError):
    """Required server-side Sandbox configuration is missing."""

    code = "SANDBOX_NOT_CONFIGURED"


class SandboxProvisioningError(SandboxError):
    """AgentKit could not provision the requested Sandbox resource."""

    code = "SANDBOX_PROVISIONING_FAILED"
    retryable = True


class SandboxSessionNotFoundError(SandboxError):
    """The temporary conversation does not exist or is not owned by the user."""

    code = "SANDBOX_SESSION_NOT_FOUND"


class SandboxInvocationError(SandboxError):
    """The coding agent failed while serving a conversation turn."""

    code = "SANDBOX_INVOCATION_FAILED"
    retryable = True


class SandboxCapacityError(SandboxError):
    """The user or Studio has reached the temporary-session limit."""

    code = "SANDBOX_CAPACITY_EXCEEDED"
    retryable = True


def _safe_error_message(error: object) -> str:
    """Return a bounded credential-safe diagnostic message."""
    message = str(error).strip()
    for key, value in os.environ.items():
        if (
            value
            and len(value) >= 8
            and any(
                token in key.upper() for token in ("KEY", "SECRET", "TOKEN", "PASSWORD")
            )
        ):
            message = message.replace(value, "***")
    message = re.sub(r"(?i)(\bbearer\s+)\S+", r"\1***", message)
    message = _SENSITIVE_PATTERN.sub(r"\1***", message)
    message = re.sub(r"https?://[^\s?]+\?[^\s]+", "[sandbox endpoint]", message)
    return message[:1000] or type(error).__name__


def _safe_public_value(value: object, depth: int = 0) -> object:
    """Return a bounded, credential-safe value for browser-visible events."""
    if depth >= 4:
        return "…"
    if isinstance(value, str):
        return _safe_error_message(value)
    if isinstance(value, dict):
        result: dict[str, object] = {}
        for key, item in list(value.items())[:30]:
            safe_key = _safe_error_message(key)[:100]
            if any(
                marker in str(key).upper()
                for marker in ("KEY", "PASSWORD", "SECRET", "TOKEN", "AUTHORIZATION")
            ):
                result[safe_key] = "***"
            else:
                result[safe_key] = _safe_public_value(item, depth + 1)
        return result
    if isinstance(value, list):
        return [_safe_public_value(item, depth + 1) for item in value[:30]]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return _safe_error_message(value)


def _public_event_text(value: object) -> str:
    """Extract readable text from a Codex event field."""
    if isinstance(value, str):
        return _safe_error_message(value)
    if isinstance(value, list):
        return "\n".join(filter(None, (_public_event_text(item) for item in value)))
    if isinstance(value, dict):
        return _public_event_text(
            value.get("text") or value.get("content") or value.get("summary")
        )
    return ""


@dataclass(frozen=True)
class SandboxCloudSession:
    """Remote AgentKit Sandbox session data kept only on the server."""

    tool_id: str
    instance_id: str
    user_session_id: str
    endpoint: str


@dataclass
class SandboxConversation:
    """Server-side state for one non-persistent Studio conversation."""

    session_id: str
    owner_id: str
    cloud: SandboxCloudSession | None = None
    thread_id: str | None = None
    openclaw_preview_url: str | None = None
    hermes_preview_url: str | None = None
    proxy_token: str = field(default_factory=lambda: secrets.token_urlsafe(24))
    created_at: float = field(default_factory=time.time)
    expires_at: float = field(
        default_factory=lambda: time.monotonic() + STUDIO_SANDBOX_TTL_SECONDS
    )
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


@dataclass(frozen=True)
class SandboxStreamEvent:
    """One typed event emitted while the coding agent is running."""

    kind: str = ""
    item_id: str = ""
    status: str = "done"
    text: str = ""
    name: str = ""
    arguments: object | None = None
    response: object | None = None
    thread_id: str | None = None


class SandboxCloudGateway(Protocol):
    """AgentKit operations needed by the Studio conversation service."""

    async def create_session(
        self,
        tool_id: str,
        envs: dict[str, str] | None = None,
    ) -> SandboxCloudSession:
        """Create a fresh remote Sandbox session."""
        raise NotImplementedError

    async def delete_session(self, session: SandboxCloudSession) -> None:
        """Delete a remote Sandbox session."""
        raise NotImplementedError

    async def stream_codex(
        self,
        session: SandboxCloudSession,
        prompt: str,
        thread_id: str | None,
    ) -> AsyncIterator[SandboxStreamEvent]:
        """Stream one turn from the coding agent inside the Sandbox."""
        if False:
            yield SandboxStreamEvent()

    async def start_openclaw(self, session: SandboxCloudSession) -> str:
        """Start OpenClaw and return a short-lived browser preview URL."""
        raise NotImplementedError

    async def start_hermes(self, session: SandboxCloudSession) -> str:
        """Start Hermes and return a short-lived browser preview URL."""
        raise NotImplementedError

    def terminal_preview_url(self, session: SandboxCloudSession) -> str:
        """Return the browser-accessible terminal URL for a Sandbox session."""
        raise NotImplementedError

    async def drain(self) -> None:
        """Wait for asynchronous cloud cleanup started by cancelled requests."""
        raise NotImplementedError


class AgentkitSandboxGateway:
    """AgentKit SDK and Sandbox terminal adapter.

    The AgentKit management SDK is synchronous, so each API call runs in a
    worker thread. Conversation output uses the Sandbox terminal WebSocket;
    the session endpoint, including its authorization query, never leaves this
    process.
    """

    def __init__(
        self,
        client: Any | Callable[[], Any],
    ) -> None:
        self._client = client
        self._background_tasks: set[asyncio.Task[None]] = set()

    def _track_cleanup(self, coroutine: Any) -> None:
        task = asyncio.create_task(coroutine)
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

    async def _call(self, method_name: str, request: Any) -> Any:
        client = self._client() if callable(self._client) else self._client
        method = getattr(client, method_name)
        return await asyncio.to_thread(method, request)

    async def _reconcile_created_session(
        self, tool_id: str, user_session_id: str
    ) -> SandboxCloudSession | None:
        from agentkit.sdk.tools import types as tools_types

        for attempt in range(6):
            response = await self._call(
                "list_sessions",
                tools_types.ListSessionsRequest(
                    ToolId=tool_id,
                    MaxResults=10,
                    Filters=[
                        tools_types.FiltersItemForListSessions(
                            Name="UserSessionId", Values=[user_session_id]
                        )
                    ],
                ),
            )
            for session in response.session_infos or []:
                if session.user_session_id != user_session_id:
                    continue
                if (session.status or "").lower() != "ready":
                    continue
                if session.session_id and session.endpoint:
                    return SandboxCloudSession(
                        tool_id=tool_id,
                        instance_id=session.session_id,
                        user_session_id=user_session_id,
                        endpoint=session.endpoint,
                    )
            if attempt < 5:
                await asyncio.sleep(5)
        return None

    async def create_session(
        self,
        tool_id: str,
        envs: dict[str, str] | None = None,
    ) -> SandboxCloudSession:
        from agentkit.sdk.tools import types as tools_types

        user_session_id = f"studio-{uuid.uuid4()}"
        session_envs = dict(envs or {})
        request = tools_types.CreateSessionRequest(
            ToolId=tool_id,
            Ttl=STUDIO_SANDBOX_TTL_SECONDS,
            TtlUnit="second",
            UserSessionId=user_session_id,
            Envs=[
                tools_types.EnvsItemForCreateSession(Key=key, Value=value)
                for key, value in session_envs.items()
            ]
            or None,
        )
        create_task = asyncio.create_task(self._call("create_session", request))
        try:
            response = await asyncio.shield(create_task)
        except asyncio.CancelledError:
            self._track_cleanup(
                self._cleanup_cancelled_create(
                    create_task, tool_id=tool_id, user_session_id=user_session_id
                )
            )
            raise
        except Exception as error:
            if _CREATE_SESSION_START_FAIL_CODE not in str(error):
                raise SandboxProvisioningError(
                    f"创建 AgentKit 沙箱会话失败：{_safe_error_message(error)}"
                ) from error
            reconciled = await self._reconcile_created_session(tool_id, user_session_id)
            if reconciled is not None:
                return reconciled
            raise SandboxProvisioningError(
                "AgentKit 返回会话启动失败，且未找到已就绪的会话。"
            ) from error

        instance_id = (response.session_id or "").strip()
        endpoint = (response.endpoint or "").strip()
        if not instance_id or not endpoint:
            raise SandboxProvisioningError(
                "AgentKit 创建会话响应缺少 SessionId 或 Endpoint。"
            )
        return SandboxCloudSession(
            tool_id=tool_id,
            instance_id=instance_id,
            user_session_id=response.user_session_id or user_session_id,
            endpoint=endpoint,
        )

    async def _cleanup_cancelled_create(
        self,
        create_task: asyncio.Task[Any],
        *,
        tool_id: str,
        user_session_id: str,
    ) -> None:
        """Delete a cloud session whose synchronous create outlived its request."""
        cloud: SandboxCloudSession | None = None
        try:
            response = await create_task
            if response.session_id and response.endpoint:
                cloud = SandboxCloudSession(
                    tool_id=tool_id,
                    instance_id=response.session_id,
                    user_session_id=response.user_session_id or user_session_id,
                    endpoint=response.endpoint,
                )
        except Exception as error:
            if _CREATE_SESSION_START_FAIL_CODE in str(error):
                cloud = await self._reconcile_created_session(tool_id, user_session_id)
            else:
                logger.warning(
                    "Cancelled Sandbox create failed before cleanup: %s",
                    _safe_error_message(error),
                )
        if cloud is not None:
            try:
                await self.delete_session(cloud)
            except SandboxError as error:
                logger.warning(
                    "Failed to clean up cancelled Sandbox create: %s",
                    _safe_error_message(error),
                )

    async def delete_session(self, session: SandboxCloudSession) -> None:
        from agentkit.sdk.tools import types as tools_types

        try:
            await self._call(
                "delete_session",
                tools_types.DeleteSessionRequest(
                    ToolId=session.tool_id,
                    SessionId=session.instance_id,
                ),
            )
        except Exception as error:
            if _SESSION_NOT_FOUND_CODE in str(error):
                return
            raise SandboxProvisioningError(
                f"删除 AgentKit 沙箱会话失败：{_safe_error_message(error)}"
            ) from error

    async def drain(self) -> None:
        if self._background_tasks:
            await asyncio.gather(*tuple(self._background_tasks), return_exceptions=True)

    @staticmethod
    def _terminal_url(endpoint: str) -> str:
        parsed = urlsplit(endpoint)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise SandboxProvisioningError("AgentKit 沙箱返回了无效 Endpoint。")
        scheme = "wss" if parsed.scheme == "https" else "ws"
        path = f"{parsed.path.rstrip('/')}/v1/shell/ws"
        return urlunsplit((scheme, parsed.netloc, path, parsed.query, ""))

    @staticmethod
    def _endpoint_url(endpoint: str, path: str) -> str:
        parsed = urlsplit(endpoint)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise SandboxProvisioningError("AgentKit 沙箱返回了无效 Endpoint。")
        prefix = parsed.path.rstrip("/")
        return urlunsplit(
            (parsed.scheme, parsed.netloc, f"{prefix}/{path.lstrip('/')}", parsed.query, "")
        )

    @staticmethod
    def _sibling_endpoint_url(endpoint: str, path: str) -> str:
        parsed = urlsplit(endpoint)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise SandboxProvisioningError("AgentKit 沙箱返回了无效 Endpoint。")
        prefix = parsed.path.rstrip("/")
        for suffix in ("/openclaw", "/hermes", "/terminal"):
            if prefix.endswith(suffix):
                prefix = prefix[: -len(suffix)]
                break
        return urlunsplit(
            (parsed.scheme, parsed.netloc, f"{prefix}/{path.lstrip('/')}", parsed.query, "")
        )

    @staticmethod
    def _json_request(
        url: str,
        *,
        method: str = "GET",
        payload: dict[str, object] | None = None,
        timeout: float = 30,
    ) -> object:
        data = json.dumps(payload).encode() if payload is not None else None
        request = urllib.request.Request(
            url,
            data=data,
            method=method,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read()
        return json.loads(body) if body else {}

    async def start_openclaw(self, session: SandboxCloudSession) -> str:
        """Wait for the OpenClaw WebUI endpoint and return its URL."""
        return await self._wait_endpoint_ready(
            self._endpoint_url(session.endpoint, "openclaw"),
            kind="OpenClaw",
        )

    async def start_hermes(self, session: SandboxCloudSession) -> str:
        """Wait for the Hermes WebUI endpoint and return its URL."""
        return await self._wait_endpoint_ready(
            self._endpoint_url(session.endpoint, "hermes"),
            kind="Hermes",
        )

    def terminal_preview_url(self, session: SandboxCloudSession) -> str:
        return self._endpoint_url(session.endpoint, "terminal")

    async def _wait_endpoint_ready(self, preview_url: str, *, kind: str) -> str:
        deadline = time.monotonic() + 180
        last_error: object = f"{kind} 尚未就绪"

        def _probe() -> None:
            request = urllib.request.Request(
                preview_url, method="GET", headers={"Accept": "text/html"}
            )
            with urllib.request.urlopen(request, timeout=10) as response:
                response.read(1)

        while time.monotonic() < deadline:
            try:
                await asyncio.to_thread(_probe)
                break
            except (urllib.error.URLError, TimeoutError) as error:
                last_error = error
                await asyncio.sleep(3)
        else:
            raise SandboxProvisioningError(
                f"{kind} 启动超时：{_safe_error_message(last_error)}"
            )
        return preview_url

    @staticmethod
    def _command(thread_id: str | None, input_marker: str, marker: str) -> str:
        stdin = (
            "python3 -c 'import base64,sys;"
            "sys.stdout.buffer.write(base64.b64decode(sys.stdin.buffer.readline()))'"
        )
        if thread_id:
            invocation = (
                "codex exec resume --json --dangerously-bypass-approvals-and-sandbox "
                f"{shlex.quote(thread_id)} -"
            )
        else:
            invocation = (
                "codex exec --json --color never --skip-git-repo-check "
                "--dangerously-bypass-approvals-and-sandbox -"
            )
        return (
            f"stty -echo; printf '\\n{input_marker}\\n'; "
            f"{stdin} | {invocation}; __veadk_status=$?; stty echo; "
            f"printf '\\n{marker}%s\\n' \"$__veadk_status\"; exit"
        )

    @staticmethod
    def _completion_status(line: str, marker: str) -> int | None:
        match = re.fullmatch(rf"{re.escape(marker)}(\d+)", line.strip())
        return int(match.group(1)) if match else None

    @staticmethod
    def _parse_codex_event(line: str) -> SandboxStreamEvent | None:
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            return None
        if not isinstance(event, dict):
            return None
        if event.get("type") == "thread.started":
            thread_id = event.get("thread_id")
            if isinstance(thread_id, str) and thread_id:
                return SandboxStreamEvent(thread_id=thread_id)
            return None
        event_type = event.get("type")
        if event_type not in {"item.started", "item.completed"}:
            return None
        item = event.get("item")
        if not isinstance(item, dict):
            return None
        item_type = str(item.get("type") or "")
        item_id = str(item.get("id") or f"item-{uuid.uuid4().hex}")[:100]
        status = "running" if event_type == "item.started" else "done"

        if item_type == "reasoning":
            text = _public_event_text(
                item.get("text") or item.get("summary") or item.get("content")
            )
            return (
                SandboxStreamEvent(
                    kind="thinking",
                    item_id=item_id,
                    status=status,
                    text=text,
                )
                if text
                else None
            )
        if item_type == "agent_message":
            text = _public_event_text(item.get("text"))
            return SandboxStreamEvent(kind="text", text=text) if text else None
        if item_type == "command_execution":
            response = None
            if status == "done":
                response = {
                    "status": _safe_public_value(item.get("status") or "completed"),
                    "exitCode": _safe_public_value(item.get("exit_code")),
                    "output": _safe_public_value(item.get("aggregated_output")),
                }
            return SandboxStreamEvent(
                kind="tool",
                item_id=item_id,
                status=status,
                name="运行命令",
                arguments={"command": _safe_public_value(item.get("command") or "")},
                response=response,
            )
        if item_type in {"file_change", "file_changes"}:
            changes = item.get("changes")
            arguments = (
                {"changes": _safe_public_value(changes)}
                if isinstance(changes, list)
                else {"path": _safe_public_value(item.get("path") or "")}
            )
            return SandboxStreamEvent(
                kind="tool",
                item_id=item_id,
                status=status,
                name="修改文件",
                arguments=arguments,
                response={"status": _safe_public_value(item.get("status") or status)}
                if status == "done"
                else None,
            )
        if item_type == "mcp_tool_call":
            server = _safe_error_message(item.get("server") or "MCP")[:100]
            tool = _safe_error_message(item.get("tool") or item.get("name") or "工具")[
                :100
            ]
            return SandboxStreamEvent(
                kind="tool",
                item_id=item_id,
                status=status,
                name=f"MCP · {server}/{tool}",
                arguments=_safe_public_value(item.get("arguments")),
                response=_safe_public_value(item.get("result") or item.get("error"))
                if status == "done"
                else None,
            )
        if item_type in {"web_search", "web_search_call"}:
            return SandboxStreamEvent(
                kind="tool",
                item_id=item_id,
                status=status,
                name="网络搜索",
                arguments=_safe_public_value(
                    item.get("query") or item.get("arguments")
                ),
                response=_safe_public_value(item.get("result") or item.get("output"))
                if status == "done"
                else None,
            )
        text = _public_event_text(item.get("text") or item.get("summary"))
        return (
            SandboxStreamEvent(
                kind="thinking",
                item_id=item_id,
                status=status,
                text=text,
            )
            if text
            else None
        )

    async def stream_codex(
        self,
        session: SandboxCloudSession,
        prompt: str,
        thread_id: str | None,
    ) -> AsyncIterator[SandboxStreamEvent]:
        import websockets

        input_marker = f"__VEADK_INPUT_{uuid.uuid4().hex}__"
        marker = f"__VEADK_DONE_{uuid.uuid4().hex}__"
        command = self._command(thread_id, input_marker, marker)
        encoded_prompt = base64.b64encode(prompt.encode("utf-8")).decode("ascii")
        buffer = ""
        exit_status: int | None = None
        prompt_sent = False
        try:
            async with websockets.connect(
                self._terminal_url(session.endpoint),
                open_timeout=30,
                close_timeout=5,
                max_size=8 * 1024 * 1024,
            ) as websocket:
                await websocket.send(
                    json.dumps({"type": "resize", "data": {"cols": 120, "rows": 40}})
                )
                async with asyncio.timeout(30):
                    while True:
                        payload = json.loads(await websocket.recv())
                        if payload.get("type") == "ping":
                            await websocket.send(
                                json.dumps(
                                    {"type": "pong", "data": payload.get("data")}
                                )
                            )
                        if payload.get("type") == "ready":
                            await websocket.send(
                                json.dumps({"type": "input", "data": f"{command}\n"})
                            )
                            break

                try:
                    async with asyncio.timeout(600):
                        async for raw_message in websocket:
                            payload = json.loads(raw_message)
                            if payload.get("type") == "ping":
                                await websocket.send(
                                    json.dumps(
                                        {"type": "pong", "data": payload.get("data")}
                                    )
                                )
                                continue
                            if payload.get("type") == "error":
                                raise SandboxInvocationError(
                                    _safe_error_message(
                                        payload.get("data") or "terminal error"
                                    )
                                )
                            if payload.get("type") != "output":
                                continue
                            buffer += str(payload.get("data") or "")
                            while "\n" in buffer:
                                line, buffer = buffer.split("\n", 1)
                                if not prompt_sent and line.strip() == input_marker:
                                    await websocket.send(
                                        json.dumps(
                                            {
                                                "type": "input",
                                                "data": f"{encoded_prompt}\n",
                                            }
                                        )
                                    )
                                    prompt_sent = True
                                    continue
                                status = self._completion_status(line, marker)
                                if status is not None:
                                    exit_status = status
                                    break
                                event = self._parse_codex_event(line.strip())
                                if event is not None:
                                    yield event
                            if exit_status is not None:
                                break
                except asyncio.CancelledError:
                    await websocket.send(
                        json.dumps({"type": "input", "data": "\u0003exit\n"})
                    )
                    await websocket.close()
                    raise
        except asyncio.CancelledError:
            raise
        except TimeoutError as error:
            raise SandboxInvocationError("临时会话响应超时，请重试。") from error
        except SandboxError:
            raise
        except Exception as error:
            raise SandboxInvocationError(
                f"连接 AgentKit 沙箱失败：{_safe_error_message(error)}"
            ) from error
        if exit_status != 0:
            raise SandboxInvocationError(
                f"沙箱中的对话进程退出，状态码：{exit_status}。"
            )


class SandboxConversationService:
    """Own temporary conversation lifecycle and per-user isolation."""

    def __init__(
        self,
        gateway: SandboxCloudGateway,
        tool_id: str | None = None,
        openclaw_tool_id: str | None = None,
        openclaw_tool_resolver: Callable[[], str] | None = None,
        hermes_tool_id: str | None = None,
        hermes_tool_resolver: Callable[[], str] | None = None,
    ) -> None:
        self._gateway = gateway
        self._configured_tool_id = (tool_id or "").strip()
        self._configured_openclaw_tool_id = (openclaw_tool_id or "").strip()
        self._openclaw_tool_resolver = openclaw_tool_resolver
        self._openclaw_tool_lock = asyncio.Lock()
        self._configured_hermes_tool_id = (hermes_tool_id or "").strip()
        self._hermes_tool_resolver = hermes_tool_resolver
        self._hermes_tool_lock = asyncio.Lock()
        self._sessions: dict[str, SandboxConversation] = {}
        self._registry_lock = asyncio.Lock()
        self._sessions_starting = 0

    @staticmethod
    def _model_env_overrides() -> dict[str, str]:
        """Return explicitly configured model settings for branded sandboxes."""
        envs = {
            key: (os.getenv(key) or "").strip()
            for key in _SANDBOX_MODEL_ENV_KEYS
        }
        configured = {key: value for key, value in envs.items() if value}
        # Existing OpenClaw images consume MODEL_AGENT_BASE_URL, while newer
        # Hermes/OpenClaw images also accept ARK_BASE_URL. Send both names so
        # the Studio-level ARK_BASE_URL consistently overrides Tool defaults.
        if "ARK_BASE_URL" in configured:
            configured["MODEL_AGENT_BASE_URL"] = configured["ARK_BASE_URL"]
        return configured

    def capabilities(self) -> dict[str, object]:
        """Report whether the dedicated temporary-chat Tool is configured."""
        enabled = bool(self._tool_id(required=False))
        return {"enabled": enabled, "reason": "" if enabled else "管理员未配置"}

    def openclaw_capabilities(self) -> dict[str, object]:
        """Report whether OpenClaw can be resolved after an explicit user action."""
        enabled = bool(
            self._openclaw_tool_id(required=False)
            or self._openclaw_tool_resolver is not None
        )
        return {"enabled": enabled, "reason": "" if enabled else "管理员未配置"}

    def hermes_capabilities(self) -> dict[str, object]:
        """Report whether Hermes can be resolved after an explicit user action."""
        enabled = bool(
            self._hermes_tool_id(required=False)
            or self._hermes_tool_resolver is not None
        )
        return {"enabled": enabled, "reason": "" if enabled else "管理员未配置"}

    def _tool_id(self, *, required: bool = True) -> str:
        tool_id = (
            self._configured_tool_id
            or (os.getenv(_SANDBOX_CHAT_TOOL_ENV) or "").strip()
        )
        if required and not tool_id:
            raise SandboxConfigurationError("管理员未配置")
        return tool_id

    def _openclaw_tool_id(self, *, required: bool = True) -> str:
        tool_id = (
            self._configured_openclaw_tool_id
            or (os.getenv(_SANDBOX_OPENCLAW_TOOL_ENV) or "").strip()
        )
        if required and not tool_id:
            raise SandboxConfigurationError("管理员未配置")
        return tool_id

    def _hermes_tool_id(self, *, required: bool = True) -> str:
        tool_id = (
            self._configured_hermes_tool_id
            or (os.getenv(_SANDBOX_HERMES_TOOL_ENV) or "").strip()
        )
        if required and not tool_id:
            raise SandboxConfigurationError("管理员未配置")
        return tool_id

    async def _resolve_openclaw_tool_id(self) -> str:
        """Resolve/reuse/create the Tool lazily after the user confirms launch."""
        tool_id = self._openclaw_tool_id(required=False)
        if tool_id:
            return tool_id
        if self._openclaw_tool_resolver is None:
            raise SandboxConfigurationError("管理员未配置")

        # A user can double-click launch or multiple users can launch at once.
        # Resolve at most once per Studio process, then every request only creates
        # an AgentKit Session from the cached reusable Tool.
        async with self._openclaw_tool_lock:
            tool_id = self._openclaw_tool_id(required=False)
            if tool_id:
                return tool_id
            try:
                resolved = await asyncio.to_thread(self._openclaw_tool_resolver)
            except SandboxError:
                raise
            except Exception as error:
                raise SandboxProvisioningError(
                    "查找或创建 OpenClaw Tool 失败："
                    f"{_safe_error_message(error)}"
                ) from error
            tool_id = (resolved or "").strip()
            if not tool_id:
                raise SandboxProvisioningError(
                    "查找或创建 OpenClaw Tool 未返回 Tool ID。"
                )
            self._configured_openclaw_tool_id = tool_id
            return tool_id

    async def _resolve_hermes_tool_id(self) -> str:
        """Resolve/reuse/create the Hermes Tool lazily after user confirms launch."""
        tool_id = self._hermes_tool_id(required=False)
        if tool_id:
            return tool_id
        if self._hermes_tool_resolver is None:
            raise SandboxConfigurationError("管理员未配置")

        async with self._hermes_tool_lock:
            tool_id = self._hermes_tool_id(required=False)
            if tool_id:
                return tool_id
            try:
                resolved = await asyncio.to_thread(self._hermes_tool_resolver)
            except SandboxError:
                raise
            except Exception as error:
                raise SandboxProvisioningError(
                    "查找或创建 Hermes Tool 失败："
                    f"{_safe_error_message(error)}"
                ) from error
            tool_id = (resolved or "").strip()
            if not tool_id:
                raise SandboxProvisioningError(
                    "查找或创建 Hermes Tool 未返回 Tool ID。"
                )
            self._configured_hermes_tool_id = tool_id
            return tool_id

    async def start(self, owner_id: str) -> SandboxConversation:
        cloud: SandboxCloudSession | None = None
        tool_id = self._tool_id()
        await self.cleanup_expired()
        async with self._registry_lock:
            if len(self._sessions) + self._sessions_starting >= (
                STUDIO_SANDBOX_MAX_ACTIVE
            ):
                raise SandboxCapacityError("临时会话并发数已达上限，请稍后重试。")
            self._sessions_starting += 1
        try:
            cloud = await self._gateway.create_session(tool_id)
            session = SandboxConversation(
                session_id=str(uuid.uuid4()),
                owner_id=owner_id,
                cloud=cloud,
            )
            self._sessions[session.session_id] = session
            return session
        except asyncio.CancelledError:
            if cloud is not None:
                await asyncio.shield(self._gateway.delete_session(cloud))
            raise
        finally:
            async with self._registry_lock:
                self._sessions_starting -= 1

    async def start_openclaw(
        self, owner_id: str
    ) -> tuple[SandboxConversation, str, str]:
        """Resolve the reusable Tool on demand, then create one Session."""
        dev_preview_url = os.environ.get("SANDBOX_OPENCLAW_DEV_URL", "").strip()

        if dev_preview_url:
            await self.cleanup_expired()
            async with self._registry_lock:
                if len(self._sessions) + self._sessions_starting >= (
                    STUDIO_SANDBOX_MAX_ACTIVE
                ):
                    raise SandboxCapacityError("临时会话并发数已达上限，请稍后重试。")
                self._sessions_starting += 1
            try:
                session = SandboxConversation(
                    session_id=str(uuid.uuid4()),
                    owner_id=owner_id,
                    cloud=None,
                    openclaw_preview_url=dev_preview_url,
                )
                self._sessions[session.session_id] = session
                terminal_url = (
                    os.environ.get("SANDBOX_OPENCLAW_TERMINAL_URL", "").strip()
                    or AgentkitSandboxGateway._sibling_endpoint_url(
                        dev_preview_url, "terminal"
                    )
                )
                return session, dev_preview_url, terminal_url
            finally:
                async with self._registry_lock:
                    self._sessions_starting -= 1

        cloud: SandboxCloudSession | None = None
        tool_id = await self._resolve_openclaw_tool_id()
        await self.cleanup_expired()
        async with self._registry_lock:
            if len(self._sessions) + self._sessions_starting >= (
                STUDIO_SANDBOX_MAX_ACTIVE
            ):
                raise SandboxCapacityError("临时会话并发数已达上限，请稍后重试。")
            self._sessions_starting += 1
        try:
            cloud = await self._gateway.create_session(
                tool_id, self._model_env_overrides()
            )
            session = SandboxConversation(
                session_id=str(uuid.uuid4()),
                owner_id=owner_id,
                cloud=cloud,
            )
            self._sessions[session.session_id] = session
            preview_url = await self._gateway.start_openclaw(session.cloud)
            terminal_url = self._gateway.terminal_preview_url(session.cloud)
            session.openclaw_preview_url = preview_url
        except BaseException:
            if cloud is not None:
                self._sessions.pop(
                    next(
                        (
                            sid
                            for sid, candidate in self._sessions.items()
                            if candidate.cloud is cloud
                        ),
                        "",
                    ),
                    None,
                )
                with contextlib.suppress(SandboxError):
                    await asyncio.shield(self._gateway.delete_session(cloud))
            raise
        finally:
            async with self._registry_lock:
                self._sessions_starting -= 1
        return session, preview_url, terminal_url

    async def start_hermes(
        self, owner_id: str
    ) -> tuple[SandboxConversation, str, str]:
        """Resolve the reusable Hermes Tool on demand, then create one Session."""
        dev_preview_url = os.environ.get("SANDBOX_HERMES_DEV_URL", "").strip()

        if dev_preview_url:
            await self.cleanup_expired()
            async with self._registry_lock:
                if len(self._sessions) + self._sessions_starting >= (
                    STUDIO_SANDBOX_MAX_ACTIVE
                ):
                    raise SandboxCapacityError("临时会话并发数已达上限，请稍后重试。")
                self._sessions_starting += 1
            try:
                session = SandboxConversation(
                    session_id=str(uuid.uuid4()),
                    owner_id=owner_id,
                    cloud=None,
                    hermes_preview_url=dev_preview_url,
                )
                self._sessions[session.session_id] = session
                terminal_url = (
                    os.environ.get("SANDBOX_HERMES_TERMINAL_URL", "").strip()
                    or AgentkitSandboxGateway._sibling_endpoint_url(
                        dev_preview_url, "terminal"
                    )
                )
                return session, dev_preview_url, terminal_url
            finally:
                async with self._registry_lock:
                    self._sessions_starting -= 1

        cloud: SandboxCloudSession | None = None
        tool_id = await self._resolve_hermes_tool_id()
        await self.cleanup_expired()
        async with self._registry_lock:
            if len(self._sessions) + self._sessions_starting >= (
                STUDIO_SANDBOX_MAX_ACTIVE
            ):
                raise SandboxCapacityError("临时会话并发数已达上限，请稍后重试。")
            self._sessions_starting += 1
        try:
            cloud = await self._gateway.create_session(
                tool_id, self._model_env_overrides()
            )
            session = SandboxConversation(
                session_id=str(uuid.uuid4()),
                owner_id=owner_id,
                cloud=cloud,
            )
            self._sessions[session.session_id] = session
            preview_url = await self._gateway.start_hermes(session.cloud)
            terminal_url = self._gateway.terminal_preview_url(session.cloud)
            session.hermes_preview_url = preview_url
        except BaseException:
            if cloud is not None:
                self._sessions.pop(
                    next(
                        (
                            sid
                            for sid, candidate in self._sessions.items()
                            if candidate.cloud is cloud
                        ),
                        "",
                    ),
                    None,
                )
                with contextlib.suppress(SandboxError):
                    await asyncio.shield(self._gateway.delete_session(cloud))
            raise
        finally:
            async with self._registry_lock:
                self._sessions_starting -= 1
        return session, preview_url, terminal_url

    def _owned(self, session_id: str, owner_id: str) -> SandboxConversation:
        session = self._sessions.get(session_id)
        if session is None or session.owner_id != owner_id:
            raise SandboxSessionNotFoundError("临时会话不存在或已过期。")
        return session

    def require_owned(self, session_id: str, owner_id: str) -> None:
        """Fail before an SSE response starts when a session is unavailable."""
        self._owned(session_id, owner_id)

    def require_proxy_session(
        self, session_id: str, proxy_token: str
    ) -> SandboxConversation:
        """Resolve a branded session from its unguessable WebUI proxy token."""
        session = self._sessions.get(session_id)
        if (
            session is None
            or not secrets.compare_digest(session.proxy_token, proxy_token)
            or session.cloud is None
        ):
            raise SandboxSessionNotFoundError("沙箱 WebUI 不存在或已过期。")
        return session

    async def stream_message(
        self, session_id: str, owner_id: str, prompt: str
    ) -> AsyncIterator[SandboxStreamEvent]:
        session = self._owned(session_id, owner_id)
        async with session.lock:
            async for event in self._gateway.stream_codex(
                session.cloud, prompt, session.thread_id
            ):
                if event.thread_id:
                    session.thread_id = event.thread_id
                if event.kind:
                    yield event

    async def close(self, session_id: str, owner_id: str) -> None:
        session = self._owned(session_id, owner_id)
        async with session.lock:
            if session.cloud is not None:
                await self._gateway.delete_session(session.cloud)
            self._sessions.pop(session_id, None)

    async def cleanup_expired(self) -> None:
        """Delete sessions that exceeded their remote TTL."""
        now = time.monotonic()
        expired = [
            (session.session_id, session.owner_id)
            for session in self._sessions.values()
            if session.expires_at <= now
        ]
        for session_id, owner_id in expired:
            try:
                await self.close(session_id, owner_id)
            except SandboxError as error:
                logger.warning(
                    "Failed to clean up expired Sandbox session %s: %s",
                    session_id,
                    _safe_error_message(error),
                )

    async def close_all(self) -> None:
        """Best-effort process-shutdown cleanup for all cloud sessions."""
        sessions = [
            (session.session_id, session.owner_id)
            for session in self._sessions.values()
        ]
        for session_id, owner_id in sessions:
            try:
                await self.close(session_id, owner_id)
            except SandboxError as error:
                logger.warning(
                    "Failed to clean up Sandbox session %s at shutdown: %s",
                    session_id,
                    _safe_error_message(error),
                )
        await self._gateway.drain()


def mount_sandbox_routes(
    app: Any,
    service: SandboxConversationService,
    owner_resolver: Callable[[Any], str],
) -> None:
    """Mount thin Studio HTTP routes for temporary Sandbox conversations."""
    from fastapi import HTTPException
    from fastapi.responses import StreamingResponse

    def _http_error(error: SandboxError) -> HTTPException:
        status_code = 500
        if isinstance(error, SandboxConfigurationError):
            status_code = 503
        elif isinstance(error, SandboxSessionNotFoundError):
            status_code = 404
        elif isinstance(error, SandboxProvisioningError):
            status_code = 502
        elif isinstance(error, SandboxCapacityError):
            status_code = 409
        return HTTPException(
            status_code=status_code,
            detail={
                "code": error.code,
                "message": str(error),
                "retryable": error.retryable,
            },
        )

    def _proxy_prefix(session: SandboxConversation) -> str:
        return (
            f"/web/openclaw/sessions/{session.session_id}"
            f"/proxy/{session.proxy_token}"
        )

    def _proxy_target_url(
        session: SandboxConversation,
        path: str,
        incoming_query: str = "",
    ) -> str:
        assert session.cloud is not None
        target = urlsplit(
            AgentkitSandboxGateway._endpoint_url(session.cloud.endpoint, path)
        )
        query = dict(parse_qsl(incoming_query, keep_blank_values=True))
        query.update(parse_qsl(target.query, keep_blank_values=True))
        return urlunsplit(
            (
                target.scheme,
                target.netloc,
                target.path,
                urlencode(query),
                "",
            )
        )

    def _rewrite_proxy_body(
        body: bytes,
        *,
        content_type: str,
        prefix: str,
    ) -> bytes:
        if not any(
            marker in content_type
            for marker in ("text/", "javascript", "json", "manifest", "xml")
        ):
            return body
        replacement = f"{prefix}/openclaw".encode()
        for marker in (b'"', b"'", b"`"):
            body = body.replace(
                marker + b"/openclaw",
                marker + replacement,
            )
        body = body.replace(b"url(/openclaw", b"url(" + replacement)
        if "text/html" in content_type:
            body = body.replace(
                b"<head>",
                b"<head>" + _OPENCLAW_PROXY_RESET_TAG,
                1,
            )
        return body

    def _rewrite_proxy_location(location: str, prefix: str) -> str:
        if not location:
            return location
        parsed = urlsplit(location)
        path = parsed.path or location
        if not path.startswith("/"):
            return location
        safe_query = urlencode(
            [
                (key, value)
                for key, value in parse_qsl(
                    parsed.query, keep_blank_values=True
                )
                if key.lower() not in {"authorization", "faasinstancename"}
            ]
        )
        return urlunsplit(("", "", f"{prefix}{path}", safe_query, parsed.fragment))

    @app.api_route(
        "/web/openclaw/sessions/{session_id}/proxy/{proxy_token}/{path:path}",
        methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    )
    async def _proxy_openclaw_webui(
        session_id: str,
        proxy_token: str,
        path: str,
        request: Request,
    ) -> Response:
        import httpx

        try:
            session = service.require_proxy_session(session_id, proxy_token)
        except SandboxError as error:
            raise _http_error(error) from error
        prefix = _proxy_prefix(session)
        target_url = _proxy_target_url(session, path, request.url.query)
        target = urlsplit(target_url)
        forwarded_headers = {
            key: value
            for key, value in request.headers.items()
            if key.lower()
            in {
                "accept",
                "accept-language",
                "content-type",
                "if-none-match",
                "if-modified-since",
                "user-agent",
            }
        }
        forwarded_headers["origin"] = f"{target.scheme}://{target.netloc}"
        try:
            async with httpx.AsyncClient(
                follow_redirects=False,
                timeout=httpx.Timeout(60),
            ) as client:
                upstream = await client.request(
                    request.method,
                    target_url,
                    headers=forwarded_headers,
                    content=await request.body(),
                )
        except httpx.HTTPError as error:
            raise _http_error(
                SandboxProvisioningError(
                    f"连接 OpenClaw WebUI 失败：{_safe_error_message(error)}"
                )
            ) from error

        content_type = upstream.headers.get("content-type", "")
        body = _rewrite_proxy_body(
            upstream.content,
            content_type=content_type,
            prefix=prefix,
        )
        blocked_headers = {
            "connection",
            "content-encoding",
            "content-length",
            "content-security-policy",
            "keep-alive",
            "proxy-authenticate",
            "proxy-authorization",
            "trailer",
            "transfer-encoding",
            "upgrade",
            "x-frame-options",
        }
        response_headers = {
            key: value
            for key, value in upstream.headers.items()
            if key.lower() not in blocked_headers
        }
        upstream_csp = upstream.headers.get("content-security-policy", "")
        if upstream_csp:
            proxy_csp = re.sub(
                r"frame-ancestors\s+[^;]+",
                "frame-ancestors 'self'",
                upstream_csp,
                flags=re.IGNORECASE,
            )
            proxy_csp = re.sub(
                r"(script-src\s+[^;]+)",
                rf"\1 {_OPENCLAW_PROXY_RESET_HASH}",
                proxy_csp,
                count=1,
                flags=re.IGNORECASE,
            )
            response_headers["content-security-policy"] = proxy_csp
        if "location" in response_headers:
            response_headers["location"] = _rewrite_proxy_location(
                response_headers["location"], prefix
            )
        return Response(
            content=body,
            status_code=upstream.status_code,
            headers=response_headers,
            media_type=None,
        )

    @app.websocket(
        "/web/openclaw/sessions/{session_id}/proxy/{proxy_token}/{path:path}"
    )
    async def _proxy_openclaw_websocket(
        websocket: WebSocket,
        session_id: str,
        proxy_token: str,
        path: str,
    ) -> None:
        import websockets

        try:
            session = service.require_proxy_session(session_id, proxy_token)
        except SandboxError:
            await websocket.close(code=4404)
            return
        target_http_url = _proxy_target_url(session, path, websocket.url.query)
        parsed = urlsplit(target_http_url)
        target_ws_url = urlunsplit(
            (
                "wss" if parsed.scheme == "https" else "ws",
                parsed.netloc,
                parsed.path,
                parsed.query,
                "",
            )
        )
        requested_protocols = [
            item.strip()
            for item in websocket.headers.get(
                "sec-websocket-protocol", ""
            ).split(",")
            if item.strip()
        ]
        try:
            async with websockets.connect(
                target_ws_url,
                origin=f"{parsed.scheme}://{parsed.netloc}",
                subprotocols=requested_protocols or None,
                max_size=None,
            ) as upstream:
                await websocket.accept(subprotocol=upstream.subprotocol)

                async def _to_upstream() -> None:
                    while True:
                        message = await websocket.receive()
                        if message["type"] == "websocket.disconnect":
                            return
                        payload = message.get("text")
                        if payload is None:
                            payload = message.get("bytes")
                        if payload is not None:
                            await upstream.send(payload)

                async def _to_browser() -> None:
                    async for payload in upstream:
                        if isinstance(payload, bytes):
                            await websocket.send_bytes(payload)
                        else:
                            await websocket.send_text(payload)

                browser_task = asyncio.create_task(_to_upstream())
                upstream_task = asyncio.create_task(_to_browser())
                done, pending = await asyncio.wait(
                    {browser_task, upstream_task},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                for task in pending:
                    task.cancel()
                await asyncio.gather(*done, *pending, return_exceptions=True)
        except WebSocketDisconnect:
            return
        except Exception as error:
            logger.warning(
                "OpenClaw WebUI WebSocket proxy failed: %s",
                _safe_error_message(error),
            )
            with contextlib.suppress(RuntimeError):
                await websocket.close(code=1011)

    @app.get("/web/sandbox/capabilities")
    async def _sandbox_capabilities(request: Request) -> dict[str, object]:
        owner_resolver(request)
        return service.capabilities()

    @app.get("/web/openclaw/capabilities")
    async def _openclaw_capabilities(request: Request) -> dict[str, object]:
        owner_resolver(request)
        return service.openclaw_capabilities()

    @app.get("/web/hermes/capabilities")
    async def _hermes_capabilities(request: Request) -> dict[str, object]:
        owner_resolver(request)
        return service.hermes_capabilities()

    @app.post("/web/sandbox/sessions")
    async def _start_sandbox_session(request: Request) -> dict[str, str]:
        try:
            session = await service.start(owner_resolver(request))
        except SandboxError as error:
            raise _http_error(error) from error
        return {
            "sessionId": session.session_id,
            "status": "ready",
            "toolName": STUDIO_SANDBOX_TOOL_NAME,
        }

    @app.post("/web/openclaw/sessions")
    async def _start_openclaw_session(request: Request) -> dict[str, object]:
        try:
            session, preview_url, terminal_url = await service.start_openclaw(
                owner_resolver(request)
            )
        except SandboxError as error:
            raise _http_error(error) from error
        webui_url = preview_url
        if session.cloud is not None:
            webui_url = f"{_proxy_prefix(session)}/openclaw/"
        return {
            "sessionId": session.session_id,
            "sandboxId": session.cloud.instance_id if session.cloud else f"dev-{session.session_id[:8]}",
            "status": "ready",
            "previewUrl": webui_url,
            "webuiUrl": webui_url,
            "terminalUrl": terminal_url,
            "createdAt": session.created_at,
            "expiresAt": session.created_at + STUDIO_SANDBOX_TTL_SECONDS,
            "ttlSeconds": STUDIO_SANDBOX_TTL_SECONDS,
        }

    @app.post("/web/hermes/sessions")
    async def _start_hermes_session(request: Request) -> dict[str, object]:
        try:
            session, preview_url, terminal_url = await service.start_hermes(
                owner_resolver(request)
            )
        except SandboxError as error:
            raise _http_error(error) from error
        return {
            "sessionId": session.session_id,
            "sandboxId": session.cloud.instance_id if session.cloud else f"dev-{session.session_id[:8]}",
            "status": "ready",
            "previewUrl": preview_url,
            "webuiUrl": preview_url,
            "terminalUrl": terminal_url,
            "createdAt": session.created_at,
            "expiresAt": session.created_at + STUDIO_SANDBOX_TTL_SECONDS,
            "ttlSeconds": STUDIO_SANDBOX_TTL_SECONDS,
        }

    @app.post("/web/sandbox/sessions/{session_id}/messages")
    async def _send_sandbox_message(
        session_id: str, request: Request
    ) -> StreamingResponse:
        data = await request.json()
        prompt = data.get("message") if isinstance(data, dict) else None
        if not isinstance(prompt, str) or not prompt.strip():
            raise HTTPException(status_code=422, detail="message must not be empty")
        if len(prompt) > 100_000:
            raise HTTPException(status_code=413, detail="message is too large")
        owner_id = owner_resolver(request)
        try:
            service.require_owned(session_id, owner_id)
        except SandboxError as error:
            raise _http_error(error) from error

        async def _stream() -> AsyncIterator[str]:
            try:
                async for event in service.stream_message(
                    session_id, owner_id, prompt.strip()
                ):
                    if event.kind == "text":
                        payload = {"text": event.text}
                        yield f"event: delta\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
                        continue
                    payload = {
                        "id": event.item_id,
                        "kind": event.kind,
                        "status": event.status,
                        "text": event.text or None,
                        "name": event.name or None,
                        "args": event.arguments,
                        "response": event.response,
                    }
                    yield f"event: activity\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
                yield "event: done\ndata: {}\n\n"
            except asyncio.CancelledError:
                try:
                    await asyncio.shield(service.close(session_id, owner_id))
                except SandboxError:
                    logger.warning(
                        "Failed to clean up cancelled Sandbox session %s", session_id
                    )
                raise
            except SandboxError as error:
                payload = {
                    "code": error.code,
                    "message": str(error),
                    "retryable": error.retryable,
                }
                yield (
                    f"event: error\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
                )
                yield 'event: done\ndata: {"reason": "failed"}\n\n'

        return StreamingResponse(
            _stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.delete("/web/sandbox/sessions/{session_id}")
    async def _delete_sandbox_session(
        session_id: str, request: Request
    ) -> dict[str, bool]:
        try:
            await service.close(session_id, owner_resolver(request))
        except SandboxError as error:
            raise _http_error(error) from error
        return {"deleted": True}

    @app.delete("/web/openclaw/sessions/{session_id}")
    async def _delete_openclaw_session(
        session_id: str, request: Request
    ) -> dict[str, bool]:
        try:
            await service.close(session_id, owner_resolver(request))
        except SandboxError as error:
            raise _http_error(error) from error
        return {"deleted": True}

    @app.delete("/web/hermes/sessions/{session_id}")
    async def _delete_hermes_session(
        session_id: str, request: Request
    ) -> dict[str, bool]:
        try:
            await service.close(session_id, owner_resolver(request))
        except SandboxError as error:
            raise _http_error(error) from error
        return {"deleted": True}

    cleanup_task: asyncio.Task[None] | None = None

    async def _cleanup_loop() -> None:
        while True:
            await asyncio.sleep(60)
            await service.cleanup_expired()

    async def _start_cleanup() -> None:
        nonlocal cleanup_task
        cleanup_task = asyncio.create_task(_cleanup_loop())

    async def _stop_cleanup() -> None:
        if cleanup_task is not None:
            cleanup_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await cleanup_task
        await service.close_all()

    app.router.on_startup.append(_start_cleanup)
    app.router.on_shutdown.append(_stop_cleanup)
