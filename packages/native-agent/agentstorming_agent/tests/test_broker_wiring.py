# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Tests for broker wiring on the native agent.

Verifies that:
  - The agent runs broker-less by default (back-compat).
  - When broker.socket_path is set but unreachable and broker.required is
    False, the agent logs a warning and continues.
  - When broker.socket_path is set, broker.required is True, and the
    socket is unreachable, the agent fails fast.
"""

from __future__ import annotations

import json
import os
import socket
import struct
import threading
import uuid
from pathlib import Path

import pytest
import yaml

from agentstorming_agent.config import BrokerConfig, PersonaConfig, load_persona
from agentstorming_agent.loop import NativeAgent


def _write_persona(tmp_path: Path, broker: dict | None = None) -> Path:
    persona_dir = tmp_path / "p"
    persona_dir.mkdir()
    cfg = {
        "name": "tester",
        "display_name": "Tester",
        "model": "scripted/echo",
        "backend": "scripted",
        "room_url": "http://localhost:1",
        "room_id": "00000000-0000-0000-0000-000000000000",
    }
    if broker is not None:
        cfg["broker"] = broker
    (persona_dir / "persona.yaml").write_text(yaml.safe_dump(cfg))
    (persona_dir / "persona.md").write_text("Test persona.")
    return persona_dir


def test_default_persona_has_no_broker_socket(tmp_path: Path) -> None:
    persona_dir = _write_persona(tmp_path)
    cfg, _ = load_persona(persona_dir)
    assert cfg.broker.socket_path is None
    assert cfg.broker.required is False


def test_persona_loads_broker_block(tmp_path: Path) -> None:
    persona_dir = _write_persona(tmp_path, broker={
        "socket_path": "/tmp/does-not-exist-xyz.sock",  # nosec B108 - mkdtemp creates 0700; /tmp is deliberate because macOS caps AF_UNIX paths at 104 chars and pytest's tmp_path exceeds it
        "required": False,
        "timeout_s": 5.0,
    })
    cfg, _ = load_persona(persona_dir)
    assert cfg.broker.socket_path == "/tmp/does-not-exist-xyz.sock"  # nosec B108 - mkdtemp creates 0700; /tmp is deliberate because macOS caps AF_UNIX paths at 104 chars and pytest's tmp_path exceeds it
    assert cfg.broker.timeout_s == 5.0


def test_agent_runs_brokerless_by_default(tmp_path: Path) -> None:
    persona_dir = _write_persona(tmp_path)
    a = NativeAgent(persona_dir)
    assert a.broker is None


def test_agent_warns_when_broker_unreachable_and_optional(tmp_path: Path, caplog) -> None:
    sock = tmp_path / "no-such.sock"
    persona_dir = _write_persona(tmp_path, broker={
        "socket_path": str(sock),
        "required": False,
    })
    with caplog.at_level("WARNING"):
        a = NativeAgent(persona_dir)
    assert a.broker is None
    assert any("broker configured" in r.message for r in caplog.records)


def test_agent_fails_fast_when_broker_required_and_unreachable(tmp_path: Path) -> None:
    sock = tmp_path / "no-such.sock"
    persona_dir = _write_persona(tmp_path, broker={
        "socket_path": str(sock),
        "required": True,
    })
    with pytest.raises(RuntimeError, match="broker required but unreachable"):
        NativeAgent(persona_dir)


def test_agent_connects_to_running_broker(tmp_path: Path) -> None:
    """End-to-end: spin up a tiny socket that answers JSON-RPC ping and
    verify the agent's BrokerClient handshake works."""
    # macOS AF_UNIX has 104-char sun_path; use /tmp.
    import tempfile
    sock_dir = Path(tempfile.mkdtemp(prefix="sb-test-", dir="/tmp"))  # nosec B108 - mkdtemp creates 0700; /tmp is deliberate because macOS caps AF_UNIX paths at 104 chars and pytest's tmp_path exceeds it
    sock_path = sock_dir / "broker.sock"

    def _serve() -> None:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.bind(str(sock_path))
        s.listen(1)
        s.settimeout(3.0)
        try:
            conn, _ = s.accept()
        except socket.timeout:
            return
        try:
            hdr = conn.recv(4)
            (n,) = struct.unpack(">I", hdr)
            buf = b""
            while len(buf) < n:
                buf += conn.recv(n - len(buf))
            req = json.loads(buf.decode("utf-8"))
            resp = {
                "jsonrpc": "2.0", "id": req["id"],
                "result": {"ok": True, "version": "test"},
            }
            body = json.dumps(resp).encode("utf-8")
            conn.sendall(struct.pack(">I", len(body)) + body)
        finally:
            conn.close()
            s.close()

    t = threading.Thread(target=_serve, daemon=True)
    t.start()

    persona_dir = _write_persona(tmp_path, broker={
        "socket_path": str(sock_path), "required": True,
    })
    a = NativeAgent(persona_dir)
    assert a.broker is not None
    t.join(timeout=2.0)
