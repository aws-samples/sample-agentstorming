# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""End-to-end test for `agentstorming owner approve` CLI subcommand.

Stands up a real storm-broker with a pending approval, then invokes
the CLI to flip it to granted, and verifies via record_approval state.
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

from agentstorming_client.cli import main as cli_main

pytestmark = pytest.mark.skipif(
    sys.platform not in ("linux", "darwin"),
    reason="broker uses POSIX peercred",
)


@pytest_asyncio.fixture
async def broker_with_pending():
    short_dir = Path(tempfile.mkdtemp(prefix="sb-cli-", dir="/tmp"))  # nosec B108 - mkdtemp creates 0700; /tmp is deliberate because macOS caps AF_UNIX paths at 104 chars and pytest's tmp_path exceeds it
    sock_path = str(short_dir / "b.sock")
    chain = AuditChain(short_dir / "audit.ndjson", secrets.token_bytes(32))

    providers = {
        "stripe": BearerTokenProvider({
            # Not shaped like a Stripe restricted key: nothing here asserts
            # the value, so a realistic prefix would only trip secret scanners.
            # nosec B105 — B105 matches the "token" key name, not the value.
            "token": "fixture-stripe-not-a-key",  # nosec B105
            "host_allowlist": ["api.stripe.com"],
        }),
    }
    my_uid = os.getuid()
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

    # Trigger an approval_required to seed the pending registry.
    from agentstorming_client.broker_client import BrokerClient, BrokerError
    c = BrokerClient(socket_path=sock_path)
    loop = asyncio.get_event_loop()
    approval_id = "cli-test-approval-1"
    try:
        await loop.run_in_executor(
            None, lambda: c._call("prepare_headers", {
                "provider": "stripe", "target": "stripe",
                "method": "POST",
                "url": "https://api.stripe.com/v1/charges",
                "estimated_cost_usd": 50.0, "trust": "user",
                "approval_id": approval_id,
            }),
        )
    except BrokerError:
        pass  # expected — that's how we seed the pending entry.

    try:
        yield sock_path, approval_id
    finally:
        await srv.stop()
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass


@pytest.mark.asyncio
async def test_owner_approve_cli_flips_pending_to_granted(
    broker_with_pending, capsys,
):
    sock_path, approval_id = broker_with_pending
    loop = asyncio.get_event_loop()
    rc = await loop.run_in_executor(
        None, lambda: cli_main([
            "owner", "approve",
            "--approval-id", approval_id,
            "--decision", "granted",
            "--broker-socket", sock_path,
        ]),
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert approval_id in out
    assert "granted" in out


@pytest.mark.asyncio
async def test_owner_approve_unknown_id_returns_nonzero(
    broker_with_pending, capsys,
):
    sock_path, _ = broker_with_pending
    loop = asyncio.get_event_loop()
    rc = await loop.run_in_executor(
        None, lambda: cli_main([
            "owner", "approve",
            "--approval-id", "definitely-not-pending",
            "--decision", "granted",
            "--broker-socket", sock_path,
        ]),
    )
    assert rc == 2
    err = capsys.readouterr().err
    assert "unknown_approval_id" in err
