# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Moderator-only endpoints must reject ordinary members.

Regression cover for the authorization bypass in ``require_moderator``: it
gated on ``power_level < 50`` while a plain ``member`` sits at exactly 50, so
every member in the room could grant speaking turns, mute, eject, pen,
appoint deputies, rewrite room documents, and overwrite the rolling summary.
"""

from __future__ import annotations

import base64
import uuid

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


async def _all_moderator_calls(app_client, room, actor, victim_pid):
    """Every endpoint behind require_moderator. Returns {name: status}."""
    out: dict[str, int] = {}
    r = await app_client.post(
        f"/v1/rooms/{room}/grants",
        json={"target_pid": victim_pid, "ttl_seconds": 60}, headers=_auth(actor),
    )
    out["grants"] = r.status_code
    r = await app_client.post(
        f"/v1/rooms/{room}/moderation/mute",
        json={"target_pid": victim_pid, "duration_seconds": 60}, headers=_auth(actor),
    )
    out["mute"] = r.status_code
    r = await app_client.post(
        f"/v1/rooms/{room}/deputies",
        json={"target_pid": victim_pid, "rank": 3}, headers=_auth(actor),
    )
    out["deputies"] = r.status_code
    r = await app_client.put(
        f"/v1/rooms/{room}/summary", json={"text": "written by the wrong party"},
        headers=_auth(actor),
    )
    out["summary"] = r.status_code
    r = await app_client.post(
        f"/v1/rooms/{room}/documents",
        json={"type": "reference", "title": "t", "body": "b"}, headers=_auth(actor),
    )
    out["documents"] = r.status_code
    r = await app_client.patch(
        f"/v1/rooms/{room}/config", json={"raise_hand_required": True},
        headers=_auth(actor),
    )
    out["config"] = r.status_code
    r = await app_client.post(
        f"/v1/rooms/{room}/moderation/eject",
        json={"target_pid": victim_pid}, headers=_auth(actor),
    )
    out["eject"] = r.status_code
    r = await app_client.post(
        f"/v1/rooms/{room}/moderation/pen",
        json={"target_pid": victim_pid, "duration_seconds": 60}, headers=_auth(actor),
    )
    out["pen"] = r.status_code
    return out


@pytest.mark.asyncio
async def test_plain_member_cannot_use_any_moderator_endpoint(app_client, fresh_room):
    room = fresh_room["room_id"]
    member = await _claim(app_client, fresh_room["participant_invite"], room, "participant")
    victim = await _claim(app_client, fresh_room["participant_invite_2"], room, "participant")
    # Seat a real moderator so the room is in its normal configuration and
    # the member is not merely the highest-ranked participant present.
    await _claim(app_client, fresh_room["moderator_invite"], room, "moderator")

    results = await _all_moderator_calls(app_client, room, member, victim["pid"])
    assert all(s == 403 for s in results.values()), results


@pytest.mark.asyncio
async def test_member_is_denied_even_with_no_moderator_seated(app_client, fresh_room):
    """A vacant seat must not promote whoever happens to be in the room."""
    room = fresh_room["room_id"]
    member = await _claim(app_client, fresh_room["participant_invite"], room, "participant")
    victim = await _claim(app_client, fresh_room["participant_invite_2"], room, "participant")

    results = await _all_moderator_calls(app_client, room, member, victim["pid"])
    assert all(s == 403 for s in results.values()), results


@pytest.mark.asyncio
async def test_moderator_is_allowed(app_client, fresh_room):
    room = fresh_room["room_id"]
    moderator = await _claim(app_client, fresh_room["moderator_invite"], room, "moderator")
    victim = await _claim(app_client, fresh_room["participant_invite"], room, "participant")

    r = await app_client.post(
        f"/v1/rooms/{room}/grants",
        json={"target_pid": victim["pid"], "ttl_seconds": 60}, headers=_auth(moderator),
    )
    assert r.status_code == 200, r.text
    r = await app_client.put(
        f"/v1/rooms/{room}/summary", json={"text": "moderator summary"},
        headers=_auth(moderator),
    )
    assert r.status_code in (200, 201), r.text
    r = await app_client.post(
        f"/v1/rooms/{room}/moderation/mute",
        json={"target_pid": victim["pid"], "duration_seconds": 60},
        headers=_auth(moderator),
    )
    assert r.status_code == 200, r.text


@pytest.mark.asyncio
async def test_room_owner_is_allowed(app_client, fresh_room):
    """§13.1 — the owner is the room superuser and outranks the moderator."""
    room = fresh_room["room_id"]
    await _claim(app_client, fresh_room["moderator_invite"], room, "moderator")
    owner = await _claim(app_client, fresh_room["owner_invite"], room, "owner")
    victim = await _claim(app_client, fresh_room["participant_invite"], room, "participant")

    r = await app_client.post(
        f"/v1/rooms/{room}/grants",
        json={"target_pid": victim["pid"], "ttl_seconds": 60}, headers=_auth(owner),
    )
    assert r.status_code == 200, r.text


@pytest.mark.asyncio
async def test_deputy_in_succession_line_is_not_a_moderator(app_client, fresh_room):
    """A deputy only moderates once the seat has actually passed to them.

    §12.4 reserves deputy assignment for "the current moderator" and §6.4
    permits exactly one holder of the ``moderating`` role, so being next in
    line must not confer moderator authority while the original-moderator is
    still seated.
    """
    room = fresh_room["room_id"]
    moderator = await _claim(app_client, fresh_room["moderator_invite"], room, "moderator")
    deputy = await _claim(app_client, fresh_room["participant_invite"], room, "participant")
    victim = await _claim(app_client, fresh_room["participant_invite_2"], room, "participant")

    r = await app_client.post(
        f"/v1/rooms/{room}/deputies",
        json={"target_pid": deputy["pid"], "rank": 1}, headers=_auth(moderator),
    )
    assert r.status_code == 200, r.text

    results = await _all_moderator_calls(app_client, room, deputy, victim["pid"])
    assert all(s == 403 for s in results.values()), results


@pytest.mark.asyncio
async def test_deputy_moderates_once_the_seat_is_vacant(app_client, fresh_room):
    """After the original-moderator leaves, rank-1 holds the seat (§12.5)."""
    room = fresh_room["room_id"]
    moderator = await _claim(app_client, fresh_room["moderator_invite"], room, "moderator")
    deputy = await _claim(app_client, fresh_room["participant_invite"], room, "participant")
    victim = await _claim(app_client, fresh_room["participant_invite_2"], room, "participant")

    r = await app_client.post(
        f"/v1/rooms/{room}/deputies",
        json={"target_pid": deputy["pid"], "rank": 1}, headers=_auth(moderator),
    )
    assert r.status_code == 200, r.text

    # The moderator departs; the deputy inherits the moderating role.
    from .conftest_helpers import set_left
    await set_left(room, moderator["pid"])

    r = await app_client.post(
        f"/v1/rooms/{room}/grants",
        json={"target_pid": victim["pid"], "ttl_seconds": 60}, headers=_auth(deputy),
    )
    assert r.status_code == 200, r.text
