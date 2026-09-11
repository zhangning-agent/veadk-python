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

import importlib.util
import json
import subprocess
from pathlib import Path

import httpx2
import pytest

CLIENT_PATH = (
    Path(__file__).parents[2]
    / "examples"
    / "16_self_host_sandbox"
    / "sandbox_client.py"
)
SPEC = importlib.util.spec_from_file_location("self_host_sandbox_client", CLIENT_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
SelfHostSandboxClient = MODULE.SelfHostSandboxClient


def _client():
    return SelfHostSandboxClient(
        base_url="https://sandbox.example.com",
        environment_id="env-123",
        agent_id="agent-123",
        session_id="session-123",
        bearer_token="token",
        remote_bash_tool_name="bash",
    )


def test_execute_command_posts_agent_tool_use_and_correlates_worker_result(
    monkeypatch,
):
    client = _client()
    posted_events = []
    event_batches = iter(
        [
            [{"_seq": 10, "type": "session.status_idle"}],
            [
                {
                    "_seq": 11,
                    "type": "agent.tool_use",
                    "id": "call-123",
                    "name": "bash",
                    "input": {"command": "echo hello", "timeout": 5000},
                },
                {
                    "_seq": 12,
                    "type": "user.tool_result",
                    "tool_use_id": "call-123",
                    "content": "exit=0\nhello\n",
                    "is_error": False,
                },
            ],
        ]
    )

    monkeypatch.setattr(client, "list_events", lambda **kwargs: next(event_batches))
    monkeypatch.setattr(
        client,
        "post_events",
        lambda events: posted_events.extend(events) or {"status": "accepted"},
    )

    result = client.execute_command(
        "echo hello",
        timeout=5,
        dispatch_id="call-123",
    )

    assert posted_events == [
        {
            "type": "agent.tool_use",
            "id": "call-123",
            "name": "bash",
            "input": {
                "command": "echo hello",
                "timeout_ms": 5000,
                "timeout": 5000,
            },
        }
    ]
    assert result == {
        "dispatch_id": "call-123",
        "tool_use_id": "call-123",
        "environment_id": "env-123",
        "session_id": "session-123",
        "status": "completed",
        "exit_code": 0,
        "stdout": "hello\n",
        "stderr": "",
    }


def test_send_tool_result_uses_payload_shape(monkeypatch):
    client = _client()
    posted_events = []
    monkeypatch.setattr(
        client,
        "post_events",
        lambda events: posted_events.extend(events) or {"status": "accepted"},
    )

    client.send_tool_result("tool-123", "done")

    assert posted_events == [
        {
            "type": "user.tool_result",
            "tool_use_id": "tool-123",
            "content": [{"type": "text", "text": "done"}],
            "is_error": False,
        }
    ]


def test_file_tools_are_converted_to_working_bash_commands(tmp_path):
    client = _client()
    target = tmp_path / "nested" / "example.txt"

    write_command, _ = client._tool_to_bash(
        "write_file",
        {"file_path": str(target), "content": "alpha\nbeta\n"},
    )
    subprocess.run(write_command, shell=True, check=True)
    assert target.read_text() == "alpha\nbeta\n"

    edit_command, _ = client._tool_to_bash(
        "edit_file",
        {
            "file_path": str(target),
            "old_string": "beta",
            "new_string": "gamma",
        },
    )
    subprocess.run(edit_command, shell=True, check=True)
    assert target.read_text() == "alpha\ngamma\n"

    read_command, _ = client._tool_to_bash(
        "read_file",
        {"file_path": str(target), "offset": 2, "limit": 1},
    )
    result = subprocess.run(
        read_command,
        shell=True,
        check=True,
        capture_output=True,
        text=True,
    )
    assert result.stdout == "2\tgamma\n"


def test_non_mcp_tools_are_mapped_to_bash_and_unknown_tools_fail():
    client = _client()

    list_command, _ = client._tool_to_bash(
        "list_files", {"path": "/workspace", "max_depth": 2}
    )
    search_command, _ = client._tool_to_bash(
        "search_files", {"pattern": "needle", "glob": "*.py"}
    )
    python_command, _ = client._tool_to_bash(
        "python", {"code": "print('ok')", "workdir": "/workspace"}
    )

    assert list_command.startswith("find /workspace")
    assert "command -v rg" in search_command
    assert "python3 -c" in python_command

    try:
        client._tool_to_bash("unknown_tool", {})
    except ValueError as error:
        assert "add an explicit bash adapter" in str(error)
    else:
        raise AssertionError("unknown non-MCP tools must fail closed")


def test_followup_tool_calls_only_use_session_events():
    requests = []
    events = []

    def handler(request):
        requests.append((request.method, request.url.path))
        assert request.url.path == "/v1/sessions/session-123/events"
        if request.method == "POST":
            posted = json.loads(request.content)["events"]
            for event in posted:
                events.append(event)
                if event["type"] == "agent.tool_use":
                    events.append({
                        "type": "user.tool_result", "tool_use_id": event["id"],
                        "content": "exit=0\nmessage-ok", "is_error": False,
                    })
            return httpx2.Response(200, json={"data": posted})
        return httpx2.Response(200, json={"data": events, "has_more": False})

    client = _client()
    client.client.close()
    with MODULE.anthropic.Anthropic(
        base_url="https://sandbox.example.com", auth_token="token", api_key=None,
        http_client=httpx2.Client(transport=httpx2.MockTransport(handler)),
    ) as api:
        client.client = api
        for turn in range(3):
            result = client.execute_command("printf message-ok", timeout=1, dispatch_id=f"call-{turn}")
            assert result["status"] == "completed"
            assert result["tool_use_id"] == f"call-{turn}"
            client.post_status_idle()
    assert [event["type"] for event in events] == ["agent.tool_use", "user.tool_result", "session.status_idle"] * 3
    assert [method for method, _ in requests] == ["POST", "GET", "POST"] * 3


def test_failed_followup_event_does_not_wait_or_use_another_endpoint(monkeypatch):
    client = _client()
    monkeypatch.setattr(client, "post_events", lambda _: None)
    client.post_status_idle()

    def fail(_):
        raise RuntimeError("event unavailable")

    def unexpected_wait(**kwargs):
        pytest.fail("must not wait when the event failed to send")

    monkeypatch.setattr(client, "post_events", fail)
    monkeypatch.setattr(client, "_wait_for_tool_result", unexpected_wait)
    with pytest.raises(RuntimeError, match="event unavailable"):
        client.execute_command("true")
