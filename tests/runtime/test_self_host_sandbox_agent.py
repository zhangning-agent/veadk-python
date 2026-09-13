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
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

EXAMPLE_DIR = Path(__file__).parents[2] / "examples" / "16_self_host_sandbox"
if str(EXAMPLE_DIR) not in sys.path:
    sys.path.insert(0, str(EXAMPLE_DIR))

os.environ.setdefault("ANTHROPIC_BASE_URL", "https://sandbox.example.com")
os.environ.setdefault("ANTHROPIC_ENVIRONMENT_KEY", "test-token")
os.environ.setdefault("MODEL_AGENT_API_KEY", "test-model-api-key")
os.environ.setdefault("MODEL_AGENT_NAME", "test-model")

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

    def create_session(self, title: str):
        self.created_titles.append(title)
        self.session_id = self.remote_session_id

    def post_status_idle(self):
        self.idle_count += 1


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


def test_remote_session_writes_no_synthetic_event_and_idles_once_per_turn(
    monkeypatch,
):
    manager = SandboxSessionManager()
    client = _FakeClient("remote-1")
    monkeypatch.setattr(manager, "_new_client", lambda: client)
    manager.create_remote_session("veadk-1")

    manager.begin_turn("veadk-1")
    manager.begin_turn("veadk-1")

    manager.end_turn("veadk-1")
    assert client.idle_count == 0
    manager.end_turn("veadk-1")
    assert client.idle_count == 1

    manager.begin_turn("veadk-1")
    manager.begin_turn("veadk-1")
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
        dispatch_tool=lambda name, arguments, *, dispatch_id: (
            dispatched.append((name, arguments, dispatch_id)) or {"stdout": "ok"}
        )
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


def test_managed_agent_config_uses_frozen_snapshot():
    config = main_module.managed_agent_config(
        {
            "name": "Distributed Agent",
            "description": "test",
            "model": {"id": "model-from-session"},
            "system": "Remember prior turns.",
            "tools": [],
        }
    )

    assert config["name"] == "Distributed_Agent"
    assert config["model_name"] == "model-from-session"
    assert config["instruction"] == "Remember prior turns."
    assert config["tools"] == []
    assert config["before_tool_callback"] is None


def test_managed_agent_config_rejects_unknown_enabled_tool():
    snapshot = {
        "name": "demo",
        "model": "model",
        "tools": [
            {
                "type": "agent_toolset_20260401",
                "configs": [{"name": "unknown", "enabled": True}],
            }
        ],
    }

    try:
        main_module.managed_agent_config(snapshot)
    except ValueError as error:
        assert "unknown" in str(error)
    else:
        raise AssertionError("unknown enabled tools must be rejected")


def test_managed_agent_config_applies_toolset_defaults_and_overrides():
    snapshot = {
        "name": "demo",
        "model": "model",
        "tools": [
            {
                "type": "agent_toolset_20260401",
                "default_config": {"enabled": False},
                "configs": [
                    {"name": "bash", "enabled": True},
                    {"name": "read", "enabled": True},
                ],
            }
        ],
    }

    config = main_module.managed_agent_config(snapshot)

    assert [tool.__name__ for tool in config["tools"]] == ["bash", "read"]


def test_managed_agent_config_requires_runtime_context_for_custom_tools():
    custom_snapshot = {
        "name": "demo",
        "model": "model",
        "tools": [{"type": "custom", "name": "external_tool"}],
    }
    try:
        main_module.managed_agent_config(custom_snapshot)
    except ValueError as error:
        assert "custom" in str(error)
    else:
        raise AssertionError("unsupported top-level tool definitions must be rejected")


def test_managed_agent_config_requires_skills_to_be_materialized():
    snapshot = {
        "name": "demo",
        "model": "model",
        "tools": [],
        "skills": [{"type": "custom", "skill_id": "skill_x", "version": "v1"}],
    }
    try:
        main_module.managed_agent_config(snapshot)
    except ValueError as error:
        assert "skills (not materialized)" in str(error)
    else:
        raise AssertionError("Session Skills must be downloaded before Agent creation")


