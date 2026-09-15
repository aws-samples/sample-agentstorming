# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Public registration end to end (§9.5): knock → interview → accept → act.

Two defects are covered here:

1. ``registration_request`` is whisper-class, but its payload carried no
   routing field, so the filter in ``services/visibility.py`` hid it from
   every viewer — including the moderator who is supposed to act on it.
2. Acceptance flipped the candidate's affiliation to ``member`` but minted
   no tokens, and every other endpoint requires a bearer token. An accepted
   member could not speak, whisper, or even read the room.
"""

from __future__ import annotations

import base64
import datetime
import uuid

import pytest

from agentstorming_server.services.jcs import canonicalise
from agentstorming_server.services.sig import generate_keypair, sign_blob, sign_envelope


def _b64url(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")


def _kid(pub: bytes) -> str:
    import hashlib
    return hashlib.sha256(pub).hexdigest()[:16]


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


async def _claim(app_client, invite, room_id, kind="moderator"):
    priv, pub = generate_keypair()
    path = {"participant": "claim", "moderator": "claim_moderator", "owner": "claim_owner"}[kind]
    r = await app_client.post(
        f"/v1/rooms/{room_id}/{path}",
        json={"invite_token": invite, "pubkey": _b64url(pub)},
    )
    assert r.status_code in (200, 201), r.text
    b = r.json()
    return {"priv": priv, "pub": pub, "pid": b["pid"], "access": b["access_token"]}


async def _register(app_client, room_id, declared="I can verify chemistry claims"):
    priv, pub = generate_keypair()
    r = await app_client.post(
        f"/v1/rooms/{room_id}/register",
        json={"pubkey": _b64url(pub), "runs_as": "agent", "declared": declared},
    )
    assert r.status_code == 200, r.text
    b = r.json()
    return {"priv": priv, "pub": pub, "pid": b["candidate_pid"],
            "interview_id": b["interview_id"]}


async def _collect_credentials(app_client, room_id, candidate):
    """Run the candidate's signed credential exchange. Returns the response."""
    r = await app_client.get(f"/v1/rooms/{room_id}/register/nonce")
    assert r.status_code == 200, r.text
    nonce = r.json()["nonce"]
    body = {
        "interview_id": candidate["interview_id"],
        "nonce": nonce,
        "ts": _now(),
    }
    sig = sign_blob(canonicalise(body), candidate["priv"])
    return await app_client.post(
        f"/v1/rooms/{room_id}/register/credentials",
        json={**body, "pubkey": _b64url(candidate["pub"]), "sig": sig},
    )


async def _sync(app_client, actor, room_id):
    r = await app_client.get(
        f"/v1/rooms/{room_id}/sync?since=-1&wait=0&limit=100",
        headers={"Authorization": f"Bearer {actor['access']}"},
    )
    assert r.status_code == 200, r.text
    return r.json()["events"]


@pytest.mark.asyncio
async def test_moderator_sees_the_registration_request(app_client, fresh_public_room):
    room = fresh_public_room["room_id"]
    moderator = await _claim(app_client, fresh_public_room["moderator_invite"], room)
    candidate = await _register(app_client, room)

    events = await _sync(app_client, moderator, room)
    knocks = [e for e in events if e["type"] == "org.agentstorming.registration_request"]
    assert knocks, "moderator cannot see the knock it is supposed to act on"
    assert knocks[0]["payload"]["candidate_pid"] == candidate["pid"]
    assert knocks[0]["payload"]["target_pid"] == moderator["pid"]


@pytest.mark.asyncio
async def test_third_party_does_not_see_the_registration_request(
    app_client, fresh_public_room
):
    """Routing the knock must not turn it into a broadcast."""
    room = fresh_public_room["room_id"]
    moderator = await _claim(app_client, fresh_public_room["moderator_invite"], room)
    owner = await _claim(app_client, fresh_public_room["owner_invite"], room, "owner")
    await _register(app_client, room)

    # The owner is a third party to this interview: the knock was routed to
    # the seated moderator, so the owner must not receive it.
    events = await _sync(app_client, owner, room)
    assert not [e for e in events if e["type"] == "org.agentstorming.registration_request"]


