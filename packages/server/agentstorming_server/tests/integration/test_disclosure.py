# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Unauthenticated routes say no more than they must.

Two disclosures found while sweeping every route after the unsigned owner
reads were reported:

- ``/readyz`` returned the database exception text, which names the host and
  user, to anyone, and returned it with HTTP 200, so the container
  HEALTHCHECK polling the route never saw a failure.
- ``POST /v1/rooms/{id}/register`` answered a private room with a distinct
  ``room_private`` 403, confirming that a private room by that name exists.
"""

from __future__ import annotations

import base64
import os

import httpx
import pytest

from agentstorming_server.app import create_app
from agentstorming_server.config import Settings
from agentstorming_server.services.sig import generate_keypair

from ..conftest import DSN


def _b64url(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")


@pytest.mark.asyncio
async def test_readyz_reports_failure_as_503_without_the_error(reset_db, tmp_path):
    os.environ["AGENTSTORMING_DSN"] = DSN
    app = create_app(Settings(dsn=DSN, attachments_local_dir=tmp_path / "attachments"))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as client:
        async with app.router.lifespan_context(app):
            assert (await client.get("/readyz")).json() == {"ok": True}

            db_error = "role storm_admin refused by db-7.internal at 10.0.4.17"

            def broken_acquire():
                raise RuntimeError(db_error)

            app.state.db.acquire = broken_acquire
            r = await client.get("/readyz")
            assert r.status_code == 503
            assert r.json() == {"ok": False}
            assert "storm_admin" not in r.text and "10.0.4.17" not in r.text


@pytest.mark.asyncio
async def test_register_answers_a_private_room_exactly_like_a_missing_one(app_client, fresh_room):
    _priv, pub = generate_keypair()
    body = {"pubkey": _b64url(pub), "runs_as": "agent", "declared": "probe"}

    private = await app_client.post(f"/v1/rooms/{fresh_room['room_id']}/register", json=body)
    missing = await app_client.post("/v1/rooms/no-such-room/register", json=body)

    assert private.status_code == missing.status_code == 404
    assert private.json() == missing.json()


async def _fill_room(room_id: str) -> None:
    """Shrink the room to one seat and take it, so it is full."""
    from agentstorming_server.domain.room import RoomConfig
    from agentstorming_server.repo.base import Database
    from agentstorming_server.repo.rooms import RoomRepo
    db = Database(DSN)
    await db.connect()
    try:
        rooms = RoomRepo(db)
        room = await rooms.get(room_id)
        cfg = room.config.to_json()
        cfg["max_participants"] = 1
        await rooms.update_config(room_id, RoomConfig.from_json(cfg))
    finally:
        await db.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("mount", ["/v1/rooms/{room}/claim", "/v1/rooms/{room}/claim_moderator",
                                   "/v1/rooms/{room}/claim_owner", "/v1/{room}/claim"])
async def test_a_junk_invite_says_nothing_about_a_full_private_room(app_client, fresh_room, mount):
    """A full private room answered ``room_full`` with its capacity, a missing
    room ``invite_invalid``, so a junk token told the two apart."""
    room = fresh_room["room_id"]
    _priv, pub = generate_keypair()
    r = await app_client.post(f"/v1/rooms/{room}/claim",
                              json={"invite_token": fresh_room["participant_invite"], "pubkey": _b64url(pub)})
    assert r.status_code == 200, r.text
    await _fill_room(room)

    _priv, pub = generate_keypair()
    junk = "not-a-real-invite"
    body = {"invite_token": junk, "pubkey": _b64url(pub)}
    full = await app_client.post(mount.format(room=room), json=body)
    missing = await app_client.post(mount.format(room="no-such-room"), json=body)
    assert full.status_code == missing.status_code == 409, (full.text, missing.text)
    assert full.json() == missing.json()
    assert full.json()["detail"]["code"] == "org.agentstorming.err.invite_invalid"


@pytest.mark.asyncio
async def test_a_valid_invite_at_the_wrong_room_is_not_burned(app_client, fresh_room, fresh_public_room):
    _priv, pub = generate_keypair()
    body = {"invite_token": fresh_room["participant_invite"], "pubkey": _b64url(pub)}
    r = await app_client.post(f"/v1/rooms/{fresh_public_room['room_id']}/claim", json=body)
    assert r.status_code == 400 and r.json()["detail"]["code"] == "org.agentstorming.err.room_mismatch"
    r = await app_client.post(f"/v1/rooms/{fresh_room['room_id']}/claim", json=body)
    assert r.status_code == 200, r.text


@pytest.mark.asyncio
@pytest.mark.parametrize("token", ["ümlaut-token", "☃" * 10, 12345, ["x"]])
async def test_an_odd_invite_token_is_refused_not_a_500(app_client, fresh_room, token):
    _priv, pub = generate_keypair()
    r = await app_client.post(f"/v1/rooms/{fresh_room['room_id']}/claim",
                              json={"invite_token": token, "pubkey": _b64url(pub)})
    assert r.status_code in (400, 409), r.text


@pytest.mark.asyncio
async def test_readyz_is_503_while_the_listen_connection_is_down(reset_db, tmp_path):
    os.environ["AGENTSTORMING_DSN"] = DSN
    app = create_app(Settings(dsn=DSN, attachments_local_dir=tmp_path / "a"))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client, \
            app.router.lifespan_context(app):
        assert (await client.get("/readyz")).status_code == 200
        hub = app.state.pubsub
        hub._conn.terminate()
        r = await client.get("/readyz")
        assert r.status_code == 503 and r.json() == {"ok": False}
        assert await hub.check() is True
        assert (await client.get("/readyz")).status_code == 200
