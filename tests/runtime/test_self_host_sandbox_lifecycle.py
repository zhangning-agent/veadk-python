import asyncio
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

LIFECYCLE_PATH = (
    Path(__file__).parents[2]
    / "examples"
    / "16_self_host_sandbox"
    / "lifecycle_test.py"
)
SPEC = importlib.util.spec_from_file_location(
    "self_host_sandbox_lifecycle", LIFECYCLE_PATH
)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_wait_for_session_observes_created_and_reclaimed(monkeypatch):
    observer = SimpleNamespace()
    responses = iter(
        [
            None,
            {"session_id": "tae-first", "status": "running"},
            {"session_id": "tae-first", "status": "running"},
            None,
        ]
    )
    observer.find_by_managed_session = lambda _: next(responses)
    monkeypatch.setattr(MODULE.time, "sleep", lambda _: None)

    created = MODULE.wait_for_session(
        observer, "managed-1", present=True, timeout=1, poll_interval=0.01
    )
    reclaimed = MODULE.wait_for_session(
        observer, "managed-1", present=False, timeout=1, poll_interval=0.01
    )

    assert created == {"session_id": "tae-first", "status": "running"}
    assert reclaimed is None


def test_wait_for_session_treats_terminal_status_as_reclaimed():
    observer = SimpleNamespace(
        find_by_managed_session=lambda _: {
            "session_id": "tae-first",
            "status": "deleted",
        }
    )
    assert MODULE.wait_for_session(
        observer, "managed-1", present=False, timeout=1, poll_interval=0.01
    ) == {"session_id": "tae-first", "status": "deleted"}


def test_run_lifecycle_reuses_managed_session_after_reclamation(monkeypatch):
    calls = []

    class Client:
        def __init__(self, **kwargs):
            self.session_id = "managed-1"

        def post_events(self, events):
            assert events[0]["type"] == "user.message"
            calls.append(("user.message", events[0]["content"][0]["text"]))

        def execute_command(self, command, **kwargs):
            calls.append(("execute", command))
            return {"status": "completed", "stdout": command.removeprefix("printf ")}

        def post_status_idle(self):
            calls.append(("idle", self.session_id))

    sessions = iter(
        [
            {"session_id": "tae-first", "status": "running"},
            None,
            {"session_id": "tae-second", "status": "running"},
        ]
    )
    monkeypatch.setattr(MODULE, "SelfHostSandboxClient", Client)
    monkeypatch.setattr(MODULE, "current_boe_jwt", lambda: "jwt")
    monkeypatch.setattr(
        MODULE.TAESessionObserver,
        "find_by_managed_session",
        lambda *_: next(sessions),
    )
    args = SimpleNamespace(
        command_timeout_seconds=1,
        tae_endpoint="https://tae.example",
        tae_sandbox_id="44nffoq7",
        create_timeout_seconds=1,
        reclaim_timeout_seconds=1,
        poll_interval_seconds=0.01,
    )

    result = MODULE.run_lifecycle(args)

    assert result["managed_session_id"] == "managed-1"
    assert result["first_tae_session_id"] == "tae-first"
    assert result["second_tae_session_id"] == "tae-second"
    assert [kind for kind, _ in calls] == ["user.message", "execute", "idle", "user.message", "execute", "idle"]
    assert result["first_marker"] in calls[0][1]
    assert result["second_marker"] in calls[3][1]


def test_current_boe_jwt_rejects_malformed_cli_output(monkeypatch):
    monkeypatch.delenv("TAE_JWT_TOKEN", raising=False)
    monkeypatch.delenv("BYTECLOUD_JWT_TOKEN", raising=False)
    monkeypatch.delenv("JWT_TOKEN", raising=False)
    monkeypatch.setattr(
        MODULE.subprocess,
        "run",
        lambda *_, **__: SimpleNamespace(stdout='{"data": {}}'),
    )
    with pytest.raises(RuntimeError, match="empty BOE ByteCloud JWT"):
        MODULE.current_boe_jwt()


def test_current_boe_jwt_reads_bytedcli_jwt_field(monkeypatch):
    monkeypatch.delenv("TAE_JWT_TOKEN", raising=False)
    monkeypatch.delenv("BYTECLOUD_JWT_TOKEN", raising=False)
    monkeypatch.delenv("JWT_TOKEN", raising=False)
    monkeypatch.setattr(
        MODULE.subprocess,
        "run",
        lambda *_, **__: SimpleNamespace(stdout='{"data": {"jwt": "cli-jwt"}}'),
    )
    assert MODULE.current_boe_jwt() == "cli-jwt"


