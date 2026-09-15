# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""PUT /documents/{id} replaces, it does not merge (§14.2).

`DocumentRepo.update` was rewritten from a dynamically-assembled SET list to a
fixed statement so SQL-injection linters stop flagging it. The obvious way to
write that rewrite — `COALESCE($n, column)` — silently converts the PUT into a
PATCH and makes clearing a nullable field impossible. These tests pin the
semantics so the next person to tidy that query finds out immediately.
"""

from __future__ import annotations

import base64

import pytest

from agentstorming_server.services.sig import generate_keypair


def _b64url(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")


async def _moderator(app_client, fresh_room):
    priv, pub = generate_keypair()
    r = await app_client.post(
        f"/v1/rooms/{fresh_room['room_id']}/claim_moderator",
        json={"invite_token": fresh_room["moderator_invite"], "pubkey": _b64url(pub)},
    )
    assert r.status_code in (200, 201), r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.mark.asyncio
async def test_put_replaces_and_can_clear_a_nullable_field(app_client, fresh_room):
    room = fresh_room["room_id"]
    auth = await _moderator(app_client, fresh_room)

    r = await app_client.post(
        f"/v1/rooms/{room}/documents",
        json={"type": "problem_statement", "title": "Brief",
              "body": "the original body", "attachment_ref": "s3://bucket/key"},
        headers=auth,
    )
    assert r.status_code in (200, 201), r.text
    doc_id = r.json()["id"]

    # A PUT that omits body/attachment_ref is asking for them to be cleared.
    r = await app_client.put(
        f"/v1/rooms/{room}/documents/{doc_id}",
        json={"type": "problem_statement", "title": "Brief v2"},
        headers=auth,
    )
    assert r.status_code in (200, 201), r.text
    doc = r.json()
    assert doc["title"] == "Brief v2"
    assert not doc.get("body"), f"body should have been cleared, got {doc.get('body')!r}"
    assert not doc.get("attachment_ref"), "attachment_ref should have been cleared"


@pytest.mark.asyncio
async def test_put_updates_every_supplied_field(app_client, fresh_room):
    room = fresh_room["room_id"]
    auth = await _moderator(app_client, fresh_room)

    r = await app_client.post(
        f"/v1/rooms/{room}/documents",
        json={"type": "reference", "title": "First", "body": "one"},
        headers=auth,
    )
    doc_id = r.json()["id"]

    r = await app_client.put(
        f"/v1/rooms/{room}/documents/{doc_id}",
        json={"type": "reference", "title": "Second", "body": "two",
              "content_type": "text/plain"},
        headers=auth,
    )
    assert r.status_code in (200, 201), r.text
    doc = r.json()
    assert doc["title"] == "Second"
    assert doc["body"] == "two"
    assert doc["content_type"] == "text/plain"


@pytest.mark.asyncio
async def test_a_member_cannot_update_a_document(app_client, fresh_room):
    """Document mutation is moderator-only (§14.2, §6.3.1)."""
    room = fresh_room["room_id"]
    auth = await _moderator(app_client, fresh_room)
    r = await app_client.post(
        f"/v1/rooms/{room}/documents",
        json={"type": "reference", "title": "Owned by the moderator"},
        headers=auth,
    )
    doc_id = r.json()["id"]

    priv, pub = generate_keypair()
    r = await app_client.post(
        f"/v1/rooms/{room}/claim",
        json={"invite_token": fresh_room["participant_invite"], "pubkey": _b64url(pub)},
    )
    member_auth = {"Authorization": f"Bearer {r.json()['access_token']}"}

    r = await app_client.put(
        f"/v1/rooms/{room}/documents/{doc_id}",
        json={"type": "reference", "title": "rewritten by a member"},
        headers=member_auth,
    )
    assert r.status_code == 403, r.text
