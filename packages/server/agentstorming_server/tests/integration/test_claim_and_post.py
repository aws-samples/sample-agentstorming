# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""End-to-end: claim participant/moderator, post a message, sync, receive."""

from __future__ import annotations

import asyncio
import base64
import json
import uuid

import pytest

from agentstorming_server.services.sig import generate_keypair, sign_envelope


def _b64url(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")


def _kid(pub: bytes) -> str:
    import hashlib
    return hashlib.sha256(pub).hexdigest()[:16]


@pytest.mark.asyncio
async def test_participant_claim_and_post(app_client, fresh_room):
    # Participant keypair
    priv, pub = generate_keypair()
    r = await app_client.post(
        f"/v1/rooms/{fresh_room['room_id']}/claim",
        json={"invite_token": fresh_room["participant_invite"], "pubkey": _b64url(pub)},
    )
    assert r.status_code in (200, 201), r.text
    body = r.json()
    pid = body["pid"]
    access = body["access_token"]

    # Post a message
    import datetime
    ts = datetime.datetime.now(datetime.timezone.utc).isoformat()
    env = {
        "id": str(uuid.uuid4()),
        "type": "org.agentstorming.message",
        "room_id": fresh_room["room_id"],
        "sender": pid,
        "ts_sender": ts, "iat": ts,
        "nonce": _b64url(uuid.uuid4().bytes),
        "reply_to": None, "mentions": [],
        "payload": {"text": "hello room"},
    }
    val = sign_envelope(env, priv)
    env["sig"] = {"alg": "ed25519", "kid": _kid(pub), "val": val}

    r = await app_client.post(
        f"/v1/rooms/{fresh_room['room_id']}/events", json=env,
        headers={"Authorization": f"Bearer {access}", "Idempotency-Key": str(uuid.uuid4())},
    )
    assert r.status_code == 200, r.text
    committed = r.json()["envelope"]
    assert committed["seq"] >= 0
    assert committed["ts_server"]

    # Sync from start; should include snapshot + join + message
    r = await app_client.get(
        f"/v1/rooms/{fresh_room['room_id']}/sync?since=-1&wait=0&limit=50",
        headers={"Authorization": f"Bearer {access}"},
    )
    assert r.status_code == 200, r.text
    events = r.json()["events"]
    types = [e["type"] for e in events]
    assert "org.agentstorming.metadata_snapshot" in types
    assert "org.agentstorming.participant_joined" in types
    assert "org.agentstorming.message" in types


@pytest.mark.asyncio
async def test_replay_rejected(app_client, fresh_room):
    priv, pub = generate_keypair()
    r = await app_client.post(
        f"/v1/rooms/{fresh_room['room_id']}/claim",
        json={"invite_token": fresh_room["participant_invite"], "pubkey": _b64url(pub)},
    )
    assert r.status_code in (200, 201)
    body = r.json()
    pid, access = body["pid"], body["access_token"]

    import datetime
    ts = datetime.datetime.now(datetime.timezone.utc).isoformat()
    env = {
        "id": str(uuid.uuid4()),
        "type": "org.agentstorming.message",
        "room_id": fresh_room["room_id"],
        "sender": pid, "ts_sender": ts, "iat": ts,
        "nonce": "FIXED-NONCE-" + "A" * 10,
        "reply_to": None, "mentions": [],
        "payload": {"text": "first"},
    }
    env["sig"] = {"alg": "ed25519", "kid": _kid(pub), "val": sign_envelope(env, priv)}

    r = await app_client.post(
        f"/v1/rooms/{fresh_room['room_id']}/events", json=env,
        headers={"Authorization": f"Bearer {access}", "Idempotency-Key": str(uuid.uuid4())},
    )
    assert r.status_code == 200

    # Same nonce again, new envelope id — should be rejected as replay
    env["id"] = str(uuid.uuid4())
    env["payload"] = {"text": "second"}
    env["sig"] = {"alg": "ed25519", "kid": _kid(pub), "val": sign_envelope(env, priv)}
    r = await app_client.post(
        f"/v1/rooms/{fresh_room['room_id']}/events", json=env,
        headers={"Authorization": f"Bearer {access}", "Idempotency-Key": str(uuid.uuid4())},
    )
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "org.agentstorming.err.replay"


@pytest.mark.asyncio
async def test_signature_rejected(app_client, fresh_room):
    priv, pub = generate_keypair()
    r = await app_client.post(
        f"/v1/rooms/{fresh_room['room_id']}/claim",
        json={"invite_token": fresh_room["participant_invite"], "pubkey": _b64url(pub)},
    )
    body = r.json()
    pid, access = body["pid"], body["access_token"]
    import datetime
    ts = datetime.datetime.now(datetime.timezone.utc).isoformat()
    env = {
        "id": str(uuid.uuid4()),
        "type": "org.agentstorming.message",
        "room_id": fresh_room["room_id"],
        "sender": pid, "ts_sender": ts, "iat": ts,
        "nonce": _b64url(uuid.uuid4().bytes),
        "reply_to": None, "mentions": [],
        "payload": {"text": "hi"},
        "sig": {"alg": "ed25519", "kid": "badkid", "val": "AA" * 43},  # invalid
    }
    r = await app_client.post(
        f"/v1/rooms/{fresh_room['room_id']}/events", json=env,
        headers={"Authorization": f"Bearer {access}", "Idempotency-Key": str(uuid.uuid4())},
    )
    assert r.status_code == 400
    assert "signature_invalid" in r.json()["detail"]["code"]
