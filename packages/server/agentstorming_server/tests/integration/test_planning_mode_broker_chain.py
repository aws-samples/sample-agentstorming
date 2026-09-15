# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""The full planning-mode chain: server promotes → client relays → broker opens.

Each half of this is unit-tested elsewhere. What is only testable together is
whether the bytes the server signs are the bytes the broker can verify —
ADR-008 deliberately has the broker verify the relayed canonical form rather
than re-canonicalising, precisely so this cannot drift, and this test is what
proves the two ends still agree.
"""

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

from agentstorming_server.services.sig import generate_keypair

pytestmark = pytest.mark.skipif(
    sys.platform not in ("linux", "darwin"),
    reason="broker peer-credential checks need Linux or macOS",
)


def _b64url(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")


@pytest_asyncio.fixture
async def broker_for_room():
    """A real broker in planning mode, bound to a room + its server pubkey.

    Returns a factory so the test can supply the room's server pubkey, which
    it only learns from the snapshot after the room exists.
    """
    from storm_broker.audit import AuditChain
    from storm_broker.policy import Capability, PolicyEngine
    from storm_broker.server import BrokerServer

    started: list = []

    async def _start(room_id: str, server_pubkey: bytes):
        # macOS caps AF_UNIX paths at 104 chars; pytest tmp_path can exceed it.
        short_dir = Path(tempfile.mkdtemp(prefix="sb-mode-", dir="/tmp"))  # nosec B108 - mkdtemp creates 0700; /tmp is deliberate because macOS caps AF_UNIX paths at 104 chars and pytest's tmp_path exceeds it
        chain = AuditChain(short_dir / "audit.ndjson", secrets.token_bytes(32))
        uid = os.getuid()
        engine = PolicyEngine(
            [
                Capability(tool="github.create_pr", rate_per_second=100.0,
                           requires_trust="user", effects="write"),
                Capability(tool="github.read_issue", rate_per_second=100.0,
                           requires_trust="user", effects="read"),
            ],
            room_mode="planning",
        )
        srv = BrokerServer(
            socket_path=str(short_dir / "b.sock"),
            providers={},
            policy_for_uid={uid: engine},
            audit_chain=chain,
            allowed_uids={uid},
            room_id=room_id,
            server_pubkey=server_pubkey,
        )
        await srv.start()
        task = asyncio.create_task(srv._server.serve_forever())
        started.append((srv, task))
        return srv, engine

    yield _start

    for srv, task in started:
        task.cancel()
        await srv.stop()


async def _bk(fn, *args, **kwargs):
    """Call the synchronous BrokerClient without deadlocking the test loop.

    The broker under test runs on this same event loop, so a blocking socket
    read from the loop thread would wait for a reply the loop can never get
    around to sending. Real agents never hit this — their broker is a
    separate process — but the harness must not pretend otherwise.
    """
    return await asyncio.to_thread(fn, *args, **kwargs)


async def _claim(app_client, invite, room_id, kind="moderator"):
    priv, pub = generate_keypair()
    path = {"participant": "claim", "moderator": "claim_moderator",
            "owner": "claim_owner"}[kind]
    r = await app_client.post(
        f"/v1/rooms/{room_id}/{path}",
        json={"invite_token": invite, "pubkey": _b64url(pub)},
    )
    assert r.status_code in (200, 201), r.text
    return r.json()


@pytest.mark.asyncio
async def test_promotion_travels_from_server_through_client_to_broker(
    app_client, fresh_planning_room, broker_for_room
):
    from agentstorming_client.broker_client import BrokerClient

    room = fresh_planning_room["room_id"]
    moderator = await _claim(app_client, fresh_planning_room["moderator_invite"], room)
    auth = {"Authorization": f"Bearer {moderator['access_token']}"}

    server_pubkey = base64.urlsafe_b64decode(
        moderator["snapshot"]["payload"]["server_pubkey"] + "=="
    )
    srv, engine = await broker_for_room(room, server_pubkey)
    client = BrokerClient(socket_path=srv.socket_path, timeout=10.0)

    # Planning: the write capability is refused, the read one is not.
    assert (await _bk(client.get_room_mode))["mode"] == "planning"
    assert not engine.evaluate("github.create_pr", {}).allow
    assert engine.evaluate("github.read_issue", {}).allow

    # The moderator promotes the room.
    r = await app_client.post(
        f"/v1/rooms/{room}/moderation/mode", json={"mode": "active"}, headers=auth,
    )
    assert r.status_code == 200, r.text

    # The agent relays the event it received. It is only the courier: the
    # broker verifies the server's signature itself.
    r = await app_client.get(
        f"/v1/rooms/{room}/sync?since=-1&wait=0&limit=100", headers=auth,
    )
    promo = next(e for e in r.json()["events"]
                 if e["type"] == "org.agentstorming.mode_promoted")
    out = await _bk(client.relay_room_mode, promo)
    assert out["mode"] == "active"

    assert (await _bk(client.get_room_mode))["mode"] == "active"
    assert engine.evaluate("github.create_pr", {}).allow


@pytest.mark.asyncio
async def test_a_snapshot_also_carries_the_mode(
    app_client, fresh_planning_room, broker_for_room
):
    """An agent that joins after the promotion learns the mode from a snapshot."""
    from agentstorming_client.broker_client import BrokerClient

    room = fresh_planning_room["room_id"]
    moderator = await _claim(app_client, fresh_planning_room["moderator_invite"], room)
    auth = {"Authorization": f"Bearer {moderator['access_token']}"}
    server_pubkey = base64.urlsafe_b64decode(
        moderator["snapshot"]["payload"]["server_pubkey"] + "=="
    )

    await app_client.post(
        f"/v1/rooms/{room}/moderation/mode", json={"mode": "active"}, headers=auth,
    )

    # A late joiner's snapshot reflects the promotion that already happened.
    srv, engine = await broker_for_room(room, server_pubkey)
    client = BrokerClient(socket_path=srv.socket_path, timeout=10.0)
    assert (await _bk(client.get_room_mode))["mode"] == "planning"

    r = await app_client.get(f"/v1/rooms/{room}/snapshot", headers=auth)
    snapshot = r.json()
    assert snapshot["payload"]["config"]["mode"] == "active"
    assert (await _bk(client.maybe_relay_room_mode, snapshot))["mode"] == "active"
    assert engine.evaluate("github.create_pr", {}).allow


@pytest.mark.asyncio
async def test_broker_refuses_an_envelope_from_another_room(
    app_client, fresh_planning_room, fresh_room, broker_for_room
):
    """A promotion in one room must not unlock a broker bound to another."""
    from agentstorming_client.broker_client import BrokerClient
    from agentstorming_client.broker_client import BrokerError

    planning = fresh_planning_room["room_id"]
    mod_p = await _claim(app_client, fresh_planning_room["moderator_invite"], planning)
    pubkey_p = base64.urlsafe_b64decode(
        mod_p["snapshot"]["payload"]["server_pubkey"] + "=="
    )
    srv, engine = await broker_for_room(planning, pubkey_p)
    client = BrokerClient(socket_path=srv.socket_path, timeout=10.0)

    # Promote a *different* room and try to replay its event here.
    other = fresh_room["room_id"]
    mod_o = await _claim(app_client, fresh_room["moderator_invite"], other)
    auth_o = {"Authorization": f"Bearer {mod_o['access_token']}"}
    r = await app_client.get(
        f"/v1/rooms/{other}/sync?since=-1&wait=0&limit=100", headers=auth_o,
    )
    foreign = next(e for e in r.json()["events"]
                   if e["type"] == "org.agentstorming.metadata_snapshot")

    with pytest.raises(BrokerError) as ei:
        await _bk(client.relay_room_mode, foreign)
    assert ei.value.message == "mode_verification_failed"
    assert (await _bk(client.get_room_mode))["mode"] == "planning"
    assert not engine.evaluate("github.create_pr", {}).allow
