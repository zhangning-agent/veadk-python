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

"""Run the VeADK agent with remotely dispatched sandbox tools."""

import argparse
import asyncio
import signal
import uuid

from agents.self_host_sandbox_agent.agent import agent
from veadk import Runner
from veadk.extensions import FeishuChannelExtension


APP_NAME = "self_host_sandbox_demo"


async def serve_feishu_channel(stop_event: asyncio.Event | None = None) -> None:
    """Serve Feishu conversations until the process receives a stop signal."""
    runner = Runner(agent=agent, app_name=APP_NAME)
    channel = FeishuChannelExtension(
        runner=runner,
        streaming=True,
        show_thinking=True,
        show_tool_calls=True,
        show_tool_results=True,
    )
    loop = asyncio.get_running_loop()
    shutdown_event = stop_event or asyncio.Event()

    if stop_event is None:
        for signal_number in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(signal_number, shutdown_event.set)
            except NotImplementedError:  # pragma: no cover - Windows fallback
                signal.signal(signal_number, lambda *_: shutdown_event.set())

    channel.start(loop)
    print("Feishu Channel is running. Press Ctrl+C to stop.")
    try:
        await shutdown_event.wait()
    finally:
        await channel.shutdown()


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--prompt",
        default=(
            "Use the bash tool to run: printf 'veadk-self-host-ok'. "
            "Then reply with the exact output."
        ),
    )
    parser.add_argument("--session-id", default=None)
    parser.add_argument(
        "--feishu",
        action="store_true",
        help="Keep running and serve conversations through the Feishu bot channel.",
    )
    args = parser.parse_args()

    if args.feishu:
        await serve_feishu_channel()
        return

    session_id = args.session_id or f"veadk-{uuid.uuid4()}"
    runner = Runner(agent=agent, app_name=APP_NAME)
    output = await runner.run(messages=args.prompt, session_id=session_id)
    print(output)


if __name__ == "__main__":
    asyncio.run(main())
