"""Materialize resources referenced by a frozen Managed Agent Session."""

from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import Any

import anthropic
from anthropic.lib.environments import download_session_skills
from google.adk.tools.mcp_tool.mcp_session_manager import (
    StreamableHTTPConnectionParams,
)
from google.adk.tools.mcp_tool.mcp_toolset import McpToolset


class ManagedMcpToolset(McpToolset):
    """Keep the owning server name on resolved tools for event attribution."""

    def __init__(self, *, server_name: str, **kwargs: Any) -> None:
        self.managed_agents_server_name = server_name
        super().__init__(**kwargs)

    async def get_tools(self, readonly_context: Any = None) -> list[Any]:
        tools = await super().get_tools(readonly_context)
        for tool in tools:
            tool.managed_agents_mcp_server_name = self.managed_agents_server_name
        return tools


def field(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)


def session_workdir(root: Path, session_id: str) -> Path:
    safe_id = re.sub(r"[^A-Za-z0-9_.-]", "_", session_id).strip("._")
    if not safe_id:
        raise ValueError("Managed Session id cannot produce an empty workdir name")
    result = (root.resolve() / "sessions" / safe_id).resolve()
    result.relative_to(root.resolve())
    result.mkdir(parents=True, exist_ok=True, mode=0o700)
    return result


async def materialize_session_skills(
    session: Any, workdir: Path, *, client: anthropic.AsyncAnthropic
) -> list[Path]:
    """Download every pinned Skill with the claimed Work's scoped client."""
    references = list(field(field(session, "agent"), "skills", []) or [])
    if not references:
        return []
    downloaded = await download_session_skills(client, workdir=workdir, session=session)
    if len(downloaded) != len(references):
        await cleanup_session_skills(downloaded)
        raise RuntimeError(
            f"downloaded {len(downloaded)} of {len(references)} Session Skills"
        )
    return downloaded


async def cleanup_session_skills(skill_dirs: list[Path]) -> None:
    for directory in skill_dirs:
        await __import__("asyncio").to_thread(shutil.rmtree, directory, True)


def skill_instructions(skill_dirs: list[Path]) -> str:
    """Build model context for materialized skills without executing them directly."""
    blocks: list[str] = []
    for directory in skill_dirs:
        skill_file = directory / "SKILL.md"
        if not skill_file.is_file():
            raise RuntimeError(f"downloaded Skill has no SKILL.md: {directory}")
        content = skill_file.read_text(encoding="utf-8")
        blocks.append(
            f"<skill name={directory.name!r} path={str(skill_file)!r}>\n"
            f"{content}\n</skill>"
        )
    if not blocks:
        return ""
    return (
        "\n\n<managed_agent_skills>\n"
        "The following Session-pinned skills were downloaded by the Worker. "
        "Follow their instructions when relevant. Supporting files are below each listed path.\n"
        + "\n\n".join(blocks)
        + "\n</managed_agent_skills>"
    )


def _enabled(config: Any, default: bool) -> bool:
    value = field(config, "enabled")
    return default if value is None else bool(value)


def mcp_toolsets(snapshot: Any) -> list[McpToolset]:
    """Translate frozen URL MCP definitions and tool policies into ADK toolsets."""
    servers = {
        str(field(server, "name", "")): server
        for server in field(snapshot, "mcp_servers", []) or []
    }
    result: list[McpToolset] = []
    for toolset in field(snapshot, "tools", []) or []:
        if field(toolset, "type") != "mcp_toolset":
            continue
        server_name = str(field(toolset, "mcp_server_name", ""))
        server = servers.get(server_name)
        if server is None:
            raise ValueError(f"MCP toolset references unknown server {server_name!r}")
        if field(server, "type") != "url":
            raise ValueError(f"MCP server {server_name!r} must use type=url")
        url = str(field(server, "url", ""))
        if not url.startswith(("http://", "https://")):
            raise ValueError(f"MCP server {server_name!r} must use an HTTP(S) URL")

        default = field(toolset, "default_config", {}) or {}
        default_enabled = _enabled(default, True)
        configs = {
            str(field(item, "name", "")): item
            for item in field(toolset, "configs", []) or []
        }

        def selected(
            tool: Any,
            _context: Any,
            *,
            configs=configs,
            default_enabled=default_enabled,
        ) -> bool:
            config = configs.get(str(getattr(tool, "name", "")), {})
            return _enabled(config, default_enabled)

        result.append(
            ManagedMcpToolset(
                server_name=server_name,
                connection_params=StreamableHTTPConnectionParams(url=url),
                tool_filter=selected,
                tool_name_prefix=f"mcp__{server_name}_",
            )
        )
    return result


__all__ = [
    "cleanup_session_skills",
    "field",
    "materialize_session_skills",
    "mcp_toolsets",
    "session_workdir",
    "skill_instructions",
]
