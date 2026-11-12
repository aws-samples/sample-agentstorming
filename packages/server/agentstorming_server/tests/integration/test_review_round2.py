# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Regression tests for the second code review of the owner-signature fix.

Each test names the defect it pins. Most were gaps left by the first round of
fixes (a check applied on one surface but not its twin) and a few predate it.
"""

from __future__ import annotations

import asyncio
import base64
import os
import uuid
from datetime import UTC, datetime, timedelta

import asyncpg
import httpx
import pytest

from agentstorming_server.app import create_app
from agentstorming_server.config import Settings
from agentstorming_server.repo.base import Database
from agentstorming_server.services.governance import GovernanceTicker
from agentstorming_server.services.sig import generate_keypair

from ..conftest import DSN
from .test_attachment_access import _upload
from .test_owner_auth import (
    PRIVATE_ROOM,
    _create_private_room,
    _now,
    _sign,
    _signed_body,
    _signed_headers,
)
from .test_pubsub_and_governance import TEST_LOCK_ID


def _b64url(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")


async def _claim(client, room_id: str, invite: str, mount: str = "claim") -> httpx.Response:
    _priv, pub = generate_keypair()
    return await client.post(f"/v1/rooms/{room_id}/{mount}",
                             json={"invite_token": invite, "pubkey": _b64url(pub)})


# ---- snapshots on a fresh process ------------------------------------------


@pytest.mark.asyncio
async def test_a_fresh_replica_serves_the_first_snapshot(fresh_room, tmp_path):
    """/sync and /stream without a cursor answered 500 on any process that had not
    yet served a claim for the room: every restart and every second replica."""
    os.environ["AGENTSTORMING_DSN"] = DSN
    room = fresh_room["room_id"]
    first = create_app(Settings(dsn=DSN, attachments_local_dir=tmp_path / "a"))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=first), base_url="http://t") as a, \
            first.router.lifespan_context(first):
        member = (await _claim(a, room, fresh_room["participant_invite"])).json()
    fresh = create_app(Settings(dsn=DSN, attachments_local_dir=tmp_path / "b"))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=fresh), base_url="http://t") as b, \
            fresh.router.lifespan_context(fresh):
        r = await b.get(f"/v1/rooms/{room}/sync", params={"since": -1},
                        headers={"Authorization": f"Bearer {member['access_token']}"})
        assert r.status_code == 200, r.text
        assert r.json()["events"][0]["type"] == "org.agentstorming.metadata_snapshot"


# ---- odd tokens are 4xx, never 500 -------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("bearer", ["café", "ÿ" * 40])
async def test_a_non_ascii_bearer_is_a_401(app_client, fresh_room, bearer):
    r = await app_client.get(f"/v1/rooms/{fresh_room['room_id']}/messages",
                             headers={"Authorization": f"Bearer {bearer}".encode("latin-1")})
    assert r.status_code == 401, r.text


@pytest.mark.asyncio
@pytest.mark.parametrize("token,status", [("café", 401), (123, 400), (["x"], 400), ("", 400)])
async def test_an_odd_refresh_token_is_refused_not_a_500(app_client, token, status):
    r = await app_client.post("/v1/tokens/refresh", json={"refresh_token": token})
    assert r.status_code == status, r.text


# ---- claims never burn an invite they refuse ---------------------------------


@pytest.mark.asyncio
async def test_a_kind_mismatch_does_not_burn_the_invite(app_client, fresh_room):
    room = fresh_room["room_id"]
    r = await _claim(app_client, room, fresh_room["participant_invite"], "claim_moderator")
    assert r.status_code == 403 and r.json()["detail"]["code"] == "org.agentstorming.err.invite_kind_mismatch"
    r = await _claim(app_client, room, fresh_room["participant_invite"])
    assert r.status_code == 200, r.text


@pytest.mark.asyncio
async def test_a_held_moderator_seat_does_not_burn_the_invite(app_client, fresh_room):
    room = fresh_room["room_id"]
    assert (await _claim(app_client, room, fresh_room["moderator_invite"])).status_code == 200
    state = app_client._transport.app.state
    _, second = await state.invites.create(room, "moderator", datetime.now(UTC) + timedelta(hours=1))
    r = await _claim(app_client, room, second)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "org.agentstorming.err.moderator_seat_occupied"
    assert await state.invites.peek(second) is not None, "the refused invite must still be redeemable"


# ---- room creation validates its config -----------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("extra", [
    {"mode": "planning"},                                   # a §5.2 field outside config
    {"config": {"max_participants": "x"}},                  # wrong type: was a 500
    {"config": {"attachments": "x"}},                       # wrong shape: was a 500
    {"config": {"max_participants": 0}},                    # out of range: was stored
    {"config": {"visibility": "weird"}},
    {"config": {"no_such_field": True}},
])
async def test_room_creation_refuses_config_it_cannot_honour(owner, extra):
    path = "/v1/owner/rooms"
    r = await owner["client"].post(path, json=await _signed_body(owner, "POST", path, {
        "room_id": "cfg-room", **extra}))
    assert r.status_code == 422, f"{extra} -> {r.status_code} {r.text[:200]}"


@pytest.mark.asyncio
async def test_room_creation_applies_a_valid_config(owner):
    path = "/v1/owner/rooms"
    cfg = {"mode": "planning", "raise_hand_required": True, "max_participants": 5,
           "attachments": {"enabled": False}}
    r = await owner["client"].post(path, json=await _signed_body(owner, "POST", path, {
        "room_id": "cfg-room", "config": cfg}))
    assert r.status_code == 200, r.text
    room = await owner["app"].state.rooms.get("cfg-room")
    assert (room.config.mode, room.config.raise_hand_required, room.config.max_participants,
            room.config.attachments_enabled) == ("planning", True, 5, False)


# ---- nonces: scoped, and checked before the limiter ------------------------------


@pytest.mark.asyncio
async def test_a_registration_nonce_does_not_open_an_owner_route(owner, fresh_public_room):
    c = owner["client"]
    nonce = (await c.get(f"/v1/rooms/{fresh_public_room['room_id']}/register/nonce")).json()["nonce"]
    ts = _now()
    headers = {
        "X-AgentStorming-Owner-Nonce": nonce, "X-AgentStorming-Owner-Ts": ts,
        "X-AgentStorming-Owner-Pubkey": _b64url(owner["pub"]),
        "X-AgentStorming-Owner-Sig": _sign("GET", "/v1/owner/keys", {}, nonce, ts, owner["priv"]),
    }
    r = await c.get("/v1/owner/keys", headers=headers)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "org.agentstorming.err.nonce_invalid"


@pytest.mark.asyncio
async def test_an_owner_nonce_does_not_collect_candidate_credentials(owner, fresh_public_room):
    state = owner["app"].state
    nonce, _ = await state.owner_nonces.issue()
    assert not await state.owner_nonces.consume(nonce, scope="registration")
    assert await state.owner_nonces.consume(nonce)


@pytest.mark.asyncio
async def test_replaying_a_used_request_does_not_spend_the_owner_limit(owner):
    """With the limiter ahead of the nonce check, 30 replays of one captured
    request emptied the owner's bucket and the owner got 429."""
    c = owner["client"]
    headers = await _signed_headers(owner, "GET", "/v1/owner/keys")
    assert (await c.get("/v1/owner/keys", headers=headers)).status_code == 200
    codes = {(await c.get("/v1/owner/keys", headers=headers)).status_code for _ in range(40)}
    assert codes == {409}
    r = await c.get("/v1/owner/keys", headers=await _signed_headers(owner, "GET", "/v1/owner/keys"))
    assert r.status_code == 200, r.text


