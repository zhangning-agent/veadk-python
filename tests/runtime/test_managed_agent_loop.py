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
import sys
from pathlib import Path
from types import SimpleNamespace


EXAMPLE_DIR = Path(__file__).parents[2] / "examples" / "16_self_host_sandbox"
if str(EXAMPLE_DIR) not in sys.path:
    sys.path.insert(0, str(EXAMPLE_DIR))

loop_module = importlib.import_module("managed_agent_loop")
ManagedAgentsLoop = loop_module.ManagedAgentsLoop


class _SessionService:
    def __init__(self, *, exists=True):
        self.session = object() if exists else None
        self.created = []

    async def get_session(self, **kwargs):
        return self.session

    async def create_session(self, **kwargs):
        self.created.append(kwargs)
        self.session = object()
        return self.session


class _Runner:
    app_name = "managed-agent-test"

    def __init__(self, events, *, session_exists=True):
        self.events = events
        self.session_service = _SessionService(exists=session_exists)
        self.calls = []

    async def run_async(self, **kwargs):
        self.calls.append(kwargs)
        for event in self.events:
            yield event


class _EventsAPI:
    def __init__(self):
        self.batches = []

    async def send(self, session_id, *, events):
        batch = list(events)
        self.batches.append((session_id, batch))
        data = []
        for event in batch:
            event_id = (
                "span-start-1"
                if event["type"] == "span.model_request_start"
                else "event"
            )
            data.append(SimpleNamespace(type=event["type"], id=event_id))
        return SimpleNamespace(data=data)


class _SDK:
    def __init__(self):
        self.beta = SimpleNamespace(
            sessions=SimpleNamespace(events=_EventsAPI()),
        )


class _SessionClient:
    session_id = "managed-session-1"

    @staticmethod
    def _event_seq(event):
        return int(event.get("_seq", 0))


def _event(*parts, usage=None, partial=False, function_calls=None):
    return SimpleNamespace(
        partial=partial,
        content=SimpleNamespace(parts=list(parts)),
        usage_metadata=usage,
        get_function_calls=lambda: list(function_calls or []),
    )


def _part(text, *, thought=False):
    return SimpleNamespace(text=text, thought=thought)


def test_turn_maps_veadk_events_to_managed_agent_events():
    usage = SimpleNamespace(
        prompt_token_count=3,
        candidates_token_count=2,
        cached_content_token_count=1,
    )
    runner = _Runner(
        [
            _event(_part("considering", thought=True)),
            _event(_part("ignored"), function_calls=[object()]),
            _event(_part("final answer"), usage=usage),
        ],
        session_exists=False,
    )
    sdk = _SDK()
    loop = ManagedAgentsLoop(runner=runner, session_client=_SessionClient())
    user_event = SimpleNamespace(
        content=[SimpleNamespace(type="text", text="hello")],
    )

    asyncio.run(loop._run_turn(sdk, user_event))

    emitted = [
        event for _, batch in sdk.beta.sessions.events.batches for event in batch
    ]
    assert [event["type"] for event in emitted] == [
        "session.status_running",
        "span.model_request_start",
        "agent.thinking",
        "agent.message",
        "span.model_request_end",
        "session.status_idle",
    ]
    assert emitted[2]["text"] == "considering"
    assert emitted[3]["content"] == [{"type": "text", "text": "final answer"}]
    assert emitted[4] == {
        "type": "span.model_request_end",
        "model_request_start_id": "span-start-1",
        "model_usage": {
            "input_tokens": 3,
            "output_tokens": 2,
            "cache_read_input_tokens": 1,
            "cache_creation_input_tokens": 0,
        },
    }
    assert runner.session_service.created == [
        {
            "app_name": "managed-agent-test",
            "user_id": "managed_agents_user",
            "session_id": "managed-session-1",
        }
    ]
    assert runner.calls[0]["session_id"] == "managed-session-1"
    assert runner.calls[0]["new_message"].parts[0].text == "hello"


def test_turn_failure_emits_span_error_and_session_error_without_idle():
    class _FailingRunner(_Runner):
        async def run_async(self, **kwargs):
            if False:
                yield None
            raise RuntimeError("model failed")

    sdk = _SDK()
    loop = ManagedAgentsLoop(
        runner=_FailingRunner([]),
        session_client=_SessionClient(),
    )

    asyncio.run(
        loop._run_turn(
            sdk,
            SimpleNamespace(content=[{"type": "text", "text": "hello"}]),
        )
    )

    emitted = [
        event for _, batch in sdk.beta.sessions.events.batches for event in batch
    ]
    assert [event["type"] for event in emitted] == [
        "session.status_running",
        "span.model_request_start",
        "span.model_request_end",
        "session.error",
    ]
    assert emitted[-1]["error"] == "model failed"


def test_invalid_user_message_emits_session_error_without_starting_runner():
    runner = _Runner([])
    sdk = _SDK()
    loop = ManagedAgentsLoop(runner=runner, session_client=_SessionClient())

    asyncio.run(
        loop._run_turn(
            sdk,
            SimpleNamespace(content=[{"type": "image", "source": "ignored"}]),
        )
    )

    emitted = [
        event for _, batch in sdk.beta.sessions.events.batches for event in batch
    ]
    assert emitted == [
        {
            "type": "session.error",
            "error": "user.message must contain at least one text block",
        }
    ]
    assert runner.calls == []


def test_recovery_only_marks_user_messages_followed_by_terminal_event_complete():
    loop = ManagedAgentsLoop(runner=_Runner([]), session_client=_SessionClient())

    loop._restore_completed_inputs(
        [
            {"_seq": 1, "id": "old", "type": "user.message"},
            {"_seq": 2, "type": "session.status_running"},
            {"_seq": 3, "type": "session.status_idle"},
            {"_seq": 4, "id": "pending", "type": "user.message"},
            {"_seq": 5, "type": "session.status_running"},
        ]
    )

    assert loop._completed_inputs == {"old"}
