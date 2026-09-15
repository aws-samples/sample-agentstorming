# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Stage-13 planning mode: a room config field, and an auditable promotion.

Before this, ``org.agentstorming.mode_promoted`` existed only as a string
constant: there was no ``mode`` on RoomConfig, nothing to promote, and
nothing in the running server changed. The event was a record of an
intention, not a mechanism.
"""

from __future__ import annotations

import base64

import pytest

from agentstorming_server.services.sig import generate_keypair


def _b64url(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")


async def _claim(app_client, invite, room_id, kind="participant"):
    priv, pub = generate_keypair()
    path = {"participant": "claim", "moderator": "claim_moderator", "owner": "claim_owner"}[kind]
    r = await app_client.post(
        f"/v1/rooms/{room_id}/{path}",
        json={"invite_token": invite, "pubkey": _b64url(pub)},
    )
    assert r.status_code in (200, 201), r.text
    b = r.json()
    return {"priv": priv, "pub": pub, "pid": b["pid"], "access": b["access_token"]}


def _auth(actor):
    return {"Authorization": f"Bearer {actor['access']}"}


@pytest.mark.asyncio
async def test_planning_mode_is_visible_in_the_snapshot(app_client, fresh_planning_room):
    room = fresh_planning_room["room_id"]
    actor = await _claim(app_client, fresh_planning_room["participant_invite"], room)
    r = await app_client.get(f"/v1/rooms/{room}/snapshot", headers=_auth(actor))
    assert r.status_code == 200, r.text
    assert r.json()["payload"]["config"]["mode"] == "planning"


@pytest.mark.asyncio
async def test_default_room_is_active(app_client, fresh_room):
    """Adding the field must not silently restrict pre-existing rooms."""
    room = fresh_room["room_id"]
    actor = await _claim(app_client, fresh_room["participant_invite"], room)
    r = await app_client.get(f"/v1/rooms/{room}/snapshot", headers=_auth(actor))
    assert r.json()["payload"]["config"]["mode"] == "active"


@pytest.mark.asyncio
async def test_member_cannot_promote(app_client, fresh_planning_room):
    room = fresh_planning_room["room_id"]
    await _claim(app_client, fresh_planning_room["moderator_invite"], room, "moderator")
    member = await _claim(app_client, fresh_planning_room["participant_invite"], room)
    r = await app_client.post(
        f"/v1/rooms/{room}/moderation/mode", json={"mode": "active"},
        headers=_auth(member),
    )
    assert r.status_code == 403, r.text


@pytest.mark.asyncio
async def test_moderator_promotes_and_the_transition_is_auditable(
    app_client, fresh_planning_room
):
    room = fresh_planning_room["room_id"]
    moderator = await _claim(
        app_client, fresh_planning_room["moderator_invite"], room, "moderator"
    )

    r = await app_client.post(
        f"/v1/rooms/{room}/moderation/mode", json={"mode": "active"},
        headers=_auth(moderator),
    )
    assert r.status_code == 200, r.text
    assert r.json() == {"room_id": room, "mode": "active", "changed": True}

    # The running server's own config now reflects it...
    r = await app_client.get(f"/v1/rooms/{room}/snapshot", headers=_auth(moderator))
    assert r.json()["payload"]["config"]["mode"] == "active"

    # ...and the room has a signed record of who promoted it.
    r = await app_client.get(
        f"/v1/rooms/{room}/sync?since=-1&wait=0&limit=100", headers=_auth(moderator),
    )
    promos = [e for e in r.json()["events"]
              if e["type"] == "org.agentstorming.mode_promoted"]
    assert len(promos) == 1, promos
    assert promos[0]["payload"] == {
        "from_mode": "planning", "to_mode": "active",
        "promoted_by": moderator["pid"],
    }
    assert promos[0]["sender"] == "system"
    assert promos[0]["sig"]["val"]


@pytest.mark.asyncio
async def test_promoting_an_active_room_is_a_no_op(app_client, fresh_room):
    room = fresh_room["room_id"]
    moderator = await _claim(app_client, fresh_room["moderator_invite"], room, "moderator")
    r = await app_client.post(
        f"/v1/rooms/{room}/moderation/mode", json={"mode": "active"},
        headers=_auth(moderator),
    )
    assert r.status_code == 200, r.text
    assert r.json()["changed"] is False


@pytest.mark.asyncio
async def test_moderator_cannot_demote(app_client, fresh_room):
    """Re-arming the restriction is an owner action, not a moderator one."""
    room = fresh_room["room_id"]
    moderator = await _claim(app_client, fresh_room["moderator_invite"], room, "moderator")
    r = await app_client.post(
        f"/v1/rooms/{room}/moderation/mode", json={"mode": "planning"},
        headers=_auth(moderator),
    )
    assert r.status_code == 400, r.text
