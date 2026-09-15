# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""The AgentCore host must supervise the persona daemon, not become it.

The adapter's job is an inversion: AgentCore is invocation-driven, the
persona is a daemon. These tests pin the properties that inversion has to
preserve — the daemon runs off the request loop, start is idempotent,
failures surface in a response instead of taking the server down, and health
reports busy while the persona is live.
"""

from __future__ import annotations

import asyncio
import threading
import time

import pytest

from agentstorming_agent.agentcore_app import AgentHost


class _FakeClient:
    def __init__(self) -> None:
        self.pid = "fake@room"
        self.posted: list[str] = []
        self.stopped = False

    async def post_message(self, text: str):
        self.posted.append(text)
        return {"seq": len(self.posted)}

    async def get_room_state(self):
        return {"room_id": "demo", "moderator_pid": "someone-else"}

    async def stop(self):
        self.stopped = True


class _FakeAgent:
    """Stands in for NativeAgent: runs until asked to stop."""

    def __init__(self, persona_dir, *, crash: bool = False) -> None:
        self.persona_dir = persona_dir
        self.client = _FakeClient()
        self.broker = None
        self._crash = crash
        self._stop = asyncio.Event()
        self.started = threading.Event()
        self.turns_taken = 0
        self.messages_posted = 0

    def request_stop(self) -> None:
        self._stop.set()

    def status(self) -> dict:
        return {"persona": "fake", "running": not self._stop.is_set(),
                "turns_taken": self.turns_taken}

    async def run(self) -> int:
        self.started.set()
        if self._crash:
            raise RuntimeError("backend exploded")
        await self._stop.wait()
        await self.client.stop()
        return 0


@pytest.fixture
def host(tmp_path, monkeypatch):
    agents: list[_FakeAgent] = []

    def _factory(persona_dir):
        a = _FakeAgent(persona_dir)
        agents.append(a)
        return a

    monkeypatch.setattr("agentstorming_agent.loop.NativeAgent", _factory)
    h = AgentHost(tmp_path)
    h._agents = agents  # type: ignore[attr-defined]
    yield h
    h.stop(timeout=5)


def _wait(pred, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return True
        time.sleep(0.02)
    return False


def test_start_launches_the_daemon_off_the_request_thread(host):
    caller = threading.get_ident()
    out = host.start()
    assert out["started"] is True
    assert _wait(lambda: host.busy)
    agent = host._agents[0]  # type: ignore[attr-defined]
    assert agent.started.wait(timeout=5)
    # The daemon must not be running on the thread that handled the call.
    assert host._thread is not None
    assert host._thread.ident != caller


def test_start_is_idempotent(host):
    assert host.start()["started"] is True
    assert _wait(lambda: host.busy)
    second = host.start()
    assert second["started"] is False
    assert second["reason"] == "already_running"
    assert len(host._agents) == 1  # type: ignore[attr-defined]


def test_status_before_start_does_not_raise(host):
    st = host.status()
    assert st["thread_alive"] is False
    assert st["error"] is None


def test_stop_leaves_the_room_cleanly(host):
    host.start()
    assert _wait(lambda: host.busy)
    agent = host._agents[0]  # type: ignore[attr-defined]
    started = time.monotonic()
    out = host.stop(timeout=5)
    elapsed = time.monotonic() - started
    assert out["stopped"] is True
    assert out["exit_code"] == 0
    assert agent.client.stopped is True
    assert host.busy is False
    # Stop must be prompt. request_stop() sets Events owned by the daemon's
    # loop, so it has to be marshalled onto that loop; calling it from this
    # thread does not wake the waiter and the stop stalls until the loop's
    # next timeout.
    assert elapsed < 2.0, f"stop took {elapsed:.1f}s — not marshalled to the loop"


def test_say_posts_through_the_running_client(host):
    host.start()
    assert _wait(lambda: host.busy)
    out = host.say("a message from the operator")
    assert out["posted"] is True
    agent = host._agents[0]  # type: ignore[attr-defined]
    assert agent.client.posted == ["a message from the operator"]


def test_say_before_start_is_an_error_not_a_crash(host):
    with pytest.raises(RuntimeError, match="not running"):
        host.say("hello")


def test_say_rejects_empty_text(host):
    host.start()
    assert _wait(lambda: host.busy)
    with pytest.raises(ValueError):
        host.say("")


def test_room_returns_the_state_snapshot(host):
    host.start()
    assert _wait(lambda: host.busy)
    assert host.room()["room_id"] == "demo"


def test_a_crashing_persona_is_reported_not_swallowed(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "agentstorming_agent.loop.NativeAgent",
        lambda persona_dir: _FakeAgent(persona_dir, crash=True),
    )
    h = AgentHost(tmp_path)
    h.start()
    assert _wait(lambda: not h.busy)
    st = h.status()
    assert st["error"] is not None
    assert "backend exploded" in st["error"]


def test_construction_failure_is_returned_to_the_caller(tmp_path, monkeypatch):
    def _boom(persona_dir):
        raise FileNotFoundError("persona.yaml missing")

    monkeypatch.setattr("agentstorming_agent.loop.NativeAgent", _boom)
    h = AgentHost(tmp_path)
    out = h.start()
    assert out["started"] is False
    assert "persona.yaml missing" in out["error"]


def test_persona_dir_must_contain_persona_yaml(tmp_path, monkeypatch):
    from agentstorming_agent import agentcore_app

    monkeypatch.setenv("AGENTSTORMING_PERSONA_DIR", str(tmp_path))
    with pytest.raises(RuntimeError, match="persona.yaml not found"):
        agentcore_app._persona_dir()

    (tmp_path / "persona.yaml").write_text("name: x\n")
    assert agentcore_app._persona_dir() == tmp_path


def test_persona_dir_env_is_required(monkeypatch):
    from agentstorming_agent import agentcore_app

    monkeypatch.delenv("AGENTSTORMING_PERSONA_DIR", raising=False)
    with pytest.raises(RuntimeError, match="AGENTSTORMING_PERSONA_DIR"):
        agentcore_app._persona_dir()