@pytest.mark.asyncio
async def test_a_non_json_signed_body_is_a_415_and_keeps_its_nonce(owner):
    await _create_private_room(owner)
    c = owner["client"]
    path = f"/v1/owner/rooms/{PRIVATE_ROOM}/moderator"
    body = await _signed_body(owner, "DELETE", path, {"room_id": PRIVATE_ROOM})
    import json as _json
    r = await c.request("DELETE", path, content=_json.dumps(body), headers={"content-type": "text/plain"})
    assert r.status_code == 415, r.text
    r = await c.request("DELETE", path, json=body)
    assert r.status_code == 200, r.text


# ---- attachments: the bearer comes before the lookup --------------------------------


@pytest.mark.asyncio
async def test_a_junk_bearer_cannot_tell_real_attachment_ids(app_client, fresh_room):
    member = (await _claim(app_client, fresh_room["room_id"], fresh_room["participant_invite"])).json()
    real = await _upload(app_client, fresh_room["room_id"], member["access_token"])
    junk = {"Authorization": "Bearer junk"}
    for att_id in (real, str(uuid.uuid4())):
        assert (await app_client.get(f"/v1/attachments/{att_id}", headers=junk)).status_code == 401
        r = await app_client.post("/v1/attachments/refresh", json={"att_id": att_id}, headers=junk)
        assert r.status_code == 401


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [{"s3_key": 5}, {"s3_key": ["k"]}, {"att_id": "not-a-uuid"}])
async def test_a_malformed_refresh_body_is_a_400(app_client, fresh_room, body):
    member = (await _claim(app_client, fresh_room["room_id"], fresh_room["participant_invite"])).json()
    r = await app_client.post("/v1/attachments/refresh", json=body,
                              headers={"Authorization": f"Bearer {member['access_token']}"})
    assert r.status_code == 400, r.text


