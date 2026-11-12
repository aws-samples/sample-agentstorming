# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Regression tests for the fourth review of the owner-signature fix.

Both were introduced by the third round's own fixes: the lock that stopped two
moderators being seated could stall the whole server, and the guard that
stopped a captured nonce request being replayed could be stepped round by
re-encoding the signature.
"""

from __future__ import annotations

import asyncio
import base64
import time
from datetime import UTC, datetime, timedelta

import pytest

from agentstorming_server.services.sig import generate_keypair

from .test_owner_auth import _nonce_headers

SIG = "X-AgentStorming-Owner-Sig"


def _b64url(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")


async def _claim_moderator(client, room_id: str, invite: str):
    _priv, pub = generate_keypair()
    return await client.post(f"/v1/rooms/{room_id}/claim_moderator",
                             json={"invite_token": invite, "pubkey": _b64url(pub)})


async def _member(client, room_id: str, invite: str) -> dict:
    _priv, pub = generate_keypair()
    r = await client.post(f"/v1/rooms/{room_id}/claim",
                          json={"invite_token": invite, "pubkey": _b64url(pub)})
    assert r.status_code == 200, r.text
    return r.json()


async def _timed_sync(client, room_id: str, member: dict) -> float:
    start = time.monotonic()
    r = await client.get(f"/v1/rooms/{room_id}/sync", params={"since": 0},
                         headers={"Authorization": f"Bearer {member['access_token']}"})
    assert r.status_code == 200, r.text
    return time.monotonic() - start


@pytest.mark.asyncio
async def test_forty_moderator_claims_at_once_do_not_stall_the_server(app_client, fresh_room):
    """The claim lock was held on one pooled connection while the claim took
    others, so claims queued on it held the whole pool and the holder could
    not get the connection it needed. An unrelated /sync waited 89 s."""
    room = fresh_room["room_id"]
    member = await _member(app_client, room, fresh_room["participant_invite"])
    claims = [_claim_moderator(app_client, room, fresh_room["moderator_invite"]) for _ in range(40)]
    started = time.monotonic()
    *results, sync_seconds = await asyncio.wait_for(
        asyncio.gather(*claims, _timed_sync(app_client, room, member)), timeout=20)
    assert time.monotonic() - started < 10
    assert sync_seconds < 10
    codes = sorted(r.status_code for r in results)
    assert codes.count(200) == 1, codes
    assert set(codes) <= {200, 409}, [r.text for r in results if r.status_code not in (200, 409)]


@pytest.mark.asyncio
async def test_claims_on_a_taken_seat_fail_fast_and_keep_their_invite(app_client, fresh_room):
    room = fresh_room["room_id"]
    state = app_client._transport.app.state
    assert (await _claim_moderator(app_client, room, fresh_room["moderator_invite"])).status_code == 200
    _, spare = await state.invites.create(room, "moderator", datetime.now(UTC) + timedelta(hours=1))
    member = await _member(app_client, room, fresh_room["participant_invite"])
    results = await asyncio.wait_for(asyncio.gather(
        *[_claim_moderator(app_client, room, spare) for _ in range(40)],
        _timed_sync(app_client, room, member)), timeout=20)
    sync_seconds = results.pop()
    assert sync_seconds < 10
    assert {r.json()["detail"]["code"] for r in results} == {"org.agentstorming.err.moderator_seat_occupied"}
    assert await state.invites.peek(spare) is not None


@pytest.mark.asyncio
async def test_the_losing_claim_of_a_race_keeps_its_invite(app_client, fresh_room, monkeypatch):
    """The second claim is refused inside the locked transaction, after it has
    redeemed. The rollback must hand its invite back."""
    room = fresh_room["room_id"]
    state = app_client._transport.app.state
    _, second = await state.invites.create(room, "moderator", datetime.now(UTC) + timedelta(hours=1))
    real_insert = state.participants.insert

    async def slow_insert(p, **kw):
        await asyncio.sleep(0.3)
        return await real_insert(p, **kw)

    monkeypatch.setattr(state.participants, "insert", slow_insert)
    first, other = await asyncio.gather(
        _claim_moderator(app_client, room, fresh_room["moderator_invite"]),
        _claim_moderator(app_client, room, second))
    winner_invite, loser_invite = ((fresh_room["moderator_invite"], second)
                                   if first.status_code == 200 else (second, fresh_room["moderator_invite"]))
    assert sorted([first.status_code, other.status_code]) == [200, 409]
    assert await state.invites.peek(winner_invite) is None
    assert await state.invites.peek(loser_invite) is not None


@pytest.mark.asyncio
async def test_an_invite_and_a_public_key_do_not_take_over_a_seat(app_client, fresh_room):
    """A claim carries no proof that the caller holds the key. Before the
    already_joined check, a spare participant invite and the moderator's public
    key, which participant_joined broadcasts, returned the moderator's tokens,
    and they worked on moderator routes."""
    room = fresh_room["room_id"]
    _priv, mod_pub = generate_keypair()
    r = await app_client.post(f"/v1/rooms/{room}/claim_moderator",
                              json={"invite_token": fresh_room["moderator_invite"], "pubkey": _b64url(mod_pub)})
    assert r.status_code == 200, r.text
    r = await app_client.post(f"/v1/rooms/{room}/claim",
                              json={"invite_token": fresh_room["participant_invite"], "pubkey": _b64url(mod_pub)})
    assert r.status_code == 409, r.text
    assert r.json()["detail"]["code"] == "org.agentstorming.err.already_joined"
    assert "access_token" not in r.text


@pytest.mark.asyncio
async def test_two_claims_racing_for_one_key_seat_it_once(app_client, fresh_room, monkeypatch):
    """The already_joined read ran before an insert that did nothing on
    conflict, so two claims for one unseated key both passed it and both got
    tokens for the one seat. A slow redeem lets both pass the read first."""
    room = fresh_room["room_id"]
    state = app_client._transport.app.state
    real_redeem = state.invites.redeem

    async def slow_redeem(token, pid, **kw):
        await asyncio.sleep(0.3)
        return await real_redeem(token, pid, **kw)

    monkeypatch.setattr(state.invites, "redeem", slow_redeem)
    _priv, pub = generate_keypair()
    invites = [fresh_room["participant_invite"], fresh_room["participant_invite_2"]]
    results = await asyncio.gather(*[
        app_client.post(f"/v1/rooms/{room}/claim", json={"invite_token": t, "pubkey": _b64url(pub)})
        for t in invites])
    assert sorted(r.status_code for r in results) == [200, 409], [r.text for r in results]
    loser = next(i for i, r in enumerate(results) if r.status_code == 409)
    assert results[loser].json()["detail"]["code"] == "org.agentstorming.err.already_joined"
    assert await state.invites.peek(invites[loser]) is not None, "the losing invite was spent"
    async with state.db.acquire() as conn:
        pid = results[1 - loser].json()["pid"]
        pairs = await conn.fetchval("SELECT count(*) FROM tokens WHERE room_id = $1 AND pid = $2",
                                    room, pid)
    assert pairs == 2, "one access and one refresh token, for the one claim that was seated"


@pytest.mark.asyncio
async def test_registering_a_key_a_participant_rotated_to_is_refused(app_client, fresh_public_room):
    """A rotated participant keeps its row and pid under the new key, so the
    pid read missed it. The insert, which no longer raised on the pubkey index,
    was ignored, and an interview opened for a pid with no seat."""
    room = fresh_public_room["room_id"]
    state = app_client._transport.app.state
    _priv, old_pub = generate_keypair()
    member = (await app_client.post(f"/v1/rooms/{room}/claim", json={
        "invite_token": fresh_public_room["owner_invite"], "pubkey": _b64url(old_pub)})).json()
    _priv2, new_pub = generate_keypair()
    await state.participants.set_pubkey(room, member["pid"], new_pub)
    async with state.db.acquire() as conn:
        before = await conn.fetchval("SELECT count(*) FROM interviews WHERE room_id = $1", room)
    r = await app_client.post(f"/v1/rooms/{room}/register",
                              json={"pubkey": _b64url(new_pub), "runs_as": "agent"})
    assert r.status_code == 409, r.text
    assert r.json()["detail"]["code"] == "org.agentstorming.err.already_member"
    async with state.db.acquire() as conn:
        after = await conn.fetchval("SELECT count(*) FROM interviews WHERE room_id = $1", room)
    assert after == before


@pytest.mark.asyncio
@pytest.mark.parametrize("mangle", [
    lambda s: s + "==",
    lambda s: s[:10] + "...." + s[10:],
    lambda s: s[:5] + "!!!!" + s[5:],
    lambda s: s + "\n",
], ids=["padding", "dots", "bangs", "newline"])
async def test_a_reencoded_nonce_request_signature_is_still_a_replay(owner, mangle):
    """The guard keyed on the sig string, and base64 decoding ignores surplus
    padding and characters outside the alphabet, so each re-encoding of one
    captured signature verified as a new request and got a new nonce."""
    c = owner["client"]
    headers = _nonce_headers(owner["priv"], owner["pub"])
    assert (await c.get("/v1/owner/nonce", headers=headers)).status_code == 200
    r = await c.get("/v1/owner/nonce", headers={**headers, SIG: mangle(headers[SIG])})
    assert r.status_code in (403, 409), r.text
    assert "nonce" not in r.json()
