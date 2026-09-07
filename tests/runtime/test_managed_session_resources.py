from __future__ import annotations

import asyncio
import importlib
import os
import socket
import subprocess
import sys
import textwrap
import time
from pathlib import Path
from types import SimpleNamespace

EXAMPLE_DIR = Path(__file__).parents[2] / "examples" / "16_self_host_sandbox"
if str(EXAMPLE_DIR) not in sys.path:
    sys.path.insert(0, str(EXAMPLE_DIR))

os.environ.setdefault("ANTHROPIC_BASE_URL", "https://sandbox.example.com")
os.environ.setdefault("ANTHROPIC_ENVIRONMENT_KEY", "test-token")
os.environ.setdefault("MODEL_AGENT_API_KEY", "test-model-api-key")
os.environ.setdefault("MODEL_AGENT_NAME", "test-model")

resources = importlib.import_module("managed_session_resources")
main = importlib.import_module("main")


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def test_worker_uses_dispatcher_scoped_client_for_pinned_skill_download(
    monkeypatch, tmp_path
) -> None:
    calls = []
    scoped_client = SimpleNamespace(name="scoped")
    session = SimpleNamespace(
        agent=SimpleNamespace(
            skills=[SimpleNamespace(type="custom", skill_id="skill_a", version="v1")]
        )
    )

    async def download(client, *, workdir, session):
        calls.append((client, workdir, session.agent.skills[0].version))
        path = workdir / "skills" / "skill-a"
        path.mkdir(parents=True)
        return [path]

    monkeypatch.setattr(resources, "download_session_skills", download)

    downloaded = asyncio.run(
        resources.materialize_session_skills(
            session, tmp_path / "work", client=scoped_client
        )
    )

    assert downloaded == [tmp_path / "work" / "skills" / "skill-a"]
    assert calls == [(scoped_client, tmp_path / "work", "v1")]


def test_real_mcp_server_is_restored_from_session_snapshot(tmp_path) -> None:
    port = free_port()
    server = tmp_path / "mcp_server.py"
    server.write_text(
        textwrap.dedent(
            f"""
            from fastmcp import FastMCP
            mcp = FastMCP("managed-session-test")
            @mcp.tool
            def echo_marker(value: str) -> str:
                return "mcp-restored:" + value
            mcp.run(transport="http", host="127.0.0.1", port={port}, path="/mcp", show_banner=False)
            """
        )
    )
    process = subprocess.Popen(
        [sys.executable, str(server)], stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    try:
        for _ in range(100):
            with socket.socket() as sock:
                if sock.connect_ex(("127.0.0.1", port)) == 0:
                    break
            time.sleep(0.05)
        else:
            raise AssertionError("MCP server did not start")

        snapshot = {
            "mcp_servers": [
                {"type": "url", "name": "echo", "url": f"http://127.0.0.1:{port}/mcp"}
            ],
            "tools": [
                {
                    "type": "mcp_toolset",
                    "mcp_server_name": "echo",
                    "default_config": {
                        "enabled": True,
                        "permission_policy": {"type": "always_allow"},
                    },
                    "configs": [],
                }
            ],
        }
        toolset = resources.mcp_toolsets(snapshot)[0]

        async def use_tool():
            tools = await toolset.get_tools_with_prefix()
            assert [tool.name for tool in tools] == ["mcp__echo__echo_marker"]
            result = await tools[0].run_async(
                args={"value": "ok"}, tool_context=SimpleNamespace(state={})
            )
            await toolset.close()
            return result

        assert "mcp-restored:ok" in str(asyncio.run(use_tool()))
    finally:
        process.terminate()
        process.wait(timeout=5)


def test_custom_tool_waits_for_matching_result_and_resumes() -> None:
    batches = []

    class Page:
        def __aiter__(self):
            async def iterate():
                yield SimpleNamespace(
                    type="user.custom_tool_result",
                    custom_tool_use_id="call-1",
                    content=[{"type": "text", "text": "approved-result"}],
                    is_error=False,
                )

            return iterate()

    async def send(session_id, *, events):
        batches.append((session_id, list(events)))

    sdk = SimpleNamespace(
        beta=SimpleNamespace(
            sessions=SimpleNamespace(
                events=SimpleNamespace(send=send, list=lambda *args, **kwargs: Page())
            )
        )
    )
    tool = main.ManagedCustomTool(
        {
            "type": "custom",
            "name": "approve",
            "description": "Approve",
            "input_schema": {"type": "object"},
        },
        sdk,
        "session-1",
    )
    result = asyncio.run(
        tool.run_async(
            args={"item": "x"},
            tool_context=SimpleNamespace(function_call_id="call-1"),
        )
    )
    assert result == {"result": "approved-result"}
    assert batches[0][1][0]["type"] == "agent.custom_tool_use"
    assert batches[0][1][1]["stop_reason"]["action_type"] == "custom_tool_result"
    assert batches[1][1] == [{"type": "session.status_running"}]
