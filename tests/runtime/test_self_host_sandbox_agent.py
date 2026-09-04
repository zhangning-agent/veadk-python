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

import asyncio
import importlib
import importlib.util
import sys
import os
from pathlib import Path
from types import SimpleNamespace


EXAMPLE_DIR = Path(__file__).parents[2] / "examples" / "16_self_host_sandbox"
if str(EXAMPLE_DIR) not in sys.path:
    sys.path.insert(0, str(EXAMPLE_DIR))

os.environ.setdefault("ANTHROPIC_BASE_URL", "https://sandbox.example.com")
os.environ.setdefault("ANTHROPIC_ENVIRONMENT_KEY", "test-token")
os.environ.setdefault("MODEL_AGENT_API_KEY", "test-model-api-key")

agent_module = importlib.import_module("agents.self_host_sandbox_agent.agent")
SandboxSessionManager = agent_module.SandboxSessionManager

MAIN_SPEC = importlib.util.spec_from_file_location(
    "self_host_sandbox_main", EXAMPLE_DIR / "main.py"
)
assert MAIN_SPEC and MAIN_SPEC.loader
main_module = importlib.util.module_from_spec(MAIN_SPEC)
MAIN_SPEC.loader.exec_module(main_module)


class _FakeClient:
    def __init__(self, remote_session_id: str):
        self.session_id = None
        self.remote_session_id = remote_session_id
        self.created_titles = []
        self.idle_count = 0
        self.wakeup_count = 0

    def create_session(self, title: str):
        self.created_titles.append(title)
        self.session_id = self.remote_session_id

    def post_status_idle(self):
        self.idle_count += 1

    def post_turn_wakeup(self):
        self.wakeup_count += 1


def test_each_veadk_session_creates_a_distinct_remote_session(monkeypatch):
    manager = SandboxSessionManager()
    clients = iter((_FakeClient("remote-1"), _FakeClient("remote-2")))
    monkeypatch.setattr(manager, "_new_client", lambda: next(clients))

    assert manager.create_remote_session("veadk-1") == "remote-1"
    assert manager.create_remote_session("veadk-1") == "remote-1"
    assert manager.create_remote_session("veadk-2") == "remote-2"

    assert manager.get("veadk-1") is not manager.get("veadk-2")
    assert manager.get("veadk-1").created_titles == [
        "VeADK Self-Hosted Sandbox Session veadk-1"
    ]
    assert manager.get("veadk-2").created_titles == [
        "VeADK Self-Hosted Sandbox Session veadk-2"
    ]


def test_remote_session_wakes_once_per_later_turn_and_idles_once(monkeypatch):
    manager = SandboxSessionManager()
    client = _FakeClient("remote-1")
    monkeypatch.setattr(manager, "_new_client", lambda: client)
    manager.create_remote_session("veadk-1")

    manager.begin_turn("veadk-1")
    manager.begin_turn("veadk-1")
    assert client.wakeup_count == 0

    manager.end_turn("veadk-1")
    assert client.idle_count == 0
    manager.end_turn("veadk-1")
    assert client.idle_count == 1

    manager.begin_turn("veadk-1")
    manager.begin_turn("veadk-1")
    assert client.wakeup_count == 1
    manager.end_turn("veadk-1")
    manager.end_turn("veadk-1")
    assert client.idle_count == 2


def test_runner_wrapper_ends_remote_turn_after_failure(monkeypatch):
    lifecycle_calls = []

    class _FailingRunner:
        async def run_async(self, **kwargs):
            yield "started"
            raise RuntimeError("turn failed")

    monkeypatch.setattr(
        agent_module.sandbox_sessions,
        "begin_turn",
        lambda session_id: lifecycle_calls.append(("begin", session_id)),
    )
    monkeypatch.setattr(
        agent_module.sandbox_sessions,
        "end_turn",
        lambda session_id: lifecycle_calls.append(("end", session_id)),
    )
    runner = agent_module.enable_sandbox_turn_lifecycle(_FailingRunner())

    async def consume():
        async for _ in runner.run_async(session_id="veadk-1"):
            pass

    try:
        asyncio.run(consume())
    except RuntimeError as error:
        assert str(error) == "turn failed"
    else:
        raise AssertionError("the wrapped runner must preserve turn failures")

    assert lifecycle_calls == [("begin", "veadk-1"), ("end", "veadk-1")]


def test_web_session_service_creation_provisions_remote_session(monkeypatch):
    created = []
    monkeypatch.setattr(
        agent_module.sandbox_sessions,
        "create_remote_session",
        lambda session_id: created.append(session_id) or "remote-web",
    )

    asyncio.run(
        agent_module.short_term_memory.session_service.create_session(
            app_name="self_host_sandbox_agent",
            user_id="user",
            session_id="web-session",
        )
    )

    assert created == ["web-session"]


def test_dispatch_task_sends_only_the_model_tool_call(monkeypatch):
    dispatched = []
    client = SimpleNamespace(
        dispatch_tool=lambda name, arguments, *, dispatch_id: dispatched.append(
            (name, arguments, dispatch_id)
        )
        or {"stdout": "ok"}
    )
    monkeypatch.setattr(agent_module.sandbox_sessions, "get", lambda session_id: client)
    tool_call = SimpleNamespace(
        session_id="veadk-session",
        id="tool-call-1",
        name="bash",
        arguments={"command": "printf ok"},
    )

    result = asyncio.run(agent_module.dispatch_task(tool_call))

    assert result == {"stdout": "ok"}
    assert dispatched == [("bash", {"command": "printf ok"}, "tool-call-1")]
    assert not hasattr(agent_module.agent, "run_turn")


def test_feishu_channel_stays_up_until_stopped_and_shuts_down(monkeypatch):
    calls = []

    class _FakeRunner:
        def __init__(self, **kwargs):
            calls.append(("runner", kwargs))

        async def run_async(self, **kwargs):
            if False:
                yield None

    class _FakeChannel:
        def __init__(self, *, runner, **kwargs):
            calls.append(("channel", runner, kwargs))

        def start(self, loop):
            calls.append(("start", loop))

        async def shutdown(self):
            calls.append(("shutdown", None))

    monkeypatch.setattr(main_module, "Runner", _FakeRunner)
    monkeypatch.setattr(main_module, "FeishuChannelExtension", _FakeChannel)
    stop_event = asyncio.Event()
    stop_event.set()

    asyncio.run(main_module.serve_feishu_channel(stop_event))

    assert calls[0] == (
        "runner",
        {"agent": main_module.agent, "app_name": "self_host_sandbox_demo"},
    )
    assert calls[1][0] == "channel"
    assert isinstance(calls[1][1], _FakeRunner)
    assert calls[1][2] == {
        "streaming": True,
        "show_thinking": True,
        "show_tool_calls": True,
        "show_tool_results": True,
        "separate_tool_call_cards": True,
        "separate_thinking_card": True,
        "create_topic": True,
    }
    assert calls[2][0] == "start"
    assert calls[3] == ("shutdown", None)
