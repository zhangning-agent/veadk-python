# Copyright (c) 2025 Beijing Volcano Engine Technology Co., Ltd. and/or its affiliates.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.

"""Verify TAE worker reclamation and wake-up for one Managed Session.

By default, send a VeADK message, verify the remote tool result and final reply,
wait for its TAE Tool Session to be reclaimed, then send another message using
the same VeADK and Managed Session. Use --mode tool for a model-free smoke test.
"""

import argparse
import asyncio
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any

EXAMPLE_DIR = Path(__file__).resolve().parent
if str(EXAMPLE_DIR) not in sys.path:
    sys.path.insert(0, str(EXAMPLE_DIR))

from sandbox_client import SelfHostSandboxClient

DEFAULT_TAE_ENDPOINT = "https://controlplane.cn-north.ai-sandbox-boe.byted.org"
TERMINAL_STATUSES = {
    "stopped",
    "deleted",
    "failed",
    "expired",
    "released",
    "terminated",
}


class TAESessionObserver:
    """Read-only TAE Session API observer authenticated as the current user."""

    def __init__(self, endpoint: str, sandbox_id: str, jwt_token: str) -> None:
        self.endpoint = endpoint.rstrip("/")
        self.sandbox_id = sandbox_id
        self.jwt_token = jwt_token

    def find_by_managed_session(self, managed_session_id: str) -> dict[str, Any] | None:
        data = self._request(
            "POST",
            "/sessions/search",
            {"metadata": {"user_session_id": managed_session_id}},
        )
        sessions = [
            session
            for session in self._sessions(data)
            if (session.get("metadata") or {}).get("user_session_id")
            == managed_session_id
        ]
        active = [
            s
            for s in sessions
            if str(s.get("status", "")).lower() not in TERMINAL_STATUSES
        ]
        if len(active) > 1:
            raise AssertionError(
                "Multiple active TAE instances for one Managed Session"
            )
        return next(iter(active or sessions), None)

    def _request(self, method: str, suffix: str, payload: dict[str, Any]) -> Any:
        sandbox_id = urllib.parse.quote(self.sandbox_id, safe="")
        request = urllib.request.Request(
            f"{self.endpoint}/api/v1/sandboxes/{sandbox_id}{suffix}",
            data=json.dumps(payload).encode() if payload else None,
            method=method,
            headers={"Content-Type": "application/json", "X-Jwt-Token": self.jwt_token},
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                decoded = json.loads(response.read().decode())
        except urllib.error.HTTPError as error:
            raise RuntimeError(
                f"TAE {method} {suffix} returned HTTP {error.code}"
            ) from error
        if decoded.get("code") not in (None, 0, 200):
            raise RuntimeError(
                f"TAE {method} {suffix} failed: code={decoded.get('code')} "
                f"message={decoded.get('message', '')}"
            )
        return decoded.get("data")

    @staticmethod
    def _sessions(data: Any) -> list[dict[str, Any]]:
        if isinstance(data, list):
            if not all(isinstance(item, dict) for item in data):
                raise RuntimeError(
                    "Invalid TAE session entry; cannot prove reclamation"
                )
            return data
        if isinstance(data, dict):
            for key in ("sessions", "items"):
                if isinstance(data.get(key), list):
                    return TAESessionObserver._sessions(data[key])
            if data.get("total") == 0:
                return []
        raise RuntimeError(
            "Unrecognized TAE session search response; cannot prove reclamation"
        )


def current_boe_jwt() -> str:
    """Use an explicit token first; otherwise reuse the local ByteCloud login."""
    for name in ("TAE_JWT_TOKEN", "BYTECLOUD_JWT_TOKEN", "JWT_TOKEN"):
        if token := os.getenv(name, "").strip():
            return token
    completed = subprocess.run(
        ["bytedcli", "--site", "boe", "--json", "auth", "get-bytecloud-jwt-token"],
        check=True,
        capture_output=True,
        text=True,
    )
    try:
        data = json.loads(completed.stdout)["data"]
        token = str(data.get("jwt") or data.get("token") or "")
    except (KeyError, TypeError, json.JSONDecodeError) as error:
        raise RuntimeError(
            "could not obtain a BOE ByteCloud JWT from bytedcli"
        ) from error
    if not token:
        raise RuntimeError("bytedcli returned an empty BOE ByteCloud JWT")
    return token


def wait_for_session(
    observer: TAESessionObserver,
    managed_session_id: str,
    *,
    present: bool,
    timeout: float,
    poll_interval: float,
) -> dict[str, Any] | None:
    """Wait for a TAE child session to appear or become terminal/deleted."""
    deadline = time.monotonic() + timeout
    last: dict[str, Any] | None = None
    while time.monotonic() < deadline:
        session = observer.find_by_managed_session(managed_session_id)
        if (
            present
            and session is not None
            and str(session.get("status", "")).lower() not in TERMINAL_STATUSES
        ):
            return session
        if not present and (
            session is None or str(session.get("status", "")).lower() == "deleted"
        ):
            return session
        last = session
        time.sleep(poll_interval)
    state = "appear" if present else "be reclaimed"
    details = (
        "not found"
        if last is None
        else f"id={last.get('session_id')} status={last.get('status')}"
    )
    raise TimeoutError(
        f"Timed out after {timeout:g}s waiting for TAE Session to {state}; last={details}"
    )


def run_lifecycle(args: argparse.Namespace) -> dict[str, Any]:
    client = SelfHostSandboxClient(timeout=max(1, int(args.command_timeout_seconds)))
    observer = TAESessionObserver(
        args.tae_endpoint, args.tae_sandbox_id, current_boe_jwt()
    )
    marker = uuid.uuid4().hex
    first_marker, second_marker = (
        f"tae-lifecycle-{marker}-first",
        f"tae-lifecycle-{marker}-second",
    )

    client.post_events(
        [
            {
                "type": "user.message",
                "content": [{"type": "text", "text": f"Execute: printf {first_marker}"}],
            }
        ]
    )
    first = client.execute_command(
        f"printf {first_marker}", timeout=args.command_timeout_seconds
    )
    if first.get("status") != "completed" or first_marker not in first.get(
        "stdout", ""
    ):
        raise RuntimeError(f"first dispatch failed: {first}")
    managed_session_id = client.session_id
    if not managed_session_id:
        raise RuntimeError("first dispatch did not produce a Managed Session ID")
    first_tae = wait_for_session(
        observer,
        managed_session_id,
        present=True,
        timeout=args.create_timeout_seconds,
        poll_interval=args.poll_interval_seconds,
    )
    first_tae_id = str(first_tae.get("session_id", ""))
    if not first_tae_id:
        raise RuntimeError(f"TAE Session is missing session_id: {first_tae}")

    client.post_status_idle()
    wait_for_session(
        observer,
        managed_session_id,
        present=False,
        timeout=args.reclaim_timeout_seconds,
        poll_interval=args.poll_interval_seconds,
    )

    client.post_events(
        [
            {
                "type": "user.message",
                "content": [{"type": "text", "text": f"Execute: printf {second_marker}"}],
            }
        ]
    )
    second = client.execute_command(
        f"printf {second_marker}", timeout=args.command_timeout_seconds
    )
    if second.get("status") != "completed" or second_marker not in second.get(
        "stdout", ""
    ):
        raise RuntimeError(f"second dispatch failed: {second}")
    second_tae = wait_for_session(
        observer,
        managed_session_id,
        present=True,
        timeout=args.create_timeout_seconds,
        poll_interval=args.poll_interval_seconds,
    )
    second_tae_id = str(second_tae.get("session_id", ""))
    if not second_tae_id or second_tae_id == first_tae_id:
        raise RuntimeError(
            f"second dispatch did not create a new TAE Tool Session: first={first_tae_id!r}, second={second_tae_id!r}"
        )
    client.post_status_idle()
    return {
        "managed_session_id": managed_session_id,
        "first_tae_session_id": first_tae_id,
        "second_tae_session_id": second_tae_id,
        "first_marker": first_marker,
        "second_marker": second_marker,
    }


def progress(stage: str, **details: Any) -> None:
    print(json.dumps({"stage": stage, **details}, ensure_ascii=False), flush=True)


def assert_tool_success(events: list[dict[str, Any]], marker: str) -> None:
    calls = {
        event.get("id")
        for event in events
        if event.get("type") == "agent.tool_use"
        and marker in json.dumps(event.get("input", {}))
    }
    for event in events:
        content = SelfHostSandboxClient._content_text(event.get("content"))
        if (
            event.get("type") in {"agent.tool_result", "user.tool_result"}
            and event.get("tool_use_id") in calls
            and not event.get("is_error")
            and marker in content
            and (not content.startswith("exit=") or content.startswith("exit=0\n"))
            and (
                not content.startswith("exit_code:")
                or content.startswith("exit_code: 0\n")
            )
        ):
            return
    raise AssertionError("No successful remote tool result matching this turn's marker")


def make_message_runner() -> tuple[Any, Any]:
    from agents.self_host_sandbox_agent.agent import (
        agent,
        enable_sandbox_turn_lifecycle,
        sandbox_sessions,
    )

    from veadk import Runner

    runner = enable_sandbox_turn_lifecycle(
        Runner(agent=agent, app_name="sandbox_lifecycle_test")
    )
    return runner, sandbox_sessions


async def run_message_lifecycle(args: argparse.Namespace) -> dict[str, Any]:
    """Send two real VeADK messages with observed TAE reclamation between them."""
    observer = TAESessionObserver(
        args.tae_endpoint, args.tae_sandbox_id, current_boe_jwt()
    )
    session_id = f"lifecycle-{uuid.uuid4().hex}"
    runner, sandbox_sessions = make_message_runner()
    sandbox_sessions.get(session_id).timeout = max(1, int(args.command_timeout_seconds))
    started = time.monotonic()
    result: dict[str, Any] = {
        "veadk_session_id": session_id,
        "tae_sandbox_id": args.tae_sandbox_id,
    }
    managed_id = None
    try:
        for turn in ("first", "second"):
            marker = f"{session_id}-{turn}"
            progress("sending", turn=turn, veadk_session_id=session_id)
            turn_started = time.monotonic()
            output = await asyncio.wait_for(
                runner.run(
                    messages=f"Use the bash tool with timeout={args.command_timeout_seconds:g} "
                    f"to execute exactly: printf {marker}. "
                    "You must actually execute the tool, then reply with its exact output.",
                    session_id=session_id,
                ),
                timeout=args.command_timeout_seconds,
            )
            client = sandbox_sessions.get(session_id)
            if not client.session_id or (
                managed_id and client.session_id != managed_id
            ):
                raise AssertionError(
                    "Managed Session changed between turns or was not created"
                )
            managed_id = client.session_id
            events = await asyncio.to_thread(
                client.list_events, limit=100, order="desc"
            )
            assert_tool_success(events, marker)
            if marker not in str(output):
                raise AssertionError(
                    f"{turn} final reply did not contain the tool output"
                )
            if not any(e.get("type") == "session.status_idle" for e in events):
                raise AssertionError("Turn did not publish session.status_idle")
            tae = await asyncio.to_thread(
                wait_for_session,
                observer,
                managed_id,
                present=True,
                timeout=args.create_timeout_seconds,
                poll_interval=args.poll_interval_seconds,
            )
            tae_id = str((tae or {}).get("session_id", ""))
            if not tae_id or (
                turn == "second" and tae_id == result["first_tae_session_id"]
            ):
                raise AssertionError("Expected a new TAE instance after reclamation")
            result.update(
                {
                    "managed_session_id": managed_id,
                    f"{turn}_tae_session_id": tae_id,
                    f"{turn}_reply": str(output),
                    f"{turn}_seconds": round(time.monotonic() - turn_started, 2),
                }
            )
            progress("turn_succeeded", turn=turn, **result)
            if turn == "first":
                reclaim_started = time.monotonic()
                progress("waiting_for_reclamation", tae_session_id=tae_id)
                await asyncio.to_thread(
                    wait_for_session,
                    observer,
                    managed_id,
                    present=False,
                    timeout=args.reclaim_timeout_seconds,
                    poll_interval=args.poll_interval_seconds,
                )
                result["reclaim_seconds"] = round(time.monotonic() - reclaim_started, 2)
                progress(
                    "reclaimed",
                    tae_session_id=tae_id,
                    seconds=result["reclaim_seconds"],
                )
        result["total_seconds"] = round(time.monotonic() - started, 2)
        result["status"] = "passed"
        return result
    except Exception as error:
        progress(
            "failed",
            **result,
            error_type=type(error).__name__,
            http_status=getattr(error, "status_code", None),
            elapsed_seconds=round(time.monotonic() - started, 2),
        )
        raise
    finally:
        sandbox_sessions.release(session_id)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--mode",
        choices=("message", "tool"),
        default="message",
        help="message: complete VeADK conversation; tool: direct dispatch smoke test",
    )
    parser.add_argument(
        "--tae-sandbox-id", default=os.getenv("TAE_SANDBOX_ID", "44nffoq7")
    )
    parser.add_argument(
        "--tae-endpoint", default=os.getenv("TAE_ENDPOINT", DEFAULT_TAE_ENDPOINT)
    )
    parser.add_argument("--command-timeout-seconds", type=float, default=600)
    parser.add_argument("--create-timeout-seconds", type=float, default=120)
    parser.add_argument("--reclaim-timeout-seconds", type=float, default=300)
    parser.add_argument("--poll-interval-seconds", type=float, default=5)
    args = parser.parse_args()
    for name in (
        "command_timeout_seconds",
        "create_timeout_seconds",
        "reclaim_timeout_seconds",
        "poll_interval_seconds",
    ):
        if getattr(args, name) <= 0:
            parser.error(f"--{name.replace('_', '-')} must be positive")
    return args


if __name__ == "__main__":
    args = parse_args()
    result = (
        asyncio.run(run_message_lifecycle(args))
        if args.mode == "message"
        else run_lifecycle(args)
    )
    print(json.dumps(result, ensure_ascii=False), flush=True)
