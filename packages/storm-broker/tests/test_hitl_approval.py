# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""End-to-end HITL approval flow.

Verifies:
  - A capability with ask_owner_at_usd surfaces approval_required to the
    agent rather than silently allowing or denying.
  - The agent sees a structured approval_id + estimated_cost_usd in the
    error data and can resubmit after the owner records approval via
    record_approval.

This test uses Stripe because a payment API is the clearest example of a
credential worth keeping out of the model context. It is illustrative only: no
cardholder data is involved and nothing here is a PCI-DSS control. A deployer
who follows this pattern to process payment card data takes on PCI-DSS scope for
their own environment — see the note in storm_broker/providers/stripe.py.
"""

from __future__ import annotations

import asyncio
import os
import secrets
import sys
import tempfile
from pathlib import Path

import pytest
import pytest_asyncio

from storm_broker.audit import AuditChain
from storm_broker.policy import Capability, PolicyEngine
from storm_broker.providers import BearerTokenProvider
from storm_broker.server import BrokerServer
from fake_credentials import STRIPE_RESTRICTED

pytestmark = pytest.mark.skipif(
    sys.platform not in ("linux", "darwin"),
    reason="SO_PEERCRED / LOCAL_PEERCRED only available on Linux + macOS",
)


@pytest_asyncio.fixture
async def broker():
    short_dir = Path(tempfile.mkdtemp(prefix="sb-hitl-", dir="/tmp"))  # nosec B108 - mkdtemp creates 0700; /tmp is deliberate because macOS caps AF_UNIX paths at 104 chars and pytest's tmp_path exceeds it
    sock_path = str(short_dir / "b.sock")
    chain = AuditChain(short_dir / "audit.ndjson", secrets.token_bytes(32))
    providers = {
        "stripe": BearerTokenProvider({
            "token": STRIPE_RESTRICTED,
            "host_allowlist": ["api.stripe.com"],
        }),
    }
    my_uid = os.getuid()
    # 5 USD trigger; calls below the threshold pass, above need approval.
    policies = {
        my_uid: PolicyEngine([
            Capability(
                tool="stripe.request",
                args={"url": ["https://api.stripe.com/*"]},
                rate_per_second=100.0,
                requires_trust="user",
                ask_owner_at_usd=5.0,
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
        yield sock_path, srv
    finally:
        await srv.stop()
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass


@pytest.mark.asyncio
async def test_under_threshold_passes(broker):
    from agentstorming_client.broker_client import BrokerClient
    sock_path, _ = broker
    c = BrokerClient(socket_path=sock_path)
    loop = asyncio.get_event_loop()
    res = await loop.run_in_executor(
        None, lambda: c._call("prepare_headers", {
            "provider": "stripe", "target": "stripe",
            "method": "POST", "url": "https://api.stripe.com/v1/charges",
            "estimated_cost_usd": 1.0, "trust": "user",
        }),
    )
    assert "headers" in res


@pytest.mark.asyncio
async def test_above_threshold_returns_approval_required(broker):
    from agentstorming_client.broker_client import BrokerClient, BrokerError
    sock_path, _ = broker
    c = BrokerClient(socket_path=sock_path)
    loop = asyncio.get_event_loop()
    with pytest.raises(BrokerError) as exc_info:
        await loop.run_in_executor(
            None, lambda: c._call("prepare_headers", {
                "provider": "stripe", "target": "stripe",
                "method": "POST", "url": "https://api.stripe.com/v1/charges",
                "estimated_cost_usd": 50.0, "trust": "user",
            }),
        )
    err = exc_info.value
    assert err.message == "approval_required"
    assert "approval_id" in err.data
    assert err.data["estimated_cost_usd"] == 50.0


@pytest.mark.asyncio
async def test_after_owner_grants_call_succeeds(broker):
    from agentstorming_client.broker_client import BrokerClient, BrokerError
    sock_path, _ = broker
    c = BrokerClient(socket_path=sock_path)
    loop = asyncio.get_event_loop()

    # First call — get an approval_id.
    with pytest.raises(BrokerError) as exc_info:
        await loop.run_in_executor(
            None, lambda: c._call("prepare_headers", {
                "provider": "stripe", "target": "stripe",
                "method": "POST", "url": "https://api.stripe.com/v1/charges",
                "estimated_cost_usd": 50.0, "trust": "user",
                "approval_id": "test-approval-1",
            }),
        )
    assert exc_info.value.data["approval_id"] == "test-approval-1"

    # Owner grants. (In production this would arrive via storm-server
    # over a separate channel; here the same uid simulates it.)
    grant = await loop.run_in_executor(
        None, lambda: c._call("record_approval", {
            "approval_id": "test-approval-1",
            "decision": "granted",
        }),
    )
    assert grant["decision"] == "granted"

    # Re-submit with the same approval_id; broker should now allow.
    res = await loop.run_in_executor(
        None, lambda: c._call("prepare_headers", {
            "provider": "stripe", "target": "stripe",
            "method": "POST", "url": "https://api.stripe.com/v1/charges",
            "estimated_cost_usd": 50.0, "trust": "user",
            "approval_id": "test-approval-1",
        }),
    )
    assert "headers" in res


@pytest.mark.asyncio
async def test_record_approval_unknown_id_is_denied(broker):
    from agentstorming_client.broker_client import BrokerClient, BrokerError
    sock_path, _ = broker
    c = BrokerClient(socket_path=sock_path)
    loop = asyncio.get_event_loop()
    with pytest.raises(BrokerError) as exc_info:
        await loop.run_in_executor(
            None, lambda: c._call("record_approval", {
                "approval_id": "does-not-exist",
                "decision": "granted",
            }),
        )
    assert exc_info.value.message == "unknown_approval_id"