@pytest.mark.parametrize("data", [None, {}, {"unexpected": []}, [None]])
def test_invalid_search_response_cannot_prove_reclamation(data):
    with pytest.raises(RuntimeError, match="cannot prove reclamation"):
        MODULE.TAESessionObserver._sessions(data)


def test_search_accepts_explicit_zero_total():
    assert MODULE.TAESessionObserver._sessions({"total": 0}) == []


def test_search_prefers_active_instance_over_deleted_history(monkeypatch):
    observer = MODULE.TAESessionObserver("https://tae.example", "44nffoq7", "jwt")
    metadata = {"user_session_id": "managed-1"}
    records = [
        {"session_id": "old", "status": "deleted", "metadata": metadata},
        {"session_id": "new", "status": "Ready", "metadata": metadata},
    ]
    monkeypatch.setattr(observer, "_request", lambda *_: records)
    assert observer.find_by_managed_session("managed-1")["session_id"] == "new"
    records[0]["status"] = "Ready"
    with pytest.raises(AssertionError, match="Multiple active"):
        observer.find_by_managed_session("managed-1")


def test_tool_assertion_rejects_echo_and_unrelated_result():
    events = [
        {
            "type": "agent.tool_use",
            "id": "tool-1",
            "input": {"command": "printf marker"},
        },
        {"type": "user.tool_result", "tool_use_id": "other", "content": "marker"},
        {"type": "agent.message", "content": "marker"},
    ]
    with pytest.raises(AssertionError):
        MODULE.assert_tool_success(events, "marker")
    events[1]["tool_use_id"] = "tool-1"
    events[1]["is_error"] = True
    with pytest.raises(AssertionError):
        MODULE.assert_tool_success(events, "marker")
    events[1]["is_error"] = False
    MODULE.assert_tool_success(events, "marker")
    for content in ("exit=1\nmarker", "exit_code: 1\nmarker"):
        events[1]["content"] = content
        with pytest.raises(AssertionError):
            MODULE.assert_tool_success(events, "marker")


@pytest.mark.parametrize(
    "changed_managed,reused_tae", [(False, False), (True, False), (False, True)]
)
def test_message_lifecycle_checks_both_session_identities(
    monkeypatch, changed_managed, reused_tae
):
    calls = []
    client = SimpleNamespace(session_id="managed-1")

    async def run(*, messages, session_id):
        calls.append(session_id)
        if len(calls) == 2 and changed_managed:
            client.session_id = "managed-2"
        marker = messages.split("printf ", 1)[1].split(".", 1)[0]
        client.list_events = lambda **_: [
            {
                "type": "agent.tool_use",
                "id": marker,
                "input": {"command": f"printf {marker}"},
            },
            {"type": "user.tool_result", "tool_use_id": marker, "content": marker},
            {"type": "session.status_idle"},
        ]
        return marker

    released = []
    manager = SimpleNamespace(get=lambda _: client, release=released.append)
    monkeypatch.setattr(
        MODULE, "make_message_runner", lambda: (SimpleNamespace(run=run), manager)
    )
    monkeypatch.setattr(MODULE, "current_boe_jwt", lambda: "jwt")
    sessions = iter(
        [
            {"session_id": "tae-first", "status": "running"},
            None,
            {
                "session_id": "tae-first" if reused_tae else "tae-second",
                "status": "running",
            },
        ]
    )
    monkeypatch.setattr(
        MODULE.TAESessionObserver, "find_by_managed_session", lambda *_: next(sessions)
    )
    args = SimpleNamespace(
        tae_endpoint="https://tae.example",
        tae_sandbox_id="44nffoq7",
        command_timeout_seconds=1,
        create_timeout_seconds=1,
        reclaim_timeout_seconds=1,
        poll_interval_seconds=0.01,
    )
    if changed_managed or reused_tae:
        with pytest.raises(AssertionError):
            asyncio.run(MODULE.run_message_lifecycle(args))
    else:
        result = asyncio.run(MODULE.run_message_lifecycle(args))
        assert result["status"] == "passed"
        assert result["managed_session_id"] == "managed-1"
        assert result["first_tae_session_id"] != result["second_tae_session_id"]
    assert len(calls) == 2 and calls[0] == calls[1]
    assert released == [calls[0]]