def test_managed_agent_config_loads_skill_instructions_and_custom_tool(tmp_path):
    skill = tmp_path / "demo-skill"
    skill.mkdir()
    (skill / "SKILL.md").write_text(
        "---\nname: demo-skill\ndescription: Demo\n---\nReturn the marker.\n"
    )
    snapshot = {
        "name": "demo",
        "model": "model",
        "skills": [{"type": "custom", "skill_id": "skill_x", "version": "v1"}],
        "tools": [
            {
                "type": "custom",
                "name": "approve",
                "description": "Approve",
                "input_schema": {"type": "object"},
            }
        ],
    }
    config = main_module.managed_agent_config(
        snapshot, sdk=SimpleNamespace(), session_id="session-1", skill_dirs=[skill]
    )
    assert "Return the marker." in config["instruction"]
    assert [tool.name for tool in config["tools"]] == ["approve"]


@pytest.mark.parametrize("result_type", ["user.tool_result", "agent.tool_result"])
def test_managed_worker_remote_tool_waits_for_matching_result(monkeypatch, result_type):
    monkeypatch.setenv("MANAGED_AGENT_TOOL_EXECUTION", "remote")
    batches = []

    async def send(session_id, *, events):
        batches.extend(events)

    async def list_events(session_id, **kwargs):
        yield {"type": "user.tool_result", "tool_use_id": "other", "content": "wrong"}
        yield {"type": result_type, "tool_use_id": "tool-1", "content": "remote-ok", "is_error": False}

    async def forbidden_local(*args, **kwargs):
        raise AssertionError("remote tools must not execute inside Agent Loop")

    monkeypatch.setattr(main_module, "_run_managed_tool", forbidden_local)
    sdk = SimpleNamespace(beta=SimpleNamespace(sessions=SimpleNamespace(
        events=SimpleNamespace(send=send, list=list_events)
    )))
    runtime = main_module.managed_work_tool_runtime(
        sdk, "session-1", workdir=Path("/tmp/managed-work")
    )
    result = asyncio.run(runtime.execute(SimpleNamespace(
        id="tool-1", name="bash", arguments={"command": "printf remote-ok"},
        tool=SimpleNamespace(),
    )))
    assert result == {"result": "remote-ok"}
    expected = ["agent.tool_use"]
    if result_type == "user.tool_result":
        expected.append("agent.tool_result")
        assert batches[-1]["tool_use_id"] == "tool-1"
        assert batches[-1]["content"] == "remote-ok"
    assert [event["type"] for event in batches] == expected


def test_managed_worker_executes_tool_locally_and_publishes_events(monkeypatch):
    sdk = SimpleNamespace(
        beta=SimpleNamespace(
            sessions=SimpleNamespace(events=SimpleNamespace(batches=[]))
        )
    )

    async def send(session_id, *, events):
        sdk.beta.sessions.events.batches.append((session_id, events))

    async def run_tool(name, arguments, *, workdir):
        assert name == "bash"
        assert arguments == {"command": "printf ok"}
        assert workdir == Path("/tmp/managed-work")
        return {
            "status": "completed",
            "exit_code": 0,
            "stdout": "ok",
            "stderr": "",
        }

    sdk.beta.sessions.events.send = send
    monkeypatch.setattr(main_module, "_run_managed_tool", run_tool)
    runtime = main_module.managed_work_tool_runtime(
        sdk, "session-1", workdir=Path("/tmp/managed-work")
    )
    result = asyncio.run(
        runtime.execute(
            SimpleNamespace(
                id="tool-1",
                name="bash",
                arguments={"command": "printf ok"},
                tool=SimpleNamespace(),
            )
        )
    )

    assert result["stdout"] == "ok"
    emitted = [
        event for _, batch in sdk.beta.sessions.events.batches for event in batch
    ]
    assert emitted == [
        {
            "type": "agent.tool_use",
            "id": "tool-1",
            "name": "bash",
            "input": {"command": "printf ok"},
            "evaluated_permission": "allow",
        },
        {
            "type": "agent.tool_result",
            "tool_use_id": "tool-1",
            "content": "exit=0\nok",
            "is_error": False,
        },
    ]


