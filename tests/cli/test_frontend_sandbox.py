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

"""Tests for Studio's temporary AgentKit Sandbox conversations."""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator
from types import SimpleNamespace
from urllib.parse import parse_qsl, urlsplit

import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient

from veadk.cli.frontend_sandbox import (
    AgentkitSandboxGateway,
    SandboxCloudSession,
    SandboxConfigurationError,
    SandboxConversationService,
    SandboxInvocationError,
    SandboxProvisioningError,
    SandboxSessionNotFoundError,
    SandboxStreamEvent,
    mount_sandbox_routes,
)


class _FakeGateway:
    def __init__(self) -> None:
        self.created = 0
        self.tool_ids: list[str] = []
        self.session_envs: list[dict[str, str]] = []
        self.deleted: list[SandboxCloudSession] = []
        self.thread_ids: list[str | None] = []

    async def create_session(
        self,
        tool_id: str,
        envs: dict[str, str] | None = None,
    ) -> SandboxCloudSession:
        self.created += 1
        self.tool_ids.append(tool_id)
        self.session_envs.append(dict(envs or {}))
        return SandboxCloudSession(
            tool_id=tool_id,
            instance_id=f"remote-{self.created}",
            user_session_id=f"user-{self.created}",
            endpoint="https://sandbox.example/path?Authorization=secret",
        )

    async def delete_session(self, session: SandboxCloudSession) -> None:
        self.deleted.append(session)

    async def stream_codex(
        self,
        session: SandboxCloudSession,
        prompt: str,
        thread_id: str | None,
    ) -> AsyncIterator[SandboxStreamEvent]:
        del session
        self.thread_ids.append(thread_id)
        if thread_id is None:
            yield SandboxStreamEvent(thread_id="thread-1")
        yield SandboxStreamEvent(
            kind="thinking",
            item_id="reasoning-1",
            status="done",
            text="分析请求",
        )
        yield SandboxStreamEvent(
            kind="tool",
            item_id="command-1",
            status="done",
            name="运行命令",
            arguments={"command": "pwd"},
            response={"exitCode": 0, "output": "/home/gem"},
        )
        yield SandboxStreamEvent(kind="text", text=f"reply:{prompt}")

    async def drain(self) -> None:
        return None

    async def start_openclaw(self, session: SandboxCloudSession) -> str:
        return "https://sandbox.example/openclaw?ticket=preview"

    async def start_hermes(self, session: SandboxCloudSession) -> str:
        return "https://sandbox.example/hermes?ticket=preview"

    async def start_code(self, session: SandboxCloudSession) -> str:
        return "https://sandbox.example/codex?ticket=preview"

    def terminal_preview_url(self, session: SandboxCloudSession) -> str:
        return "https://sandbox.example/terminal?ticket=preview"


def _app(
    gateway: _FakeGateway,
    tool_id: str | None = "tool-studio",
    openclaw_tool_resolver=None,
    hermes_tool_resolver=None,
    code_tool_resolver=None,
) -> FastAPI:
    app = FastAPI()
    service = SandboxConversationService(
        gateway,
        tool_id=tool_id,
        openclaw_tool_id=tool_id,
        openclaw_tool_resolver=openclaw_tool_resolver,
        hermes_tool_id=tool_id,
        hermes_tool_resolver=hermes_tool_resolver,
        code_tool_id=tool_id,
        code_tool_resolver=code_tool_resolver,
    )

    def _owner(request: Request) -> str:
        owner = request.headers.get("X-Test-User", "")
        if not owner:
            raise HTTPException(status_code=401, detail="identity required")
        return owner

    mount_sandbox_routes(app, service, _owner)
    return app


