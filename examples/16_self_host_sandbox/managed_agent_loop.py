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

"""Event-driven Managed Agents loop backed entirely by the Anthropic SDK."""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any

from google.genai import types

from veadk.utils.adk_compat import get_event_function_calls

from sandbox_client import SelfHostSandboxClient

logger = logging.getLogger("veadk.managed_agent_loop")

_TERMINAL_EVENT_TYPES = {
    "session.status_idle",
    "session.status_terminated",
    "session.error",
}


class ManagedAgentsLoop:
    """Consume one pending Managed Session turn and drive a VeADK Runner.

    A self-hosted sandbox is normally started for one WorkItem. Consequently
    ``run(max_turns=1)`` handles one uncompleted ``user.message``, emits the
    canonical Agent/Session events, and exits after ``session.status_idle``.
    A later user message creates another WorkItem and starts a fresh sandbox.
    """

    def __init__(
        self,
        *,
        runner: Any,
        session_client: SelfHostSandboxClient,
        user_id: str = "managed_agents_user",
    ) -> None:
        if not session_client.session_id:
            raise ValueError(
                "ANTHROPIC_SESSION_ID (or SANDBOX_SESSION_ID) is required for Agent Loop mode."
            )
        self.runner = runner
        self.session_client = session_client
        self.session_id = session_client.session_id
        self.user_id = user_id
        self._completed_inputs: set[str] = set()

    async def run(
        self,
        *,
        max_turns: int | None = 1,
        stop_event: asyncio.Event | None = None,
    ) -> int:
        """Listen through SDK SSE, process pending user messages, and return turns run."""
        self._restore_completed_inputs(
            await asyncio.to_thread(self.session_client.list_events)
        )
        turns = 0
        backoff = 0.25
        stop_after_terminal = False

        async with self.session_client.create_async_client() as sdk:
            while stop_event is None or not stop_event.is_set():
                try:
                    stream = await sdk.beta.sessions.events.stream(self.session_id)
                    terminal_seen = False
                    async with stream:
                        async for event in stream:
                            event_type = str(getattr(event, "type", "") or "")
                            if event_type == "session.status_terminated":
                                return turns
                            if (
                                stop_after_terminal
                                and event_type in _TERMINAL_EVENT_TYPES
                            ):
                                terminal_seen = True
                                continue
                            if event_type != "user.message":
                                continue

                            event_id = str(getattr(event, "id", "") or "")
                            if event_id and event_id in self._completed_inputs:
                                continue

                            await self._run_turn(sdk, event)
                            if event_id:
                                self._completed_inputs.add(event_id)
                            turns += 1
                            if max_turns is not None and turns >= max_turns:
                                stop_after_terminal = True
                    if terminal_seen:
                        return turns
                    backoff = 0.25
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception(
                        "Managed Session event stream failed; reconnecting in %.2fs",
                        backoff,
                    )
                    if stop_event is not None:
                        try:
                            await asyncio.wait_for(stop_event.wait(), timeout=backoff)
                            return turns
                        except TimeoutError:
                            pass
                    else:
                        await asyncio.sleep(backoff)
                    backoff = min(backoff * 2, 5.0)
        return turns

    def _restore_completed_inputs(self, events: list[dict[str, Any]]) -> None:
        """Mark inputs followed by a terminal event, leaving interrupted turns pending."""
        pending: list[str] = []
        for event in sorted(events, key=self.session_client._event_seq):
            event_type = str(event.get("type") or "")
            if event_type == "user.message":
                event_id = str(event.get("id") or "")
                if event_id:
                    pending.append(event_id)
            elif event_type in _TERMINAL_EVENT_TYPES:
                self._completed_inputs.update(pending)
                pending.clear()

    async def _run_turn(self, sdk: Any, user_event: Any) -> None:
        span_id = ""
        usage = {
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_read_input_tokens": 0,
            "cache_creation_input_tokens": 0,
        }

        try:
            prompt = self._message_text(getattr(user_event, "content", None))
            await self._ensure_local_session()
            started = await sdk.beta.sessions.events.send(
                self.session_id,
                events=[
                    {"type": "session.status_running"},
                    {"type": "span.model_request_start"},
                ],
            )
            for event in getattr(started, "data", None) or []:
                if getattr(event, "type", None) == "span.model_request_start":
                    span_id = str(getattr(event, "id", "") or "")
                    break
            if not span_id:
                raise RuntimeError(
                    "events.send did not return the persisted span.model_request_start id"
                )

            message = types.Content(role="user", parts=[types.Part(text=prompt)])
            async for event in self.runner.run_async(
                user_id=self.user_id,
                session_id=self.session_id,
                new_message=message,
            ):
                self._merge_usage(usage, getattr(event, "usage_metadata", None))
                await self._publish_runner_event(sdk, event)

            await sdk.beta.sessions.events.send(
                self.session_id,
                events=[
                    {
                        "type": "span.model_request_end",
                        "model_request_start_id": span_id,
                        "model_usage": usage,
                    },
                    {
                        "type": "session.status_idle",
                        "stop_reason": {"type": "end_turn"},
                    },
                ],
            )
        except asyncio.CancelledError:
            raise
        except Exception as error:
            logger.exception("Managed Agents turn failed")
            events: list[dict[str, Any]] = []
            if span_id:
                events.append(
                    {
                        "type": "span.model_request_end",
                        "model_request_start_id": span_id,
                        "model_usage": usage,
                        "is_error": True,
                    }
                )
            events.append({"type": "session.error", "error": str(error)})
            await sdk.beta.sessions.events.send(self.session_id, events=events)

    async def _ensure_local_session(self) -> None:
        service = self.runner.session_service
        session = await service.get_session(
            app_name=self.runner.app_name,
            user_id=self.user_id,
            session_id=self.session_id,
        )
        if session is None:
            await service.create_session(
                app_name=self.runner.app_name,
                user_id=self.user_id,
                session_id=self.session_id,
            )

    async def _publish_runner_event(self, sdk: Any, event: Any) -> None:
        if bool(getattr(event, "partial", False)):
            return

        # Remote tools publish agent.tool_use before execution and the client
        # canonicalizes user.tool_result into agent.tool_result. Do not duplicate
        # those events when ADK later exposes its function-call bookkeeping.
        if get_event_function_calls(event):
            return

        output_events: list[dict[str, Any]] = []
        content = getattr(event, "content", None)
        for part in getattr(content, "parts", None) or []:
            text = str(getattr(part, "text", "") or "")
            if not text:
                continue
            if bool(getattr(part, "thought", False)):
                output_events.append(
                    {
                        "type": "agent.thinking",
                        "thinking_id": f"thinking_{uuid.uuid4().hex}",
                        "text": text,
                    }
                )
            else:
                output_events.append(
                    {
                        "type": "agent.message",
                        "message_id": f"message_{uuid.uuid4().hex}",
                        "content": [{"type": "text", "text": text}],
                    }
                )
        if output_events:
            await sdk.beta.sessions.events.send(self.session_id, events=output_events)

    @staticmethod
    def _message_text(content: Any) -> str:
        blocks = content if isinstance(content, list) else list(content or [])
        parts: list[str] = []
        for block in blocks:
            block_type = (
                block.get("type")
                if isinstance(block, dict)
                else getattr(block, "type", None)
            )
            if block_type != "text":
                continue
            text = (
                block.get("text")
                if isinstance(block, dict)
                else getattr(block, "text", None)
            )
            if text:
                parts.append(str(text))
        if not parts:
            raise ValueError("user.message must contain at least one text block")
        return "\n".join(parts)

    @staticmethod
    def _merge_usage(target: dict[str, int], usage: Any) -> None:
        if usage is None:
            return
        target["input_tokens"] += int(getattr(usage, "prompt_token_count", 0) or 0)
        target["output_tokens"] += int(getattr(usage, "candidates_token_count", 0) or 0)
        target["cache_read_input_tokens"] += int(
            getattr(usage, "cached_content_token_count", 0) or 0
        )
