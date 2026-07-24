# Copyright (c) 2025 Beijing Volcano Engine Technology Co., Ltd. and/or its affiliates.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Tests for deploy-time Studio Sandbox Tool provisioning."""

from types import SimpleNamespace

from veadk.cli.studio_sandbox_tools import (
    ensure_studio_code_env_tool,
    ensure_studio_hermes_tool,
    ensure_studio_openclaw_tool,
)


def test_ensure_studio_code_env_tool_reuses_ready_exact_name() -> None:
    client = SimpleNamespace(
        list_tools=lambda _: SimpleNamespace(
            tools=[
                SimpleNamespace(
                    name="veadk-studio-demo-chat-12345678",
                    project_name="default",
                    tool_type="CodeEnv",
                    tool_id="tool-existing",
                )
            ],
            next_token=None,
        ),
        get_tool=lambda _: SimpleNamespace(status="Ready"),
        create_tool=lambda _: (_ for _ in ()).throw(
            AssertionError("ready Tool must be reused")
        ),
    )

    assert (
        ensure_studio_code_env_tool(
            name="veadk-studio-demo-chat-12345678",
            client=client,
            timeout_seconds=0,
        )
        == "tool-existing"
    )


def test_ensure_studio_code_env_tool_creates_ready_code_env() -> None:
    requests: list[object] = []

    def _create(request: object) -> SimpleNamespace:
        requests.append(request)
        return SimpleNamespace(tool_id="tool-created")

    client = SimpleNamespace(
        list_tools=lambda _: SimpleNamespace(tools=[], next_token=None),
        get_tool=lambda _: SimpleNamespace(status="Ready"),
        create_tool=_create,
    )

    assert (
        ensure_studio_code_env_tool(
            name="veadk-studio-demo-skill-12345678",
            client=client,
            timeout_seconds=0,
        )
        == "tool-created"
    )
    request = requests[0]
    assert getattr(request, "name") == "veadk-studio-demo-skill-12345678"
    assert getattr(request, "tool_type") == "CodeEnv"
    assert getattr(request, "project_name") == "default"
    assert getattr(request, "cpu_milli") == 4000
    assert getattr(request, "memory_mb") == 8192
    assert getattr(request, "envs") is None


def test_ensure_studio_openclaw_tool_reuses_tagged_tool() -> None:
    tagged = SimpleNamespace(
        name="shared-openclaw",
        project_name="default",
        tool_type="Private",
        tool_id="tool-openclaw",
        tags=[
            SimpleNamespace(
                key="veadk-studio-purpose",
                value="openclaw",
            )
        ],
    )
    calls = 0

    def _list(_: object) -> SimpleNamespace:
        nonlocal calls
        calls += 1
        return SimpleNamespace(tools=[tagged] if calls == 1 else [])

    client = SimpleNamespace(
        list_tools=_list,
        get_tool=lambda _: SimpleNamespace(
            status="Ready",
            image_url="an-existing-openclaw-image",
            port=8080,
        ),
        update_tool=lambda _: (_ for _ in ()).throw(
            AssertionError("reused Tool configuration must not be overwritten")
        ),
        create_tool=lambda _: (_ for _ in ()).throw(
            AssertionError("tagged Tool must be reused")
        ),
    )

    assert (
        ensure_studio_openclaw_tool(
            name="veadk-studio-demo-openclaw-12345678",
            image_url="registry/arkclaw:test",
            model_api_key="",
            model_name="",
            model_base_url="",
            client=client,
            timeout_seconds=0,
        )
        == "tool-openclaw"
    )


def test_ensure_studio_openclaw_tool_creates_image_and_model_envs() -> None:
    requests: list[object] = []
    client = SimpleNamespace(
        list_tools=lambda _: SimpleNamespace(tools=[], next_token=None),
        get_tool=lambda _: SimpleNamespace(
            status="Ready",
            image_url="registry/arkclaw:test",
            port=8080,
        ),
        create_tool=lambda request: (
            requests.append(request) or SimpleNamespace(tool_id="tool-created")
        ),
    )

    assert (
        ensure_studio_openclaw_tool(
            name="veadk-studio-demo-openclaw-12345678",
            image_url="registry/arkclaw:test",
            model_api_key="ark-secret",
            model_name="minimax-m3",
            model_base_url="https://ark.example/api/v3",
            client=client,
            timeout_seconds=0,
        )
        == "tool-created"
    )
    request = requests[0]
    assert request.tool_type == "Private"
    assert request.image_url == "registry/arkclaw:test"
    assert request.command == "/opt/gem/run.sh"
    assert request.port == 8080
    assert {env.key: env.value for env in request.envs} == {
        "MODEL_AGENT_API_KEY": "ark-secret",
        "MODEL_AGENT_NAME": "minimax-m3",
        "MODEL_AGENT_BASE_URL": "https://ark.example/api/v3",
    }
    assert {tag.key: tag.value for tag in request.tags} == {
        "veadk-studio-purpose": "openclaw"
    }


def test_ensure_studio_hermes_tool_uses_dedicated_branding_and_tag() -> None:
    requests: list[object] = []
    client = SimpleNamespace(
        list_tools=lambda _: SimpleNamespace(tools=[], next_token=None),
        get_tool=lambda _: SimpleNamespace(
            status="Ready",
            image_url="registry/hermes:test",
            port=8080,
        ),
        create_tool=lambda request: (
            requests.append(request) or SimpleNamespace(tool_id="tool-hermes")
        ),
    )

    assert (
        ensure_studio_hermes_tool(
            name="veadk-studio-demo-hermes-12345678",
            image_url="registry/hermes:test",
            model_api_key="ark-secret",
            model_name="doubao-seed-evolving",
            model_base_url="https://ark.example/api/v3",
            client=client,
            timeout_seconds=0,
        )
        == "tool-hermes"
    )
    request = requests[0]
    assert request.command == "/opt/gem/run.sh"
    assert request.description == "Reusable VeADK Studio Hermes sandbox image"
    assert {tag.key: tag.value for tag in request.tags} == {
        "veadk-studio-purpose": "hermes"
    }


def test_ensure_studio_openclaw_tool_requires_model_envs_only_for_creation() -> None:
    client = SimpleNamespace(
        list_tools=lambda _: SimpleNamespace(tools=[], next_token=None),
        create_tool=lambda _: (_ for _ in ()).throw(
            AssertionError("invalid configuration must fail before creation")
        ),
    )

    try:
        ensure_studio_openclaw_tool(
            name="veadk-studio-demo-openclaw-12345678",
            image_url="registry/arkclaw:test",
            model_api_key="",
            model_name="minimax-m3",
            model_base_url="https://ark.example/api/v3",
            client=client,
            timeout_seconds=0,
        )
    except ValueError as error:
        assert "MODEL_AGENT_API_KEY" in str(error)
    else:
        raise AssertionError("new Tool must require its model API key")
