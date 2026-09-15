# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""A deputy holding the moderating seat has the seat's authority.

ADR-006 removed a ``power_level`` threshold from ``require_moderator`` that was
too *permissive*: a member sits at exactly 50, so ``< 50`` admitted everyone.
Two thresholds survived in ``PostEventService`` that were too *restrictive*, and
they are the mirror image of the same mistake:

    if principal.participant.power_level < 80:   # "moderator + owner bypass"

Deputy power levels are ``80 - deputy_rank``, so **every** deputy sits below 80.
Once the moderating seat passes to a deputy, that test denied the acting
moderator. For freeze the failure is self-defeating: succession to a deputy is
what happens when the freeze TTL elapses, so the one participant who needs to
act in a frozen room was the one locked out of it.

These tests pin the behaviour by seat identity, so that reintroducing any
numeric stand-in fails here.
"""

from __future__ import annotations

import base64
import datetime
import uuid

import pytest

from agentstorming_server.repo.base import Database
from agentstorming_server.repo.participants import ParticipantRepo
from agentstorming_server.repo.rooms import RoomRepo
from agentstorming_server.services.sig import generate_keypair, sign_envelope

from ..conftest import DSN


def _b64url(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")


def _kid(pub: bytes) -> str:
    import hashlib
    return hashlib.sha256(pub).hexdigest()[:16]


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


async def _post_message(app_client, actor, room_id, text, grant_id=None):
    ts = datetime.datetime.now(datetime.timezone.utc).isoformat()
    payload: dict = {"text": text}
    if grant_id is not None:
        payload["grant_id"] = grant_id
    env = {
        "id": str(uuid.uuid4()), "type": "org.agentstorming.message",
        "room_id": room_id, "sender": actor["pid"], "ts_sender": ts, "iat": ts,
        "nonce": _b64url(uuid.uuid4().bytes),
        "reply_to": None, "mentions": [], "payload": payload,
    }
    env["sig"] = {"alg": "ed25519", "kid": _kid(actor["pub"]),
                  "val": sign_envelope(env, actor["priv"])}
    return await app_client.post(
        f"/v1/rooms/{room_id}/events", json=env,
        headers={"Authorization": f"Bearer {actor['access']}",
                 "Idempotency-Key": str(uuid.uuid4())},
    )


async def _seat_deputy_as_moderator(room_id: str, deputy_pid: str, moderator_pid: str) -> None:
    """Make `deputy_pid` the acting moderator: rank it, and unseat the incumbent.

    Goes through the repository rather than the API because the point here is
    the *state* — a deputy holding the seat — not the transition that produced
    it, which has its own tests.
    """
    db = Database(DSN)
    await db.connect()
    try:
        parts = ParticipantRepo(db)
        # A deputy is a member carrying a deputy_rank (power_level 80 - rank).
        await parts.set_affiliation(room_id, deputy_pid, "member", deputy_rank=1)
        # Unseat the incumbent so the deputy actually holds the seat, which is
        # what get_acting_moderator resolves.
        await parts.set_affiliation(room_id, moderator_pid, "member")
    finally:
        await db.close()


async def _freeze(room_id: str) -> None:
    db = Database(DSN)
    await db.connect()
    try:
        await RoomRepo(db).set_state(room_id, "FROZEN")
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_acting_deputy_moderator_may_post_in_a_frozen_room(app_client, fresh_room):
    """The regression that mattered: succession + freeze locked out the moderator."""
    room = fresh_room["room_id"]
    moderator = await _claim(app_client, fresh_room["moderator_invite"], room, "moderator")
    deputy = await _claim(app_client, fresh_room["participant_invite"], room, "participant")

    await _seat_deputy_as_moderator(room, deputy["pid"], moderator["pid"])
    await _freeze(room)

    r = await _post_message(app_client, deputy, room, "unfreezing this room")
    assert r.status_code in (200, 201), (
        "the acting moderator was refused in a frozen room — power_level < 80 is "
        f"back: {r.status_code} {r.text}"
    )


@pytest.mark.asyncio
async def test_ordinary_member_still_cannot_post_in_a_frozen_room(app_client, fresh_room):
    """The permissive direction must stay closed."""
    room = fresh_room["room_id"]
    await _claim(app_client, fresh_room["moderator_invite"], room, "moderator")
    member = await _claim(app_client, fresh_room["participant_invite"], room, "participant")

    await _freeze(room)

    r = await _post_message(app_client, member, room, "I should not get through")
    assert r.status_code == 409, r.text
    assert "room_frozen" in r.text


@pytest.mark.asyncio
async def test_room_owner_may_post_in_a_frozen_room(app_client, fresh_room):
    room = fresh_room["room_id"]
    owner = await _claim(app_client, fresh_room["owner_invite"], room, "owner")

    await _freeze(room)

    r = await _post_message(app_client, owner, room, "owner override")
    assert r.status_code in (200, 201), r.text
