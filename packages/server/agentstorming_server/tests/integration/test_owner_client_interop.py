# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""The Python SDK's owner calls work against the real server.

The owner signature now binds ``op`` and reads are signed through headers, and
both sides of that change live in different packages. This runs the SDK, not
a copy of its signing code, so a client and server that drift apart fail here.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

import httpx
import pytest
import pytest_asyncio

from agentstorming_client import owner as owner_sdk
from agentstorming_client.signing import b64url
from agentstorming_server.app import create_app
from agentstorming_server.config import Settings
from agentstorming_server.domain.room import Room, RoomConfig
from agentstorming_server.repo.base import Database
from agentstorming_server.repo.rooms import RoomRepo
from agentstorming_server.services.sig import generate_keypair

from ..conftest import DSN

BASE = "http://testserver"


@pytest_asyncio.fixture
async def sdk(reset_db, tmp_path):
    key_path = tmp_path / "owner.key"
    _priv, pub = owner_sdk.init_key(key_path)
    os.environ["AGENTSTORMING_DSN"] = DSN
    app = create_app(Settings(
        dsn=DSN, attachments_local_dir=tmp_path / "attachments", owner_pubkey=b64url(pub),
    ))
    async with app.router.lifespan_context(app):
        db = Database(DSN)
        await db.connect()
        try:
            spriv, spub = generate_keypair()
            await RoomRepo(db).create(Room(
                id="sdk room", state="ACTIVE", config=RoomConfig(visibility="private"),
                server_pubkey=spub, server_privkey=spriv, title="SDK interop",
                created_at=datetime.now(timezone.utc),
            ))
        finally:
            await db.close()
        yield {"key_path": key_path, "pub": pub, "transport": httpx.ASGITransport(app=app)}


@pytest.mark.asyncio
async def test_sdk_signed_reads(sdk):
    kw = {"key_path": sdk["key_path"], "transport": sdk["transport"]}
    rooms = await owner_sdk.list_rooms(BASE, **kw)
    assert [r["room_id"] for r in rooms["rooms"]] == ["sdk room"]

    # A room id with a space proves the client signs the path the server decodes.
    room = await owner_sdk.get_room(BASE, "sdk room", **kw)
    assert room["title"] == "SDK interop" and room["visibility"] == "private"

    keys = await owner_sdk.list_keys(BASE, **kw)
    assert [k["pubkey"] for k in keys["keys"]] == [b64url(sdk["pub"])]


@pytest.mark.asyncio
async def test_sdk_request_invite_signs_the_op(sdk):
    invite = await owner_sdk.request_invite(
        BASE, "sdk room", kind="participant", key_path=sdk["key_path"], transport=sdk["transport"],
    )
    assert invite["kind"] == "participant" and invite["room_id"] == "sdk room"


@pytest.mark.asyncio
async def test_sdk_with_a_stranger_key_is_refused(sdk, tmp_path):
    stranger = tmp_path / "stranger.key"
    owner_sdk.init_key(stranger)
    with pytest.raises(httpx.HTTPStatusError) as err:
        await owner_sdk.list_rooms(BASE, key_path=stranger, transport=sdk["transport"])
    assert err.value.response.status_code == 403


@pytest.mark.asyncio
@pytest.mark.parametrize("room_id", ["50% done?", "a?b", "%3F", "q?x=1&y=%25"])
async def test_sdk_reads_rooms_whose_ids_need_escaping(sdk, room_id):
    """The SDK and the server escape ``%`` and ``?`` in ``op`` the same way."""
    from agentstorming_server.routers.v1_owner import op_path as server_op_path
    db = Database(DSN)
    await db.connect()
    try:
        spriv, spub = generate_keypair()
        await RoomRepo(db).create(Room(
            id=room_id, state="ACTIVE", config=RoomConfig(), server_pubkey=spub,
            server_privkey=spriv, created_at=datetime.now(timezone.utc),
        ))
    finally:
        await db.close()
    room = await owner_sdk.get_room(BASE, room_id, key_path=sdk["key_path"], transport=sdk["transport"])
    assert room["room_id"] == room_id
    path = f"/v1/owner/rooms/{room_id}"
    assert owner_sdk.op_path(path) == server_op_path(path)
