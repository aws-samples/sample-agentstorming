# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""pytest conftest for server tests — spins up a throwaway DB schema."""

from __future__ import annotations

import asyncio
import os
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path

import asyncpg
import pytest
import pytest_asyncio

from agentstorming_server.app import create_app
from agentstorming_server.config import Settings
from agentstorming_server.domain.room import Room, RoomConfig
from agentstorming_server.repo.base import Database
from agentstorming_server.repo.invites import InviteRepo
from agentstorming_server.repo.rooms import RoomRepo
from agentstorming_server.services.sig import generate_keypair

DSN = os.environ.get("AGENTSTORMING_TEST_DSN", "postgresql://agentstorming@127.0.0.1:5432/agentstorming_test")


def _have_postgres() -> bool:
    try:
        loop = asyncio.new_event_loop()
        conn = loop.run_until_complete(asyncpg.connect(DSN, timeout=3))
        loop.run_until_complete(conn.close())
        loop.close()
        return True
    except Exception:
        return False


HAS_PG = _have_postgres()


def pytest_collection_modifyitems(config, items):
    if HAS_PG:
        return
    skip = pytest.mark.skip(reason="Postgres test DB not available — set AGENTSTORMING_TEST_DSN to enable")
    for item in items:
        if "integration" in str(item.fspath):
            item.add_marker(skip)


#: Every table the schema creates, truncated as one static statement. Kept as a
#: literal rather than built from a list so the SQL contains no interpolation at
#: all. If the schema gains a table and this is not updated, tests will leak rows
#: between cases and fail — which is the right failure mode.
_TRUNCATE_ALL = (
    "TRUNCATE TABLE "
    "nonces, idempotency, mutes, grants, hands, attachments, summaries, "
    "documents, tokens, invites, events, participants, admin_tokens, "
    "owner_challenges, registration_requests, rooms "
    "CASCADE"
)


@pytest_asyncio.fixture
async def reset_db():
    """Truncate all tables at the start of each integration test."""
    if not HAS_PG:
        pytest.skip("no DB")
    conn = await asyncpg.connect(DSN)
    try:
        # Apply schema (idempotent) then truncate everything.
        #
        # The schema is this package's own *.sql, read from disk and executed as
        # DDL. There is no interpolation and no caller input; a schema cannot be
        # applied through bound parameters, so this is the only shape available.
        # nosemgrep: python.sqlalchemy.security.sqlalchemy-execute-raw-query
        schema_dir = Path(__file__).resolve().parents[1] / "schema"
        sql = "\n".join((p.read_text() for p in sorted(schema_dir.glob("*.sql"))))
        await conn.execute(sql)  # nosemgrep

        # One static statement rather than an f-string per table in a loop.
        # Table identifiers cannot be bound as parameters, so the previous
        # version interpolated each name — flagged by Semgrep, and each call was
        # wrapped in a bare `except Exception: pass` that hid a genuinely failed
        # truncate. A single literal TRUNCATE is atomic, faster, needs no
        # interpolation, and fails loudly if the schema and this list disagree.
        await conn.execute(_TRUNCATE_ALL)
    finally:
        await conn.close()
    yield


@pytest_asyncio.fixture
async def app_client(reset_db, tmp_path):
    """A fully-configured FastAPI app with httpx ASGITransport."""
    import httpx
    os.environ["AGENTSTORMING_DSN"] = DSN
    settings = Settings(dsn=DSN, attachments_local_dir=tmp_path / "attachments")
    app = create_app(settings)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        # Trigger lifespan manually — ASGITransport does not.
        async with app.router.lifespan_context(app):
            yield client


@pytest_asyncio.fixture
async def fresh_room(reset_db):
    """Create a room with one participant-invite and one moderator-invite."""
    db = Database(DSN); await db.connect()
    try:
        rooms = RoomRepo(db); invites = InviteRepo(db)
        priv, pub = generate_keypair()
        room = Room(
            id="test-room",
            state="ACTIVE",
            config=RoomConfig(),
            server_pubkey=pub,
            server_privkey=priv,
            created_at=datetime.now(timezone.utc),
        )
        await rooms.create(room)
        expires = datetime.now(timezone.utc) + timedelta(hours=1)
        _, pt = await invites.create("test-room", "participant", expires)
        _, pt2 = await invites.create("test-room", "participant", expires)
        _, mt = await invites.create("test-room", "moderator", expires)
        _, ot = await invites.create("test-room", "owner", expires)
        return {"room_id": "test-room", "participant_invite": pt,
                "participant_invite_2": pt2,
                "moderator_invite": mt, "owner_invite": ot}
    finally:
        await db.close()


@pytest_asyncio.fixture
async def fresh_public_room(reset_db):
    """A ``visibility=public`` room, so ``/register`` is reachable."""
    db = Database(DSN); await db.connect()
    try:
        rooms = RoomRepo(db); invites = InviteRepo(db)
        priv, pub = generate_keypair()
        room = Room(
            id="public-room",
            state="ACTIVE",
            config=RoomConfig(visibility="public"),
            server_pubkey=pub,
            server_privkey=priv,
            created_at=datetime.now(timezone.utc),
        )
        await rooms.create(room)
        expires = datetime.now(timezone.utc) + timedelta(hours=1)
        _, mt = await invites.create("public-room", "moderator", expires)
        _, ot = await invites.create("public-room", "owner", expires)
        return {"room_id": "public-room", "moderator_invite": mt, "owner_invite": ot}
    finally:
        await db.close()


@pytest_asyncio.fixture
async def fresh_planning_room(reset_db):
    """A room created in Stage-13 ``planning`` mode."""
    db = Database(DSN); await db.connect()
    try:
        rooms = RoomRepo(db); invites = InviteRepo(db)
        priv, pub = generate_keypair()
        room = Room(
            id="planning-room",
            state="ACTIVE",
            config=RoomConfig(mode="planning"),
            server_pubkey=pub,
            server_privkey=priv,
            created_at=datetime.now(timezone.utc),
        )
        await rooms.create(room)
        expires = datetime.now(timezone.utc) + timedelta(hours=1)
        _, pt = await invites.create("planning-room", "participant", expires)
        _, mt = await invites.create("planning-room", "moderator", expires)
        return {"room_id": "planning-room", "participant_invite": pt,
                "moderator_invite": mt}
    finally:
        await db.close()
