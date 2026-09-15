# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""End-to-end test: agent calls broker.sign() and verifies signature."""

from __future__ import annotations

import asyncio
import base64
import os
import secrets
import sys
import tempfile
from pathlib import Path

import pytest
import pytest_asyncio

from storm_broker.audit import AuditChain
from storm_broker.policy import Capability, PolicyEngine
from storm_broker.providers import KeyHandleProvider
from storm_broker.server import BrokerServer

pytestmark = pytest.mark.skipif(
    sys.platform not in ("linux", "darwin"),
    reason="SO_PEERCRED / LOCAL_PEERCRED only available on Linux + macOS",
)


def _gen_ed25519_pem() -> bytes:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ed25519
    k = ed25519.Ed25519PrivateKey.generate()
    return k.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )


@pytest_asyncio.fixture
async def broker():
    short_dir = Path(tempfile.mkdtemp(prefix="sb-sign-", dir="/tmp"))  # nosec B108 - mkdtemp creates 0700; /tmp is deliberate because macOS caps AF_UNIX paths at 104 chars and pytest's tmp_path exceeds it
    sock_path = str(short_dir / "b.sock")
    chain = AuditChain(short_dir / "audit.ndjson", secrets.token_bytes(32))

    pem = _gen_ed25519_pem()
    handle = KeyHandleProvider({"key_pem": pem, "key_id": "test-key-1"})
    providers = {"signer": handle}

    my_uid = os.getuid()
    policies = {
        my_uid: PolicyEngine([
            Capability(
                tool="signer.sign",
                args={"kid": ["test-key-1"]},
                rate_per_second=100.0,
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
        yield sock_path, handle
    finally:
        await srv.stop()
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass


@pytest.mark.asyncio
async def test_sign_via_broker_and_verify(broker):
    from agentstorming_client.broker_client import BrokerClient
    from cryptography.hazmat.primitives import serialization

    sock_path, handle = broker
    c = BrokerClient(socket_path=sock_path)
    loop = asyncio.get_event_loop()

    payload = b"please sign this"
    res = await loop.run_in_executor(
        None, lambda: c.sign("signer", payload),
    )
    assert res["alg"] == "ed25519"
    assert res["kid"] == "test-key-1"

    # Verify with the provider's public key (published, not private).
    raw = base64.b64decode(res["sig_b64"])
    pub = serialization.load_pem_public_key(handle.public_key_pem().encode())
    pub.verify(raw, payload)