# ---- candidate credentials: same input checks as the owner surface -----------------


@pytest.mark.asyncio
@pytest.mark.parametrize("override", [{"ts": "2026-11-02T10:00:00"}, {"interview_id": "\ud800"}])
async def test_candidate_credential_collection_never_answers_500(app_client, fresh_public_room, override):
    _priv, pub = generate_keypair()
    body = {"interview_id": "x", "nonce": "n", "ts": _now(), "pubkey": _b64url(pub), "sig": "AAAA", **override}
    import json as _json
    r = await app_client.post(f"/v1/rooms/{fresh_public_room['room_id']}/register/credentials",
                              content=_json.dumps(body), headers={"content-type": "application/json"})
    # 400 from the route's own checks, or 422 from body validation: never 500.
    assert r.status_code in (400, 422), r.text


# ---- dynamic invites and links ---------------------------------------------------------


@pytest.mark.asyncio
async def test_a_moderator_mints_participant_invites_only(app_client, fresh_room):
    room = fresh_room["room_id"]
    mod = (await _claim(app_client, room, fresh_room["moderator_invite"])).json()
    auth = {"Authorization": f"Bearer {mod['access_token']}"}
    url = f"/v1/rooms/{room}/moderation/dynamic-invite"
    for kind in ("owner", "moderator"):
        r = await app_client.post(url, json={"kind": kind}, headers=auth)
        assert r.status_code == 400 and r.json()["detail"]["code"] == "org.agentstorming.err.bad_kind"
    for ttl in (0, -5, "60", 10**12, True):
        assert (await app_client.post(url, json={"ttl_seconds": ttl}, headers=auth)).status_code == 400
    r = await app_client.post(url, json={"reason": "need a cryptographer"}, headers=auth)
    assert r.status_code == 200 and r.json()["kind"] == "participant", r.text
    assert f"/#/r/{room}/join?t=" in r.json()["invite_link"]


@pytest.mark.asyncio
async def test_owner_invite_links_keep_the_token_out_of_the_request(owner):
    await _create_private_room(owner)
    path = f"/v1/owner/rooms/{PRIVATE_ROOM}/invites"
    r = await owner["client"].post(path, json=await _signed_body(owner, "POST", path, {
        "room_id": PRIVATE_ROOM, "kind": "participant"}))
    link = httpx.URL(r.json()["invite_link"])
    assert r.json()["invite_token"] not in str(link.copy_with(fragment=None))
    assert link.fragment == f"/r/{PRIVATE_ROOM}/join?t={r.json()['invite_token']}"