def test_always_ask_tool_waits_for_confirmation(monkeypatch, tmp_path):
    sdk = SimpleNamespace(
        beta=SimpleNamespace(
            sessions=SimpleNamespace(events=SimpleNamespace(batches=[]))
        )
    )

    async def send(session_id, *, events):
        sdk.beta.sessions.events.batches.append((session_id, list(events)))

    class Page:
        def __aiter__(self):
            async def iterate():
                yield SimpleNamespace(
                    type="user.tool_confirmation",
                    tool_use_id="tool-ask",
                    result="allow",
                )

            return iterate()

    sdk.beta.sessions.events.send = send
    sdk.beta.sessions.events.list = lambda *args, **kwargs: Page()

    async def run_tool(*args, **kwargs):
        return {"status": "completed", "exit_code": 0, "stdout": "ok", "stderr": ""}

    monkeypatch.setattr(main_module, "_run_managed_tool", run_tool)
    runtime = main_module.managed_work_tool_runtime(
        sdk,
        "session-1",
        workdir=tmp_path,
        snapshot={
            "tools": [
                {
                    "type": "agent_toolset_20260401",
                    "default_config": {"enabled": False},
                    "configs": [
                        {
                            "name": "bash",
                            "enabled": True,
                            "permission_policy": {"type": "always_ask"},
                        }
                    ],
                }
            ]
        },
    )
    result = asyncio.run(
        runtime.execute(
            SimpleNamespace(
                id="tool-ask",
                name="bash",
                arguments={"command": "printf ok"},
                tool=SimpleNamespace(),
            )
        )
    )
    assert result["stdout"] == "ok"
    batches = [batch for _, batch in sdk.beta.sessions.events.batches]
    assert batches[0][0]["evaluated_permission"] == "ask"
    assert batches[0][1]["stop_reason"]["type"] == "requires_action"
    assert batches[1] == [{"type": "session.status_running"}]


def test_mcp_tool_executes_locally_and_publishes_mcp_events(tmp_path):
    sdk = SimpleNamespace(
        beta=SimpleNamespace(
            sessions=SimpleNamespace(events=SimpleNamespace(batches=[]))
        )
    )

    async def send(session_id, *, events):
        sdk.beta.sessions.events.batches.append((session_id, list(events)))

    class FakeMcpTool:
        name = "mcp__orders__lookup"
        _mcp_tool = object()
        managed_agents_mcp_server_name = "orders"

        async def run_async(self, *, args, tool_context):
            return {"order": args["id"]}

    sdk.beta.sessions.events.send = send
    runtime = main_module.managed_work_tool_runtime(
        sdk, "session-1", workdir=tmp_path, snapshot={"tools": []}
    )
    result = asyncio.run(
        runtime.execute(
            SimpleNamespace(
                id="mcp-call-1",
                name=FakeMcpTool.name,
                arguments={"id": "42"},
                tool=FakeMcpTool(),
                context=SimpleNamespace(),
            )
        )
    )
    assert result == {"order": "42"}
    emitted = [
        event for _, batch in sdk.beta.sessions.events.batches for event in batch
    ]
    assert emitted[0] == {
        "type": "agent.mcp_tool_use",
        "id": "mcp-call-1",
        "mcp_server_name": "orders",
        "name": "lookup",
        "input": {"id": "42"},
    }
    assert emitted[1]["type"] == "agent.mcp_tool_result"
    assert emitted[1]["mcp_tool_use_id"] == "mcp-call-1"
    assert '"order": "42"' in emitted[1]["content"]