def test_sandbox_routes_start_stream_and_delete_without_exposing_endpoint() -> None:
    gateway = _FakeGateway()
    with TestClient(_app(gateway)) as client:
        create = client.post("/web/sandbox/sessions", headers={"X-Test-User": "alice"})

        assert create.status_code == 200
        assert create.json()["status"] == "ready"
        assert "endpoint" not in create.json()
        assert "secret" not in create.text
        session_id = create.json()["sessionId"]

        first = client.post(
            f"/web/sandbox/sessions/{session_id}/messages",
            headers={"X-Test-User": "alice"},
            json={"message": "hello"},
        )
        second = client.post(
            f"/web/sandbox/sessions/{session_id}/messages",
            headers={"X-Test-User": "alice"},
            json={"message": "again"},
        )
        deleted = client.delete(
            f"/web/sandbox/sessions/{session_id}",
            headers={"X-Test-User": "alice"},
        )

    assert first.status_code == 200
    assert "event: activity" in first.text
    assert '"kind": "thinking"' in first.text
    assert '"kind": "tool"' in first.text
    assert "event: delta" in first.text
    assert 'data: {"text": "reply:hello"}' in first.text
    assert "event: done" in first.text
    assert second.status_code == 200
    assert gateway.thread_ids == [None, "thread-1"]
    assert deleted.json() == {"deleted": True}
    assert [item.instance_id for item in gateway.deleted] == ["remote-1"]
    assert gateway.tool_ids == ["tool-studio"]
    assert gateway.session_envs == [{}]


def test_openclaw_endpoint_uses_the_image_proxy_path() -> None:
    endpoint = (
        "https://sandbox.example/?faasInstanceName=instance&Authorization=secret"
    )

    assert AgentkitSandboxGateway._endpoint_url(endpoint, "openclaw") == (
        "https://sandbox.example/openclaw"
        "?faasInstanceName=instance&Authorization=secret"
    )
    assert AgentkitSandboxGateway._endpoint_url(endpoint, "terminal") == (
        "https://sandbox.example/terminal"
        "?faasInstanceName=instance&Authorization=secret"
    )
    assert AgentkitSandboxGateway._endpoint_url(endpoint, "codex") == (
        "https://sandbox.example/codex"
        "?faasInstanceName=instance&Authorization=secret"
    )
    assert AgentkitSandboxGateway._sibling_endpoint_url(
        "https://sandbox.example/hermes?Authorization=secret",
        "terminal",
    ) == "https://sandbox.example/terminal?Authorization=secret"


def test_sandbox_capabilities_report_configured_tool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SANDBOX_CHAT_CODEX", "configured-tool")
    with TestClient(_app(_FakeGateway(), tool_id=None)) as client:
        response = client.get(
            "/web/sandbox/capabilities", headers={"X-Test-User": "alice"}
        )

    assert response.status_code == 200
    assert response.json() == {"enabled": True, "reason": ""}