# ---- governance elects one replica ----------------------------------------------------


@pytest.mark.asyncio
async def test_one_replica_governs_and_another_takes_over(reset_db):
    """Ticks were serialised but every replica still ticked in turn, and each kept
    its own snapshot bookkeeping, so every replica published its own snapshots."""
    dbs = [Database(DSN), Database(DSN)]
    for db in dbs:
        await db.connect()
    counts = [0, 0]

    def ticker(i):
        class Ticker(GovernanceTicker):
            async def _tick(self) -> None:
                counts[i] += 1
        return Ticker(dbs[i], None, None, None, None, None, None, None,
                      tick_seconds=0.05, lock_id=TEST_LOCK_ID)

    a, b = ticker(0), ticker(1)
    try:
        await a.start()
        await asyncio.sleep(0.3)
        await b.start()
        await asyncio.sleep(0.6)
        assert counts[0] > 0 and counts[1] == 0, counts
        await a.stop()
        before = counts[1]
        await asyncio.sleep(0.5)
        assert counts[1] > before, "the second replica must take over once the first stops"
    finally:
        await a.stop()
        await b.stop()
        for db in dbs:
            await db.close()


@pytest.mark.asyncio
async def test_a_leader_whose_lock_connection_dies_stands_down(reset_db):
    db = Database(DSN)
    await db.connect()
    ticks = {"n": 0}

    class Ticker(GovernanceTicker):
        async def _tick(self) -> None:
            ticks["n"] += 1

    t = Ticker(db, None, None, None, None, None, None, None,
               tick_seconds=0.05, lock_id=TEST_LOCK_ID)
    try:
        assert await t._lead() is True
        pid = t._lock_conn.get_server_pid()
        admin = await asyncpg.connect(DSN)
        try:
            await admin.execute("SELECT pg_terminate_backend($1)", pid)
            await asyncio.sleep(0.2)
            # The lock went with the session; whoever asks next may take it.
            assert await admin.fetchval("SELECT pg_try_advisory_lock($1)", TEST_LOCK_ID)
            assert await t._lead() is False
            await admin.execute("SELECT pg_advisory_unlock($1)", TEST_LOCK_ID)
        finally:
            await admin.close()
        assert await t._lead() is True
    finally:
        await t._resign()
        await db.close()


# ---- the LISTEN connection on a silent peer -----------------------------------------------


@pytest.mark.asyncio
async def test_the_hub_recovers_from_a_peer_that_goes_silent(reset_db):
    """Closing a connection whose peer had stopped answering waited forever, so the
    hub never reconnected. Freeze the flow through a local proxy, as a NAT that
    drops state would, and the hub must still come back."""
    from agentstorming_server.services.pubsub import PubSubHub
    upstream = httpx.URL(DSN.replace("postgresql://", "http://"))
    states: list[dict] = []

    async def pipe(reader, writer, state):
        try:
            while data := await reader.read(65536):
                while state["frozen"]:
                    await asyncio.sleep(0.05)
                writer.write(data)
                await writer.drain()
        except (ConnectionError, OSError):
            pass

    async def handle(cr, cw):
        ur, uw = await asyncio.open_connection(upstream.host, upstream.port or 5432)
        state = {"frozen": False}
        states.append(state)
        await asyncio.gather(pipe(cr, uw, state), pipe(ur, cw, state), return_exceptions=True)

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    proxied = DSN.replace(f"{upstream.host}:{upstream.port or 5432}", f"127.0.0.1:{port}")
    hub = PubSubHub(proxied, check_interval=0.3, probe_timeout=0.5)
    try:
        await hub.start()
        states[0]["frozen"] = True
        for _ in range(60):
            await asyncio.sleep(0.1)
            if hub.healthy and len(states) > 1:
                break
        assert hub.healthy and len(states) > 1, "the hub must reconnect past a silent peer"
    finally:
        await hub.stop()
        server.close()