def test_managed_tool_environment_removes_control_plane_secrets(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_ENVIRONMENT_KEY", "secret")
    monkeypatch.setenv("DATABASE_POSTGRESQL_PASSWORD", "secret")
    monkeypatch.setenv("VEADK_MANAGED_SESSION_DB_URL", "secret")
    monkeypatch.setenv("MODEL_API_KEY", "secret")
    monkeypatch.setenv("SAFE_VALUE", "visible")

    environment = main_module._managed_tool_environment()

    assert "SAFE_VALUE" not in environment
    assert "ANTHROPIC_ENVIRONMENT_KEY" not in environment
    assert "DATABASE_POSTGRESQL_PASSWORD" not in environment
    assert "VEADK_MANAGED_SESSION_DB_URL" not in environment
    assert "MODEL_API_KEY" not in environment


def test_managed_worker_executes_real_local_tool(tmp_path):
    result = asyncio.run(
        main_module._run_managed_tool(
            "bash", {"command": "printf local-tool-ok"}, workdir=tmp_path
        )
    )

    assert result == {
        "status": "completed",
        "exit_code": 0,
        "stdout": "local-tool-ok",
        "stderr": "",
    }


def test_managed_worker_times_out_local_tool(tmp_path):
    result = asyncio.run(
        main_module._run_managed_tool(
            "bash",
            {"command": "sleep 30", "timeout_ms": 20},
            workdir=tmp_path,
        )
    )

    assert result["status"] == "failed"
    assert result["exit_code"] == -1
    assert "Timed out" in result["stderr"]


def test_managed_tool_translation_accepts_standard_names(tmp_path):
    target = tmp_path / "notes.txt"
    write_command, _ = main_module.SelfHostSandboxClient.tool_to_bash(
        "write", {"file_path": str(target), "content": "one\ntwo\nthree\n"}
    )
    edit_command, _ = main_module.SelfHostSandboxClient.tool_to_bash(
        "edit",
        {
            "file_path": str(target),
            "old_string": "three",
            "new_string": "THREE",
        },
    )

    read_command, _ = main_module.SelfHostSandboxClient.tool_to_bash(
        "read", {"file_path": str(target), "view_range": [2, 3]}
    )
    glob_command, _ = main_module.SelfHostSandboxClient.tool_to_bash(
        "glob", {"path": str(tmp_path), "pattern": "*.txt"}
    )
    grep_command, _ = main_module.SelfHostSandboxClient.tool_to_bash(
        "grep", {"path": str(tmp_path), "pattern": "two"}
    )

    async def run(command):
        process = await asyncio.create_subprocess_exec(
            "/bin/bash",
            "-lc",
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate()
        assert process.returncode == 0, stderr.decode()
        return stdout.decode()

    asyncio.run(run(write_command))
    asyncio.run(run(edit_command))
    assert "2\ttwo" in asyncio.run(run(read_command))
    assert target.read_text() == "one\ntwo\nTHREE\n"
    assert str(target) in asyncio.run(run(glob_command))
    assert "two" in asyncio.run(run(grep_command))


def test_managed_file_tools_are_confined_to_workdir(tmp_path):
    assert main_module._confined_tool_arguments(
        "write", {"file_path": "nested/file.txt", "content": "ok"}, workdir=tmp_path
    )["file_path"] == str(tmp_path / "nested/file.txt")

    for name, arguments in (
        ("read", {"file_path": "../outside.txt"}),
        ("glob", {"pattern": "../*"}),
        ("grep", {"path": "/etc", "pattern": "root"}),
    ):
        try:
            main_module._confined_tool_arguments(name, arguments, workdir=tmp_path)
        except ValueError as error:
            assert "outside MANAGED_AGENT_WORKDIR" in str(
                error
            ) or "must be relative" in str(error)
        else:
            raise AssertionError(f"{name} must reject paths outside workdir")


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
