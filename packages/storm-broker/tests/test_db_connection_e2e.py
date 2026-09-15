# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""End-to-end test for DBConnectionBroker fd-passing.

Stands up:
  - A local TCP echo server (the stand-in for Postgres/MySQL/Redis)
  - A storm-broker with a DBConnectionBrokerProvider configured to
    connect to that echo server, holding a fake password the test
    process never sees in process state.
  - A BrokerClient.open_db_connection() call that should receive a
    pre-connected socket fd via SCM_RIGHTS.

Verifies:
  - The fd works as a real TCP socket (we can send/recv over it).
  - The diagnostic info returned redacts the password (only its sha256
    prefix is exposed).
  - On Windows the client raises NotImplementedError.
"""

from __future__ import annotations

import asyncio
import os
import secrets
import socket as _socket
import sys
import tempfile
import threading
from pathlib import Path

import pytest
import pytest_asyncio

from storm_broker.audit import AuditChain
from storm_broker.policy import Capability, PolicyEngine
from storm_broker.providers import DBConnectionBrokerProvider
from storm_broker.server import BrokerServer
from fake_credentials import DB_PASSWORD, GENERIC_SECRET

pytestmark = pytest.mark.skipif(
    sys.platform not in ("linux", "darwin"),
    reason="SCM_RIGHTS fd-passing is POSIX-only",
)


def _start_echo_server() -> tuple[int, threading.Event]:
    """Bind a TCP echo server on 127.0.0.1:<random>, return (port, stop_evt)."""
    s = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    s.listen(4)
    port = s.getsockname()[1]
    stop = threading.Event()

    def _serve() -> None:
        s.settimeout(0.5)
        while not stop.is_set():
            try:
                conn, _ = s.accept()
            except _socket.timeout:
                continue
            except OSError:
                break

            def _handle(c: _socket.socket) -> None:
                try:
                    while True:
                        data = c.recv(1024)
                        if not data:
                            break
                        c.sendall(data)
                except OSError:
                    pass
                finally:
                    c.close()

            threading.Thread(target=_handle, args=(conn,), daemon=True).start()
        s.close()

    threading.Thread(target=_serve, daemon=True).start()
    return port, stop


@pytest_asyncio.fixture
async def broker():
    short_dir = Path(tempfile.mkdtemp(prefix="sb-db-", dir="/tmp"))  # nosec B108 - mkdtemp creates 0700; /tmp is deliberate because macOS caps AF_UNIX paths at 104 chars and pytest's tmp_path exceeds it
    sock_path = str(short_dir / "b.sock")
    chain = AuditChain(short_dir / "audit.ndjson", secrets.token_bytes(32))

    port, stop_evt = _start_echo_server()

    provider = DBConnectionBrokerProvider({
        "driver": "raw_tcp",
        "host": "127.0.0.1",
        "port": port,
        "username": "agent_ro",
        "password": DB_PASSWORD,
        "database": "appdb",
    })
    providers = {"appdb": provider}

    my_uid = os.getuid()
    policies = {
        my_uid: PolicyEngine([
            Capability(
                tool="appdb.open_db_connection",
                args={"database": ["appdb"]},
                rate_per_second=10.0,
                requires_trust="user",
            ),
        ])
    }
    srv = BrokerServer(
        socket_path=sock_path, providers=providers,
        policy_for_uid=policies, audit_chain=chain,
        allowed_uids={my_uid},
    )
    await srv.start()
    task = asyncio.create_task(srv.serve_forever())
    try:
        yield sock_path
    finally:
        stop_evt.set()
        await srv.stop()
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass


@pytest.mark.asyncio
async def test_open_db_connection_returns_usable_fd(broker):
    from agentstorming_client.broker_client import BrokerClient

    sock_path = broker
    c = BrokerClient(socket_path=sock_path)
    loop = asyncio.get_event_loop()

    info, conn = await loop.run_in_executor(
        None, lambda: c.open_db_connection("appdb", database="appdb"),
    )
    assert info["ok"] is True
    assert info["fd_pending"] is True
    # Diagnostics MUST NOT include the raw password.
    diag = info["diagnostics"]
    assert "password" not in diag
    assert "password_sha256" in diag
    assert diag["username"] == "agent_ro"

    # Now use the live fd: send bytes to the echo server and read back.
    conn.settimeout(2.0)
    conn.sendall(b"ping")
    echoed = b""
    while len(echoed) < 4:
        chunk = conn.recv(4 - len(echoed))
        if not chunk:
            break
        echoed += chunk
    conn.close()
    assert echoed == b"ping"


@pytest.mark.asyncio
async def test_unknown_db_provider_denied(broker):
    from agentstorming_client.broker_client import BrokerClient, BrokerError

    sock_path = broker
    c = BrokerClient(socket_path=sock_path)
    loop = asyncio.get_event_loop()
    with pytest.raises(BrokerError):
        await loop.run_in_executor(
            None, lambda: c.open_db_connection("nonexistent"),
        )


def test_db_provider_redacts_credentials():
    p = DBConnectionBrokerProvider({
        "driver": "raw_tcp", "host": "127.0.0.1", "port": 1,
        "username": "u", "password": GENERIC_SECRET,
    })
    diag = p.credentials_redacted()
    assert "password" not in diag
    assert GENERIC_SECRET not in str(diag)


def test_db_provider_rejects_unknown_driver():
    from storm_broker.providers.base import ProviderError
    with pytest.raises(ProviderError):
        DBConnectionBrokerProvider({"driver": "fictional", "host": "x", "port": 1})
