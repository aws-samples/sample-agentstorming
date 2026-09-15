# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""End-to-end test: start a real broker on a tmp socket, connect via
the Python broker_client, verify the SO_PEERCRED gate + JSON-RPC flow."""

from __future__ import annotations

import asyncio
import os
import secrets
import socket
import sys
import threading
from pathlib import Path

import pytest
import pytest_asyncio

from storm_broker.audit import AuditChain
from storm_broker.dlp import DLPScanner
from storm_broker.policy import Capability, PolicyEngine
from storm_broker.providers import BearerTokenProvider
from storm_broker.server import BrokerServer
from fake_credentials import GITHUB_TOKEN

# Skip tests on platforms without peercred support.
pytestmark = pytest.mark.skipif(
    sys.platform not in ("linux", "darwin"),
    reason="SO_PEERCRED / LOCAL_PEERCRED only available on Linux + macOS",
)


@pytest_asyncio.fixture
async def broker(tmp_path: Path):
    # macOS AF_UNIX sun_path limit is 104 chars; pytest tmp_path can exceed.
    import tempfile
    short_dir = Path(tempfile.mkdtemp(prefix="sb-", dir="/tmp"))  # nosec B108 - mkdtemp creates 0700; /tmp is deliberate because macOS caps AF_UNIX paths at 104 chars and pytest's tmp_path exceeds it
    sock_path = str(short_dir / "b.sock")
    audit_path = short_dir / "audit.ndjson"
    chain = AuditChain(audit_path, secrets.token_bytes(32))
    providers = {
        "github": BearerTokenProvider({
            "token": GITHUB_TOKEN,
            "host_allowlist": ["api.github.com"],
        }),
    }
    # On test runs, the test process *is* the storm-agent — so
    # allow our own uid.
    my_uid = os.getuid()
    policies = {
        my_uid: PolicyEngine([
            Capability(tool="github.request",
                       args={"url": ["https://api.github.com/*"]},
                       rate_per_second=100.0,
                       requires_trust="user"),
        ])
    }
    srv = BrokerServer(
        socket_path=sock_path,
        providers=providers,
        policy_for_uid=policies,
        audit_chain=chain,
        allowed_uids={my_uid},
    )
    await srv.start()
    task = asyncio.create_task(srv.serve_forever())
    try:
        yield sock_path
    finally:
        await srv.stop()
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass


def _client_call_in_thread(sock_path: str, payload: bytes) -> bytes:
    """Run a synchronous client call in a thread (peercred only works on the
    real socket fd, so we use the BrokerClient class)."""
    import struct
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(5.0)
    s.connect(sock_path)
    s.sendall(struct.pack(">I", len(payload)) + payload)
    hdr = b""
    while len(hdr) < 4:
        c = s.recv(4 - len(hdr))
        if not c:
            break
        hdr += c
    n = struct.unpack(">I", hdr)[0]
    body = b""
    while len(body) < n:
        c = s.recv(n - len(body))
        if not c:
            break
        body += c
    s.close()
    return body


@pytest.mark.asyncio
async def test_ping(broker):
    from agentstorming_client.broker_client import BrokerClient
    c = BrokerClient(socket_path=broker)
    # synchronous call from a thread (BrokerClient is sync)
    loop = asyncio.get_event_loop()
    res = await loop.run_in_executor(None, c.ping)
    assert res["ok"] is True


@pytest.mark.asyncio
async def test_prepare_headers_allowed(broker):
    from agentstorming_client.broker_client import BrokerClient
    c = BrokerClient(socket_path=broker)
    loop = asyncio.get_event_loop()
    res = await loop.run_in_executor(
        None, lambda: c.prepare_headers(
            provider="github", target="github",
            method="GET", url="https://api.github.com/repos/x/y",
        )
    )
    assert res["headers"] == {"Authorization": f"Bearer {GITHUB_TOKEN}"}


@pytest.mark.asyncio
async def test_prepare_headers_blocked_by_host_allowlist(broker):
    from agentstorming_client.broker_client import BrokerClient, BrokerError
    c = BrokerClient(socket_path=broker)
    loop = asyncio.get_event_loop()
    # The capability rule requires args.url glob api.github.com/*; a
    # non-matching URL should be denied at policy layer.
    with pytest.raises(BrokerError):
        await loop.run_in_executor(
            None, lambda: c.prepare_headers(
                provider="github", method="GET",
                url="https://api.attacker.com/leak",
            )
        )


@pytest.mark.asyncio
async def test_prepare_headers_provider_unknown(broker):
    from agentstorming_client.broker_client import BrokerClient, BrokerError
    c = BrokerClient(socket_path=broker)
    loop = asyncio.get_event_loop()
    with pytest.raises(BrokerError):
        await loop.run_in_executor(
            None, lambda: c.prepare_headers(provider="nonexistent", url="https://x"),
        )
