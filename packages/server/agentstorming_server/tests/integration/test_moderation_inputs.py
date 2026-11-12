# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Moderator endpoints: malformed bodies and the §6.3 power rule.

The moderator routes read their bodies by hand. ``int()`` on a string,
``UUID()`` on a number and a duration past year 9999 each raised inside the
handler, so a typo came back as a 500. And eject, pen, mute and deputy
assignment never compared the caller's power level with the target's, so the
moderator (90) could eject, pen, mute or demote the room owner (100), which
§6.3 forbids.
"""

from __future__ import annotations

import base64
import uuid

import pytest
import pytest_asyncio

from agentstorming_server.services.sig import generate_keypair


def _b64url(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")


async def _join(client, room_id: str, invite: str, mount: str = "claim") -> dict:
    _priv, pub = generate_keypair()
    r = await client.post(f"/v1/rooms/{room_id}/{mount}",
                          json={"invite_token": invite, "pubkey": _b64url(pub)})
    assert r.status_code == 200, r.text
    return r.json()


def _auth(who: dict) -> dict:
    return {"Authorization": f"Bearer {who['access_token']}"}


@pytest_asyncio.fixture
async def seats(app_client, fresh_room):
    room = fresh_room["room_id"]
    return {
        "room": room,
        "owner": await _join(app_client, room, fresh_room["owner_invite"], "claim_owner"),
        "moderator": await _join(app_client, room, fresh_room["moderator_invite"], "claim_moderator"),
        "member": await _join(app_client, room, fresh_room["participant_invite"]),
        "member2": await _join(app_client, room, fresh_room["participant_invite_2"]),
    }


def _malformed(member_pid: str) -> list[tuple[str, dict]]:
    return [
        ("grants", {"target_pid": member_pid, "hand_id": "not-a-uuid"}),
        ("grants", {"target_pid": member_pid, "hand_id": 5}),
        ("grants", {"target_pid": member_pid, "ttl_seconds": "abc"}),
        ("grants", {"target_pid": member_pid, "ttl_seconds": [30]}),
        ("grants", {"target_pid": member_pid, "ttl_seconds": 10**12}),
        ("grants", {"target_pid": member_pid, "ttl_seconds": True}),
        ("grants", {"target_pid": {"pid": member_pid}}),
        ("grants", {"target_pid": "a\x00b"}),
        ("moderation/mute", {"target_pid": member_pid, "duration_seconds": "60"}),
        ("moderation/mute", {"target_pid": member_pid, "duration_seconds": 10**12}),
        ("moderation/mute", {"target_pid": member_pid, "duration_seconds": 1.5}),
        ("moderation/mute", {"target_pid": 7, "duration_seconds": 60}),
        ("moderation/pen", {"target_pid": member_pid, "duration_seconds": "x"}),
        ("moderation/eject", {"target_pid": ["x"]}),
        ("deputies", {"target_pid": member_pid, "rank": "one"}),
        ("deputies", {"target_pid": member_pid, "rank": 2.0}),
        (f"grants/{uuid.uuid4()}/extend", {"ttl_seconds": "x"}),
        (f"grants/{uuid.uuid4()}/extend", {"ttl_seconds": 10**12}),
    ]


@pytest.mark.asyncio
async def test_a_malformed_moderator_body_is_a_400_not_a_500(app_client, seats):
    for path, body in _malformed(seats["member"]["pid"]):
        r = await app_client.post(f"/v1/rooms/{seats['room']}/{path}", json=body,
                                  headers=_auth(seats["moderator"]))
        assert r.status_code == 400, (path, body, r.status_code, r.text)
        assert r.json()["detail"]["code"] == "org.agentstorming.err.bad_body", (path, body)


@pytest.mark.asyncio
async def test_well_formed_moderator_bodies_still_work(app_client, seats):
    room, mod, member = seats["room"], _auth(seats["moderator"]), seats["member"]["pid"]
    r = await app_client.post(f"/v1/rooms/{room}/grants", json={"target_pid": member, "hand_id": None},
                              headers=mod)
    assert r.status_code == 200, r.text
    r = await app_client.post(f"/v1/rooms/{room}/grants/{r.json()['grant_id']}/extend",
                              json={"ttl_seconds": 120}, headers=mod)
    assert r.status_code == 200, r.text
    r = await app_client.post(f"/v1/rooms/{room}/moderation/mute",
                              json={"target_pid": member, "duration_seconds": 60}, headers=mod)
    assert r.status_code == 200, r.text
    r = await app_client.post(f"/v1/rooms/{room}/deputies", json={"target_pid": member, "rank": 1},
                              headers=mod)
    assert r.status_code == 200, r.text
    r = await app_client.post(f"/v1/rooms/{room}/moderation/pen",
                              json={"target_pid": seats["member2"]["pid"], "duration_seconds": 60},
                              headers=mod)
    assert r.status_code == 200, r.text


@pytest.mark.asyncio
@pytest.mark.parametrize("path,extra", [
    ("moderation/eject", {}),
    ("moderation/pen", {"duration_seconds": 60}),
    ("moderation/mute", {"duration_seconds": 60}),
    ("deputies", {"rank": 1}),
])
async def test_the_moderator_cannot_act_on_the_room_owner(app_client, seats, path, extra):
    room, owner = seats["room"], seats["owner"]
    r = await app_client.post(f"/v1/rooms/{room}/{path}", json={"target_pid": owner["pid"], **extra},
                              headers=_auth(seats["moderator"]))
    assert r.status_code == 403, r.text
    assert r.json()["detail"]["code"] == "org.agentstorming.err.outranked"
    state = app_client._transport.app.state
    p = await state.participants.get(room, owner["pid"])
    assert p.affiliation == "room-owner" and p.deputy_rank is None and p.left_at is None
    assert not await state.mutes.is_muted(room, owner["pid"])
    r = await app_client.get(f"/v1/rooms/{room}/snapshot", headers=_auth(owner))
    assert r.status_code == 200, "the owner's tokens were not revoked"


@pytest.mark.asyncio
@pytest.mark.parametrize("path,extra", [
    ("moderation/eject", {}),
    ("moderation/pen", {"duration_seconds": 60}),
])
async def test_the_moderator_cannot_remove_itself(app_client, seats, path, extra):
    mod = seats["moderator"]
    r = await app_client.post(f"/v1/rooms/{seats['room']}/{path}", json={"target_pid": mod["pid"], **extra},
                              headers=_auth(mod))
    assert r.status_code == 403, r.text


@pytest.mark.asyncio
async def test_the_owner_can_still_eject_the_moderator(app_client, seats):
    room, mod = seats["room"], seats["moderator"]
    r = await app_client.post(f"/v1/rooms/{room}/moderation/eject", json={"target_pid": mod["pid"]},
                              headers=_auth(seats["owner"]))
    assert r.status_code == 200, r.text
    p = await app_client._transport.app.state.participants.get(room, mod["pid"])
    assert p.affiliation == "ejected"


@pytest.mark.asyncio
@pytest.mark.parametrize("path,extra", [
    ("moderation/eject", {}),
    ("moderation/pen", {"duration_seconds": 60}),
    ("moderation/mute", {"duration_seconds": 60}),
    ("deputies", {"rank": 1}),
])
async def test_acting_on_someone_not_in_the_room_is_a_404(app_client, seats, path, extra):
    r = await app_client.post(f"/v1/rooms/{seats['room']}/{path}",
                              json={"target_pid": "nobody@test-room", **extra},
                              headers=_auth(seats["moderator"]))
    assert r.status_code == 404, r.text
    assert r.json()["detail"]["code"] == "org.agentstorming.err.unknown_participant"