def test_openclaw_routes_create_report_lifecycle_and_delete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import httpx

    proxied_urls: list[str] = []

    async def _proxy_request(
        self: object,
        method: str,
        url: str,
        **kwargs: object,
    ) -> SimpleNamespace:
        del self, method, kwargs
        proxied_urls.append(url)
        return SimpleNamespace(
            status_code=200,
            content=b'<script src="/openclaw/assets/app.js"></script>',
            headers={
                "content-type": "text/html; charset=utf-8",
                "content-security-policy": "frame-ancestors 'none'",
                "x-frame-options": "DENY",
            },
        )

    monkeypatch.setattr(httpx.AsyncClient, "request", _proxy_request)
    gateway = _FakeGateway()
    app = _app(gateway)
    with TestClient(app) as client:
        created = client.post(
            "/web/openclaw/sessions", headers={"X-Test-User": "alice"}
        )
        assert created.status_code == 200
        payload = created.json()
        assert payload["status"] == "ready"
        assert payload["sandboxId"] == "remote-1"
        assert payload["previewUrl"] == payload["webuiUrl"]
        assert payload["webuiUrl"].startswith(
            f"/web/openclaw/sessions/{payload['sessionId']}/proxy/"
        )
        assert payload["webuiUrl"].endswith("/openclaw/")
        assert payload["terminalUrl"].endswith("/terminal?ticket=preview")
        assert payload["expiresAt"] - payload["createdAt"] == 3600
        assert payload["ttlSeconds"] == 3600

        proxied = client.get(payload["webuiUrl"])
        assert proxied.status_code == 200
        assert "x-frame-options" not in proxied.headers
        assert "frame-ancestors 'self'" in proxied.headers["content-security-policy"]
        assert "frame-ancestors 'none'" not in proxied.headers["content-security-policy"]
        proxy_prefix = payload["webuiUrl"].removesuffix("/openclaw/")
        assert f'{proxy_prefix}/openclaw/assets/app.js' in proxied.text
        assert "Authorization=secret" in proxied_urls[0]

        invalid_proxy = client.get(
            payload["webuiUrl"].replace("/proxy/", "/proxy/invalid-", 1)
        )
        assert invalid_proxy.status_code == 404

        deleted = client.delete(
            f"/web/openclaw/sessions/{payload['sessionId']}",
            headers={"X-Test-User": "alice"},
        )
        assert deleted.status_code == 200
        assert deleted.json() == {"deleted": True}


def test_hermes_routes_create_report_lifecycle_and_delete() -> None:
    gateway = _FakeGateway()
    app = _app(gateway)
    with TestClient(app) as client:
        created = client.post(
            "/web/hermes/sessions", headers={"X-Test-User": "alice"}
        )
        assert created.status_code == 200
        payload = created.json()
        assert payload["status"] == "ready"
        assert payload["sandboxId"] == "remote-1"
        assert payload["previewUrl"].endswith("?ticket=preview")
        assert payload["webuiUrl"].endswith("/hermes?ticket=preview")
        assert payload["terminalUrl"].endswith("/terminal?ticket=preview")
        assert payload["expiresAt"] - payload["createdAt"] == 3600
        assert payload["ttlSeconds"] == 3600

        deleted = client.delete(
            f"/web/hermes/sessions/{payload['sessionId']}",
            headers={"X-Test-User": "alice"},
        )
        assert deleted.status_code == 200
        assert deleted.json() == {"deleted": True}


