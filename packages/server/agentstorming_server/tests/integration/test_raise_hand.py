# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Raise-hand flow: raise, moderator grants, participant posts."""

from __future__ import annotations

import base64
import datetime
import uuid

import pytest

from agentstorming_server.services.sig import generate_keypair, sign_envelope


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


async def _post(app_client, actor, room_id, env_type, payload):
    ts = datetime.datetime.now(datetime.timezone.utc).isoformat()
    env = {
        "id": str(uuid.uuid4()), "type": env_type, "room_id": room_id,
        "sender": actor["pid"], "ts_sender": ts, "iat": ts,
        "nonce": _b64url(uuid.uuid4().bytes),
        "reply_to": None, "mentions": [], "payload": payload,
    }
    env["sig"] = {"alg": "ed25519", "kid": _kid(actor["pub"]), "val": sign_envelope(env, actor["priv"])}
    r = await app_client.post(
        f"/v1/rooms/{room_id}/events", json=env,
        headers={"Authorization": f"Bearer {actor['access']}", "Idempotency-Key": str(uuid.uuid4())},
    )
    return r


@pytest.mark.asyncio
async def test_hand_raise_then_grant_then_post(app_client, fresh_room):
    room = fresh_room["room_id"]

    participant = await _claim(app_client, fresh_room["participant_invite"], room, "participant")
    moderator = await _claim(app_client, fresh_room["moderator_invite"], room, "moderator")

    # Participant raises hand
    r = await _post(app_client, participant, room, "org.agentstorming.hand_raised", {"hint": "I have a point"})
    assert r.status_code == 200, r.text

    # Sync as moderator and verify hand appears
    r = await app_client.get(
        f"/v1/rooms/{room}/sync?since=-1&wait=0&limit=50",
        headers={"Authorization": f"Bearer {moderator['access']}"},
    )
    events = r.json()["events"]
    assert any(e["type"] == "org.agentstorming.hand_raised" for e in events)

    # Moderator grants (not tied to hand_id for simplicity)
    r = await app_client.post(
        f"/v1/rooms/{room}/grants",
        json={"target_pid": participant["pid"], "ttl_seconds": 60},
        headers={"Authorization": f"Bearer {moderator['access']}"},
    )
    assert r.status_code == 200, r.text
    grant_id = r.json()["grant_id"]

    # Participant speaks under grant
    r = await _post(
        app_client, participant, room, "org.agentstorming.message",
        {"text": "thanks for the turn", "grant_id": grant_id},
    )
    assert r.status_code == 200, r.text
