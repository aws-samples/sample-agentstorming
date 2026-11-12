# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Regression tests for the third code review of the owner-signature fix.

As in the earlier rounds, most of these are a check applied on one surface and
missing on its twin, or a fix whose edges were not covered.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from agentstorming_server.app import create_app
from agentstorming_server.config import Settings
from agentstorming_server.services.jcs import canonicalise
from agentstorming_server.services.rate_limit import RateLimiter
from agentstorming_server.services.sig import generate_keypair, sign_blob

from ..conftest import DSN
from .test_owner_auth import (
    PRIVATE_ROOM,
    _create_private_room,
    _nonce_headers,
    _now,
    _signed_body,
    _signed_headers,
)


def _b64url(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")


async def _claim(client, room_id: str, invite: str, *, pub: bytes | None = None,
                 mount: str = "claim") -> httpx.Response:
    if pub is None:
        _priv, pub = generate_keypair()
    return await client.post(f"/v1/rooms/{room_id}/{mount}",
                             json={"invite_token": invite, "pubkey": _b64url(pub)})


def _state(app_client):
    return app_client._transport.app.state


# ---- the candidate route cannot grow the limiter or starve a candidate ----------------


@pytest.mark.asyncio
async def test_an_oversized_candidate_pubkey_never_reaches_the_limiter(app_client, fresh_public_room):
    """A megabyte "pubkey" per request was kept as a rate-limit key for the life
    of the process."""
    limiter = _state(app_client).rate_limiter
    before = len(limiter._buckets)
    url = f"/v1/rooms/{fresh_public_room['room_id']}/register/credentials"
    for i in range(20):
        r = await app_client.post(url, json={"interview_id": "x", "nonce": "n", "ts": _now(),
                                             "pubkey": f"{i}" + "A" * 100_000, "sig": "AAAA"})
        assert r.status_code == 422, r.text
    assert len(limiter._buckets) == before


@pytest.mark.asyncio
async def test_junk_bodies_naming_a_candidate_do_not_spend_its_allowance(app_client, fresh_public_room):
    """The limiter ran before the signature, so anyone who knew a pending
    candidate's pubkey could keep its correctly signed poll at 429."""
    _priv, pub = generate_keypair()
    url = f"/v1/rooms/{fresh_public_room['room_id']}/register/credentials"
    for _ in range(10):
        r = await app_client.post(url, json={"interview_id": "x", "nonce": "n", "ts": _now(),
                                             "pubkey": _b64url(pub), "sig": "AAAA"})
        assert r.status_code == 403, r.text
    assert not any(k[0] == f"pub:{_b64url(pub)}" for k in _state(app_client).rate_limiter._buckets)


@pytest.mark.asyncio
async def test_the_limiter_drops_refilled_buckets_once_it_is_large(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr("agentstorming_server.services.rate_limit.time.monotonic", lambda: now[0])
    limiter = RateLimiter(post_per_minute=10, raise_hand_per_minute=1, sync_per_minute=60)
    monkeypatch.setattr(RateLimiter, "PRUNE_AT", 100)
    for i in range(100):
        await limiter.check(f"key-{i}", "register")
    assert len(limiter._buckets) == 100
    now[0] += 3600            # every bucket has refilled
    await limiter.check("new-key", "register")
    assert len(limiter._buckets) == 1


# ---- claims -------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_key_already_in_the_room_cannot_claim_again(app_client, fresh_room):
    """The insert is ON CONFLICT DO NOTHING, so a re-claim spent the invite and
    broadcast a signed participant_joined for an affiliation never stored."""
    room = fresh_room["room_id"]
    _priv, pub = generate_keypair()
    assert (await _claim(app_client, room, fresh_room["participant_invite"], pub=pub)).status_code == 200
    events_before = await _state(app_client).events.get_after(room, -1, 1000)
    r = await _claim(app_client, room, fresh_room["moderator_invite"], pub=pub, mount="claim_moderator")
    assert r.status_code == 409 and r.json()["detail"]["code"] == "org.agentstorming.err.already_joined"
    assert await _state(app_client).invites.peek(fresh_room["moderator_invite"]) is not None
    events_after = await _state(app_client).events.get_after(room, -1, 1000)
    assert len(events_after) == len(events_before), "no participant_joined for a seat not taken"


@pytest.mark.asyncio
async def test_two_moderator_invites_at_once_seat_one_moderator(app_client, fresh_room, monkeypatch):
    """The seat check and the insert are separate statements. A slow insert
    lets both claims pass the check first, as real concurrency would."""
    room = fresh_room["room_id"]
    state = _state(app_client)
    _, second = await state.invites.create(room, "moderator", datetime.now(UTC) + timedelta(hours=1))
    real_insert = state.participants.insert

    async def slow_insert(p, **kw):
        await asyncio.sleep(0.3)
        return await real_insert(p, **kw)

    monkeypatch.setattr(state.participants, "insert", slow_insert)
    results = await asyncio.gather(
        _claim(app_client, room, fresh_room["moderator_invite"], mount="claim_moderator"),
        _claim(app_client, room, second, mount="claim_moderator"),
    )
    codes = sorted(r.status_code for r in results)
    assert codes == [200, 409], [r.text for r in results]
    loser = next(r for r in results if r.status_code == 409)
    assert loser.json()["detail"]["code"] == "org.agentstorming.err.moderator_seat_occupied"
    async with state.db.acquire() as conn:
        seated = await conn.fetchval(
            "SELECT count(*) FROM participants WHERE room_id = $1 AND affiliation = 'original-moderator'",
            room)
    assert seated == 1


@pytest.mark.asyncio
async def test_concurrent_refreshes_of_one_token_yield_one_pair(app_client, fresh_room):
    """Lookup then revoke let every concurrent refresh pass and get a new pair."""
    member = (await _claim(app_client, fresh_room["room_id"], fresh_room["participant_invite"])).json()
    body = {"refresh_token": member["refresh_token"]}
    results = await asyncio.gather(*[app_client.post("/v1/tokens/refresh", json=body) for _ in range(5)])
    assert sorted(r.status_code for r in results).count(200) == 1, [r.status_code for r in results]


# ---- NUL never reaches Postgres ----------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/v1/rooms/%00/register", "/v1/rooms/a%00b/register/nonce"])
async def test_a_nul_in_the_path_is_a_400(app_client, path):
    method = "GET" if path.endswith("nonce") else "POST"
    r = await app_client.request(method, path, json={"pubkey": "x"} if method == "POST" else None)
    assert r.status_code == 400, r.text


@pytest.mark.asyncio
@pytest.mark.parametrize("s3_key", ["\x00", "a\x00b", "\ud800"])
async def test_a_nul_or_surrogate_s3_key_is_a_400(app_client, fresh_room, s3_key):
    member = (await _claim(app_client, fresh_room["room_id"], fresh_room["participant_invite"])).json()
    r = await app_client.post("/v1/attachments/refresh",
                              content=json.dumps({"s3_key": s3_key}),
                              headers={"content-type": "application/json",
                                       "Authorization": f"Bearer {member['access_token']}"})
    assert r.status_code == 400, r.text


@pytest.mark.asyncio
async def test_a_self_signed_nul_interview_id_is_a_400(app_client, fresh_public_room):
    priv, pub = generate_keypair()
    fields = {"interview_id": "\x00", "nonce": "n", "ts": _now()}
    body = {**fields, "pubkey": _b64url(pub), "sig": sign_blob(canonicalise(fields), priv)}
    r = await app_client.post(f"/v1/rooms/{fresh_public_room['room_id']}/register/credentials",
                              content=json.dumps(body), headers={"content-type": "application/json"})
    assert r.status_code == 400, r.text


# ---- /sync and /stream deliver everything --------------------------------------------------------


@pytest.mark.asyncio
async def test_sync_advances_past_a_page_of_events_the_viewer_cannot_see(app_client, fresh_room):
    """The cursor was computed after the visibility filter, so a page of other
    people's whispers left it in place and the client asked for it forever."""
    room = fresh_room["room_id"]
    member = (await _claim(app_client, room, fresh_room["participant_invite"])).json()
    state = _state(app_client)
    start = (await app_client.get(f"/v1/rooms/{room}/sync", params={"since": 0, "limit": 1000},
                                  headers={"Authorization": f"Bearer {member['access_token']}"})).json()["next_since"]
    for _ in range(3):
        await state.sys_publish_event(room, "org.agentstorming.go_speak_expired",
                                      payload={"pid": "someone-else", "target_pid": "someone-else"})
    r = await app_client.get(f"/v1/rooms/{room}/sync", params={"since": start, "limit": 2},
                             headers={"Authorization": f"Bearer {member['access_token']}"})
    assert r.status_code == 200 and r.json()["events"] == []
    assert r.json()["next_since"] > start


@pytest.mark.asyncio
async def test_a_stream_more_than_one_page_behind_catches_up_at_once(fresh_room, tmp_path):
    """Replay read one page and then waited for a wake, so a client more than
    `limit` events behind got the rest only when something else happened."""
    import uvicorn
    os.environ["AGENTSTORMING_DSN"] = DSN
    app = create_app(Settings(dsn=DSN, attachments_local_dir=tmp_path / "a"))
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning"))
    serve = asyncio.create_task(server.serve())
    try:
        for _ in range(100):
            if server.started:
                break
            await asyncio.sleep(0.05)
        port = server.servers[0].sockets[0].getsockname()[1]
        base = f"http://127.0.0.1:{port}"
        room = fresh_room["room_id"]
        async with httpx.AsyncClient(base_url=base, timeout=10) as c:
            member = (await _claim(c, room, fresh_room["participant_invite"])).json()
            auth = {"Authorization": f"Bearer {member['access_token']}"}
            cursor = (await c.get(f"/v1/rooms/{room}/sync", params={"since": 0, "limit": 1000},
                                  headers=auth)).json()["next_since"]
            for i in range(7):
                await app.state.sys_publish_event(room, "org.agentstorming.invite_minted",
                                                  payload={"kind": "participant", "by_pid": f"p{i}",
                                                           "expires_at": _now()})
            seen: set[int] = set()

            async def read():
                async with c.stream("GET", f"/v1/rooms/{room}/stream",
                                    params={"since": cursor, "limit": 2}, headers=auth) as resp:
                    async for line in resp.aiter_lines():
                        if line.startswith("id: ") and line[4:].isdigit():
                            seen.add(int(line[4:]))
                            if len(seen) >= 7:
                                return

            await asyncio.wait_for(read(), timeout=5)
        assert len(seen) == 7
    finally:
        server.should_exit = True
        await asyncio.wait_for(serve, timeout=10)


# ---- owner surface edges ----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_every_signed_owner_body_refuses_an_unknown_field(owner):
    """A typo such as ttl for ttl_seconds was dropped, and a one-minute invite
    came out lasting a week."""
    await _create_private_room(owner)
    path = f"/v1/owner/rooms/{PRIVATE_ROOM}/invites"
    r = await owner["client"].post(path, json=await _signed_body(owner, "POST", path, {
        "room_id": PRIVATE_ROOM, "kind": "moderator", "ttl": 60}))
    assert r.status_code == 422, r.text
    path = f"/v1/owner/rooms/{PRIVATE_ROOM}/terminate"
    r = await owner["client"].post(path, json=await _signed_body(owner, "POST", path, {
        "room_id": PRIVATE_ROOM, "force": True}))
    assert r.status_code == 422, r.text


@pytest.mark.asyncio
async def test_a_text_json_media_type_is_refused_before_the_nonce(owner):
    await _create_private_room(owner)
    path = f"/v1/owner/rooms/{PRIVATE_ROOM}/terminate"
    body = await _signed_body(owner, "POST", path, {"room_id": PRIVATE_ROOM})
    r = await owner["client"].post(path, content=json.dumps(body), headers={"content-type": "text/x+json"})
    assert r.status_code == 415, r.text
    r = await owner["client"].post(path, json=body)
    assert r.status_code == 200, r.text


@pytest.mark.asyncio
async def test_a_captured_nonce_request_cannot_be_replayed(owner):
    """Each replay was charged to the owner's issuance limit; about 60 locked the
    owner out of every owner route."""
    c = owner["client"]
    headers = _nonce_headers(owner["priv"], owner["pub"])
    assert (await c.get("/v1/owner/nonce", headers=headers)).status_code == 200
    codes = {(await c.get("/v1/owner/nonce", headers=headers)).status_code for _ in range(70)}
    assert codes == {409}
    r = await c.get("/v1/owner/keys", headers=await _signed_headers(owner, "GET", "/v1/owner/keys"))
    assert r.status_code == 200, r.text


# ---- governance lock connection ------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_governance_lock_connection_is_reaped_when_its_client_vanishes(reset_db):
    from agentstorming_server.repo.base import Database
    from agentstorming_server.services.governance import GovernanceTicker
    db = Database(DSN)
    await db.connect()
    t = GovernanceTicker(db, None, None, None, None, None, None, None, tick_seconds=1.0, lock_id=778_043)
    try:
        conn = await t._connect_lock()
        version = int(await conn.fetchval("SHOW server_version_num"))
        assert int(await conn.fetchval("SHOW tcp_keepalives_idle")) == 10
        if version >= 140000:
            assert await conn.fetchval("SHOW idle_session_timeout") == "30s"
        await conn.close()
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_a_lone_surrogate_in_a_constrained_field_is_a_422_not_a_500(app_client, fresh_public_room):
    """FastAPI's default handler echoed the input with ensure_ascii=False and
    could not encode a lone surrogate, turning the 422 into a 500."""
    r = await app_client.post(f"/v1/rooms/{fresh_public_room['room_id']}/register",
                              content='{"pubkey": "\\ud800", "runs_as": "agent"}',
                              headers={"content-type": "application/json"})
    assert r.status_code == 422, r.text
    assert r.json()["detail"][0]["loc"] == ["body", "pubkey"]