def test_code_routes_proxy_codex_cookie_and_sse(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import httpx

    proxied_urls: list[str] = []
    event_cookies: list[str] = []
    native_forwarded_headers: list[dict[str, str]] = []

    class _Upstream:
        def __init__(
            self,
            *,
            content: bytes,
            content_type: str,
            headers: dict[str, str] | None = None,
        ) -> None:
            self.status_code = 200
            self._content = content
            self.headers = {"content-type": content_type, **(headers or {})}

        async def aread(self) -> bytes:
            return self._content

        async def aiter_bytes(self):
            yield self._content

        async def aclose(self) -> None:
            return None

    async def _send(
        self: object,
        request: object,
        *,
        stream: bool = False,
    ) -> _Upstream:
        del self, stream
        url = str(request.url)
        proxied_urls.append(url)
        request_path = urlsplit(url).path
        if urlsplit(url).path.endswith("/events"):
            event_cookies.append(request.headers.get("cookie", ""))
            return _Upstream(
                content=b"event: message\ndata: {}\n\n",
                content_type="text/event-stream",
            )
        if urlsplit(url).path.endswith("/capability"):
            return _Upstream(
                content=b'{"ok":true}',
                content_type="application/json",
                headers={
                    "set-cookie": (
                        "agentkit_codex_web_capability=token; "
                        "Path=/codex/api; HttpOnly; SameSite=Strict"
                    )
                },
            )
        if request_path.endswith("/terminal"):
            native_forwarded_headers.append(dict(request.headers))
            return _Upstream(
                content=(
                    b"<html><head></head><body>"
                    b"<script>new URL('v1/shell/ws', window.location.href)</script>"
                    b"</body></html>"
                ),
                content_type="text/html; charset=utf-8",
                headers={
                    "x-frame-options": "SAMEORIGIN",
                    "content-security-policy": "frame-ancestors 'none'",
                },
            )
        if request_path.endswith("/browser-ui"):
            native_forwarded_headers.append(dict(request.headers))
            return _Upstream(
                content=b"<html><body>Browser UI</body></html>",
                content_type="text/html; charset=utf-8",
                headers={"x-frame-options": "DENY"},
            )
        if request_path.endswith("/v1/browser/info"):
            native_forwarded_headers.append(dict(request.headers))
            return _Upstream(
                content=(
                    b'{"data":{"cdp_url":"wss://testserver/web/code/native/cdp/'
                    b'devtools/page/1?view=main&Authorization=must-not-leak'
                    b'&faasInstanceName=instance"}}'
                ),
                content_type="application/json",
            )
        if "/static/" in request_path:
            native_forwarded_headers.append(dict(request.headers))
            return _Upstream(
                content=b"body{background:#111}",
                content_type="text/css",
            )
        return _Upstream(
            content=(
                b'<script src="/codex/assets/app.js"></script>'
                b'<script>const terminal="/terminal";'
                b'const browser="/browser-ui";</script>'
            ),
            content_type="text/html; charset=utf-8",
            headers={"x-frame-options": "DENY"},
        )

    monkeypatch.setattr(httpx.AsyncClient, "send", _send)
    gateway = _FakeGateway()
    with TestClient(_app(gateway)) as client:
        created = client.post(
            "/web/code/sessions", headers={"X-Test-User": "alice"}
        )
        assert created.status_code == 200
        payload = created.json()
        assert payload["status"] == "ready"
        assert payload["sandboxId"] == "remote-1"
        assert payload["webuiUrl"].startswith(
            f"/web/code/sessions/{payload['sessionId']}/proxy/"
        )
        assert payload["webuiUrl"].endswith("/codex/")
        assert payload["terminalUrl"].startswith(
            f"/web/code/sessions/{payload['sessionId']}/proxy/"
        )
        assert payload["terminalUrl"].endswith("/native/terminal")
        assert payload["expiresAt"] - payload["createdAt"] == 3600

        proxied = client.get(payload["webuiUrl"])
        assert proxied.status_code == 200
        assert "x-frame-options" not in proxied.headers
        proxy_prefix = payload["webuiUrl"].removesuffix("/codex/")
        assert f'{proxy_prefix}/codex/assets/app.js' in proxied.text
        assert f'const terminal="{proxy_prefix}/native/terminal"' in proxied.text
        assert f'const browser="{proxy_prefix}/native/browser-ui"' in proxied.text

        terminal = client.get(f"{proxy_prefix}/native/terminal")
        assert terminal.status_code == 200
        assert "x-frame-options" not in terminal.headers
        assert (
            terminal.headers["content-security-policy"]
            == "frame-ancestors 'self'"
        )
        assert "new URL('v1/shell/ws'" in terminal.text

        terminal_asset = client.get(
            f"{proxy_prefix}/native/static/sandbox/xterm.css"
        )
        assert terminal_asset.status_code == 200
        assert terminal_asset.text == "body{background:#111}"

        browser = client.get(f"{proxy_prefix}/native/browser-ui")
        assert browser.status_code == 200
        assert "x-frame-options" not in browser.headers

        browser_info = client.get(
            f"{proxy_prefix}/native/v1/browser/info?view=main"
        )
        assert browser_info.status_code == 200
        cdp_url = browser_info.json()["data"]["cdp_url"]
        assert "view=main" in cdp_url
        assert "Authorization" not in cdp_url
        assert "faasInstanceName" not in cdp_url

        assert native_forwarded_headers
        assert all(
            headers["x-forwarded-prefix"] == f"{proxy_prefix}/native"
            for headers in native_forwarded_headers
        )
        assert all(
            headers["x-forwarded-host"] == "testserver"
            for headers in native_forwarded_headers
        )
        assert all("cookie" not in headers for headers in native_forwarded_headers)

        capability = client.get(f"{proxy_prefix}/codex/api/capability")
        assert capability.status_code == 200
        assert f"Path={proxy_prefix}/codex/api" in capability.headers["set-cookie"]

        events = client.get(f"{proxy_prefix}/codex/api/sessions/bridge/events")
        assert events.status_code == 200
        assert events.text == "event: message\ndata: {}\n\n"

        deleted = client.delete(
            f"/web/code/sessions/{payload['sessionId']}",
            headers={"X-Test-User": "alice"},
        )
        assert deleted.json() == {"deleted": True}

    assert all("Authorization=secret" in url for url in proxied_urls)
    assert event_cookies == ["agentkit_codex_web_capability=token"]


def test_code_native_websocket_stays_in_the_session_proxy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import websockets

    connected: dict[str, object] = {}

    class _Upstream:
        subprotocol = None

        async def __aenter__(self) -> "_Upstream":
            return self

        async def __aexit__(self, *_: object) -> None:
            return None

        async def send(self, payload: object) -> None:
            connected["payload"] = payload

        def __aiter__(self) -> "_Upstream":
            return self

        async def __anext__(self) -> str:
            if connected.get("yielded"):
                raise StopAsyncIteration
            connected["yielded"] = True
            return "native-ready"

    def _connect(url: str, **kwargs: object) -> _Upstream:
        connected["url"] = url
        connected["kwargs"] = kwargs
        return _Upstream()

    monkeypatch.setattr(websockets, "connect", _connect)
    with TestClient(_app(_FakeGateway())) as client:
        created = client.post(
            "/web/code/sessions", headers={"X-Test-User": "alice"}
        )
        payload = created.json()
        proxy_prefix = payload["webuiUrl"].removesuffix("/codex/")
        with client.websocket_connect(
            f"{proxy_prefix}/native/v1/shell/ws?session_id=shell-1",
            headers={"cookie": "native-session=token"},
        ) as websocket:
            assert websocket.receive_text() == "native-ready"

    target = urlsplit(str(connected["url"]))
    assert target.path.endswith("/v1/shell/ws")
    assert dict(parse_qsl(target.query)) == {
        "session_id": "shell-1",
        "Authorization": "secret",
    }
    kwargs = connected["kwargs"]
    assert isinstance(kwargs, dict)
    assert "additional_headers" not in kwargs


def test_branded_sessions_override_tool_model_envs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ARK_BASE_URL", "https://ark.example/api/v3")
    monkeypatch.setenv("MODEL_AGENT_API_KEY", "configured-model-secret")
    monkeypatch.setenv("MODEL_AGENT_NAME", "configured-model")
    monkeypatch.setenv("CODEX_API_KEY", "configured-codex-secret")
    monkeypatch.setenv("CODEX_BASE_URL", "https://codex.example/api/v3")
    monkeypatch.setenv("CODEX_MODEL", "configured-codex-model")
    gateway = _FakeGateway()

    with TestClient(_app(gateway)) as client:
        openclaw = client.post(
            "/web/openclaw/sessions", headers={"X-Test-User": "alice"}
        )
        hermes = client.post(
            "/web/hermes/sessions", headers={"X-Test-User": "alice"}
        )
        code = client.post(
            "/web/code/sessions", headers={"X-Test-User": "alice"}
        )

    assert openclaw.status_code == 200
    assert hermes.status_code == 200
    assert code.status_code == 200
    expected = {
        "ARK_BASE_URL": "https://ark.example/api/v3",
        "MODEL_AGENT_BASE_URL": "https://ark.example/api/v3",
        "MODEL_AGENT_API_KEY": "configured-model-secret",
        "MODEL_AGENT_NAME": "configured-model",
    }
    assert gateway.session_envs == [
        expected,
        expected,
        {
            "CODEX_API_KEY": "configured-codex-secret",
            "CODEX_BASE_URL": "https://codex.example/api/v3",
            "CODEX_MODEL": "configured-codex-model",
        },
    ]


def test_openclaw_tool_is_resolved_only_after_user_starts_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SANDBOX_OPENCLAW_TOOL", raising=False)
    resolved = 0

    def _resolve() -> str:
        nonlocal resolved
        resolved += 1
        return "tool-resolved-on-click"

    gateway = _FakeGateway()
    with TestClient(
        _app(gateway, tool_id=None, openclaw_tool_resolver=_resolve)
    ) as client:
        capability = client.get(
            "/web/openclaw/capabilities",
            headers={"X-Test-User": "alice"},
        )
        assert capability.json() == {"enabled": True, "reason": ""}
        assert resolved == 0
        assert gateway.created == 0

        first = client.post(
            "/web/openclaw/sessions",
            headers={"X-Test-User": "alice"},
        )
        second = client.post(
            "/web/openclaw/sessions",
            headers={"X-Test-User": "bob"},
        )

    assert first.status_code == 200
    assert second.status_code == 200
    assert resolved == 1
    assert gateway.tool_ids == [
        "tool-resolved-on-click",
        "tool-resolved-on-click",
    ]


def test_code_tool_is_resolved_only_after_user_starts_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SANDBOX_CODE_TOOL", raising=False)
    resolved = 0

    def _resolve() -> str:
        nonlocal resolved
        resolved += 1
        return "tool-code-on-click"

    gateway = _FakeGateway()
    with TestClient(
        _app(gateway, tool_id=None, code_tool_resolver=_resolve)
    ) as client:
        capability = client.get(
            "/web/code/capabilities",
            headers={"X-Test-User": "alice"},
        )
        assert capability.json() == {"enabled": True, "reason": ""}
        assert resolved == 0

        first = client.post(
            "/web/code/sessions",
            headers={"X-Test-User": "alice"},
        )
        second = client.post(
            "/web/code/sessions",
            headers={"X-Test-User": "bob"},
        )

    assert first.status_code == 200
    assert second.status_code == 200
    assert resolved == 1
    assert gateway.tool_ids == ["tool-code-on-click", "tool-code-on-click"]


def test_sandbox_capabilities_report_admin_not_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SANDBOX_CHAT_CODEX", raising=False)
    with TestClient(_app(_FakeGateway(), tool_id=None)) as client:
        response = client.get(
            "/web/sandbox/capabilities", headers={"X-Test-User": "alice"}
        )

    assert response.status_code == 200
    assert response.json() == {"enabled": False, "reason": "管理员未配置"}


@pytest.mark.asyncio
async def test_sandbox_start_requires_preconfigured_chat_tool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SANDBOX_CHAT_CODEX", raising=False)
    gateway = _FakeGateway()
    service = SandboxConversationService(gateway)

    with pytest.raises(SandboxConfigurationError, match="管理员未配置"):
        await service.start("alice")

    assert gateway.created == 0


def test_codex_parser_preserves_reasoning_and_tool_lifecycle() -> None:
    reasoning = AgentkitSandboxGateway._parse_codex_event(
        json.dumps(
            {
                "type": "item.completed",
                "item": {
                    "id": "reasoning-1",
                    "type": "reasoning",
                    "text": "检查工作区",
                },
            }
        )
    )
    command_started = AgentkitSandboxGateway._parse_codex_event(
        json.dumps(
            {
                "type": "item.started",
                "item": {
                    "id": "command-1",
                    "type": "command_execution",
                    "command": "pwd",
                },
            }
        )
    )
    command_completed = AgentkitSandboxGateway._parse_codex_event(
        json.dumps(
            {
                "type": "item.completed",
                "item": {
                    "id": "command-1",
                    "type": "command_execution",
                    "command": "pwd",
                    "status": "completed",
                    "exit_code": 0,
                    "aggregated_output": "/home/gem",
                },
            }
        )
    )

    assert reasoning == SandboxStreamEvent(
        kind="thinking",
        item_id="reasoning-1",
        status="done",
        text="检查工作区",
    )
    assert command_started == SandboxStreamEvent(
        kind="tool",
        item_id="command-1",
        status="running",
        name="运行命令",
        arguments={"command": "pwd"},
    )
    assert command_completed == SandboxStreamEvent(
        kind="tool",
        item_id="command-1",
        status="done",
        name="运行命令",
        arguments={"command": "pwd"},
        response={"status": "completed", "exitCode": 0, "output": "/home/gem"},
    )


def test_sandbox_route_hides_sessions_owned_by_another_user() -> None:
    gateway = _FakeGateway()
    with TestClient(_app(gateway)) as client:
        created = client.post("/web/sandbox/sessions", headers={"X-Test-User": "alice"})
        session_id = created.json()["sessionId"]
        response = client.delete(
            f"/web/sandbox/sessions/{session_id}",
            headers={"X-Test-User": "bob"},
        )

    assert response.status_code == 404
    assert [item.instance_id for item in gateway.deleted] == ["remote-1"]


def test_sandbox_route_rejects_empty_message() -> None:
    gateway = _FakeGateway()
    with TestClient(_app(gateway)) as client:
        created = client.post("/web/sandbox/sessions", headers={"X-Test-User": "alice"})
        response = client.post(
            f"/web/sandbox/sessions/{created.json()['sessionId']}/messages",
            headers={"X-Test-User": "alice"},
            json={"message": "  "},
        )

    assert response.status_code == 422


def test_sandbox_route_requires_an_identity() -> None:
    with TestClient(_app(_FakeGateway())) as client:
        response = client.post("/web/sandbox/sessions")

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_service_owner_check_does_not_reveal_session() -> None:
    service = SandboxConversationService(_FakeGateway(), tool_id="tool-studio")
    session = await service.start("alice")

    with pytest.raises(SandboxSessionNotFoundError):
        await service.close(session.session_id, "bob")


@pytest.mark.asyncio
async def test_service_allows_multiple_sessions_for_the_same_owner() -> None:
    gateway = _FakeGateway()
    service = SandboxConversationService(gateway, tool_id="tool-studio")

    first, second = await asyncio.gather(
        service.start("alice"),
        service.start("alice"),
    )

    assert first.session_id != second.session_id
    assert gateway.created == 2


def test_terminal_completion_ignores_echoed_command_and_prompt_is_not_in_command() -> (
    None
):
    marker = "__VEADK_DONE_test__"
    command = AgentkitSandboxGateway._command(None, "__VEADK_INPUT_test__", marker)

    assert "private prompt" not in command
    assert AgentkitSandboxGateway._completion_status(command, marker) is None
    assert AgentkitSandboxGateway._completion_status(f"{marker}0\r", marker) == 0


@pytest.mark.asyncio
async def test_gateway_accepts_a_lazy_client_factory() -> None:
    class _Client:
        def list_tools(self, request: object) -> str:
            del request
            return "ok"

    calls = 0

    def _factory() -> _Client:
        nonlocal calls
        calls += 1
        return _Client()

    gateway = AgentkitSandboxGateway(_factory)

    assert await gateway._call("list_tools", object()) == "ok"
    assert calls == 1


@pytest.mark.asyncio
async def test_gateway_forwards_session_environment_overrides() -> None:
    requests: list[object] = []

    class _Client:
        def create_session(self, request: object) -> SimpleNamespace:
            requests.append(request)
            return SimpleNamespace(
                session_id="remote-1",
                user_session_id="user-1",
                endpoint="https://sandbox.example?Authorization=secret",
            )

    gateway = AgentkitSandboxGateway(_Client())
    await gateway.create_session(
        "tool-1",
        {
            "ARK_BASE_URL": "https://ark.example/api/v3",
            "MODEL_AGENT_API_KEY": "configured-model-secret",
        },
    )

    assert len(requests) == 1
    assert {
        item.key: item.value for item in getattr(requests[0], "envs")
    } == {
        "ARK_BASE_URL": "https://ark.example/api/v3",
        "MODEL_AGENT_API_KEY": "configured-model-secret",
    }


@pytest.mark.asyncio
async def test_gateway_accepts_an_already_expired_session_as_deleted() -> None:
    class _Client:
        def delete_session(self, request: object) -> None:
            del request
            raise RuntimeError("InvalidResource.NotFound")

    gateway = AgentkitSandboxGateway(_Client())
    await gateway.delete_session(
        SandboxCloudSession(
            tool_id="tool-1",
            instance_id="expired-session",
            user_session_id="user-1",
            endpoint="https://sandbox.example",
        )
    )


@pytest.mark.asyncio
async def test_delete_failure_keeps_session_for_cleanup_retry() -> None:
    class _FailDeleteGateway(_FakeGateway):
        async def delete_session(self, session: SandboxCloudSession) -> None:
            del session
            raise SandboxProvisioningError("delete failed")

    service = SandboxConversationService(_FailDeleteGateway(), tool_id="tool-studio")
    session = await service.start("alice")

    with pytest.raises(SandboxProvisioningError):
        await service.close(session.session_id, "alice")

    service.require_owned(session.session_id, "alice")


@pytest.mark.asyncio
async def test_expiry_and_close_all_delete_cloud_sessions() -> None:
    gateway = _FakeGateway()
    service = SandboxConversationService(gateway, tool_id="tool-studio")
    expired = await service.start("alice")
    expired.expires_at = time.monotonic() - 1

    await service.cleanup_expired()
    active = await service.start("bob")
    await service.close_all()

    assert [item.instance_id for item in gateway.deleted] == [
        expired.cloud.instance_id,
        active.cloud.instance_id,
    ]


def test_sse_error_has_an_explicit_done_frame() -> None:
    class _FailStreamGateway(_FakeGateway):
        async def stream_codex(
            self,
            session: SandboxCloudSession,
            prompt: str,
            thread_id: str | None,
        ) -> AsyncIterator[SandboxStreamEvent]:
            del session, prompt, thread_id
            raise SandboxInvocationError("failed")
            yield SandboxStreamEvent()

    with TestClient(_app(_FailStreamGateway())) as client:
        created = client.post("/web/sandbox/sessions", headers={"X-Test-User": "alice"})
        response = client.post(
            f"/web/sandbox/sessions/{created.json()['sessionId']}/messages",
            headers={"X-Test-User": "alice"},
            json={"message": "hello"},
        )

    assert "event: error" in response.text
    assert 'event: done\ndata: {"reason": "failed"}' in response.text


@pytest.mark.asyncio
async def test_cancelled_create_is_deleted_after_sdk_call_finishes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    deleted: list[str] = []
    created: list[object] = []

    class _Client:
        def create_session(self, request: object) -> SimpleNamespace:
            created.append(request)
            time.sleep(0.05)
            return SimpleNamespace(
                session_id="remote-1",
                user_session_id="user-1",
                endpoint="https://sandbox.example?Authorization=secret",
            )

        def delete_session(self, request: object) -> None:
            deleted.append(str(getattr(request, "session_id")))

    gateway = AgentkitSandboxGateway(_Client())
    task = asyncio.create_task(gateway.create_session("tool-1"))
    await asyncio.sleep(0)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
    await gateway.drain()

    assert deleted == ["remote-1"]
    assert len(created) == 1
    assert getattr(created[0], "tool_id") == "tool-1"
    assert getattr(created[0], "envs") is None