@pytest.mark.asyncio
async def test_accepted_candidate_receives_tokens_and_can_post(
    app_client, fresh_public_room
):
    room = fresh_public_room["room_id"]
    moderator = await _claim(app_client, fresh_public_room["moderator_invite"], room)
    candidate = await _register(app_client, room)

    # Before a decision: the candidate may poll and is told to wait.
    r = await _collect_credentials(app_client, room, candidate)
    assert r.status_code == 409, r.text
    assert r.json()["detail"]["code"] == "org.agentstorming.err.interview_pending"

    r = await app_client.post(
        f"/v1/rooms/{room}/moderation/registrations/{candidate['interview_id']}/accept",
        json={"interview_id": candidate["interview_id"]},
        headers={"Authorization": f"Bearer {moderator['access']}"},
    )
    assert r.status_code == 200, r.text

    r = await _collect_credentials(app_client, room, candidate)
    assert r.status_code == 200, r.text
    creds = r.json()
    assert creds["pid"] == candidate["pid"]
    assert creds["affiliation"] == "member"
    assert creds["access_token"] and creds["refresh_token"]
    assert creds["snapshot"]["type"] == "org.agentstorming.metadata_snapshot"

    # The whole point: the admitted member can now actually act.
    ts = _now()
    env = {
        "id": str(uuid.uuid4()), "type": "org.agentstorming.message",
        "room_id": room, "sender": candidate["pid"], "ts_sender": ts, "iat": ts,
        "nonce": _b64url(uuid.uuid4().bytes), "reply_to": None, "mentions": [],
        "payload": {"text": "admitted, and able to speak"},
    }
    env["sig"] = {"alg": "ed25519", "kid": _kid(candidate["pub"]),
                  "val": sign_envelope(env, candidate["priv"])}
    r = await app_client.post(
        f"/v1/rooms/{room}/events", json=env,
        headers={"Authorization": f"Bearer {creds['access_token']}",
                 "Idempotency-Key": str(uuid.uuid4())},
    )
    assert r.status_code == 200, r.text


@pytest.mark.asyncio
async def test_rejected_candidate_gets_no_tokens(app_client, fresh_public_room):
    room = fresh_public_room["room_id"]
    moderator = await _claim(app_client, fresh_public_room["moderator_invite"], room)
    candidate = await _register(app_client, room)

    r = await app_client.post(
        f"/v1/rooms/{room}/moderation/registrations/{candidate['interview_id']}/reject",
        json={"interview_id": candidate["interview_id"], "reason": "off-topic"},
        headers={"Authorization": f"Bearer {moderator['access']}"},
    )
    assert r.status_code == 200, r.text

    r = await _collect_credentials(app_client, room, candidate)
    assert r.status_code == 403, r.text


@pytest.mark.asyncio
async def test_credentials_require_the_candidates_own_key(app_client, fresh_public_room):
    """An impostor with a valid keypair cannot collect someone else's tokens."""
    room = fresh_public_room["room_id"]
    moderator = await _claim(app_client, fresh_public_room["moderator_invite"], room)
    candidate = await _register(app_client, room)
    await app_client.post(
        f"/v1/rooms/{room}/moderation/registrations/{candidate['interview_id']}/accept",
        json={"interview_id": candidate["interview_id"]},
        headers={"Authorization": f"Bearer {moderator['access']}"},
    )

    impostor_priv, impostor_pub = generate_keypair()
    r = await app_client.get(f"/v1/rooms/{room}/register/nonce")
    body = {"interview_id": candidate["interview_id"], "nonce": r.json()["nonce"],
            "ts": _now()}
    sig = sign_blob(canonicalise(body), impostor_priv)
    r = await app_client.post(
        f"/v1/rooms/{room}/register/credentials",
        json={**body, "pubkey": _b64url(impostor_pub), "sig": sig},
    )
    assert r.status_code == 403, r.text
    assert r.json()["detail"]["code"] == "org.agentstorming.err.forbidden"


@pytest.mark.asyncio
async def test_credential_nonce_is_single_use(app_client, fresh_public_room):
    room = fresh_public_room["room_id"]
    moderator = await _claim(app_client, fresh_public_room["moderator_invite"], room)
    candidate = await _register(app_client, room)
    await app_client.post(
        f"/v1/rooms/{room}/moderation/registrations/{candidate['interview_id']}/accept",
        json={"interview_id": candidate["interview_id"]},
        headers={"Authorization": f"Bearer {moderator['access']}"},
    )

    r = await app_client.get(f"/v1/rooms/{room}/register/nonce")
    nonce = r.json()["nonce"]
    body = {"interview_id": candidate["interview_id"], "nonce": nonce, "ts": _now()}
    payload = {**body, "pubkey": _b64url(candidate["pub"]),
               "sig": sign_blob(canonicalise(body), candidate["priv"])}

    r1 = await app_client.post(f"/v1/rooms/{room}/register/credentials", json=payload)
    assert r1.status_code == 200, r1.text
    r2 = await app_client.post(f"/v1/rooms/{room}/register/credentials", json=payload)
    assert r2.status_code == 409, r2.text
    assert r2.json()["detail"]["code"] == "org.agentstorming.err.nonce_invalid"


@pytest.mark.asyncio
async def test_credentials_reject_a_forged_signature(app_client, fresh_public_room):
    room = fresh_public_room["room_id"]
    candidate = await _register(app_client, room)
    r = await app_client.get(f"/v1/rooms/{room}/register/nonce")
    body = {"interview_id": candidate["interview_id"], "nonce": r.json()["nonce"],
            "ts": _now()}
    r = await app_client.post(
        f"/v1/rooms/{room}/register/credentials",
        json={**body, "pubkey": _b64url(candidate["pub"]),
              "sig": _b64url(b"\x00" * 64)},
    )
    assert r.status_code == 403, r.text
    assert r.json()["detail"]["code"] == "org.agentstorming.err.sig_invalid"
