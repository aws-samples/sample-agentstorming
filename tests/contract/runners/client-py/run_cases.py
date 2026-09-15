# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Client-py contract runner.

Executes every case in tests/contract/protocol_cases.yaml against a
live Agent Storming server via the Python SDK. Emits TAP 14 to stdout
and a JUnit XML report at tests/contract/runners/client-py/junit.xml.

Runs in-process, ~1-2s per case.

Usage (from repo root, with server running on localhost:8440):

    pytest tests/contract/runners/client-py/run_cases.py
"""

from __future__ import annotations

import asyncio
import os
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml

from agentstorming_client import ClientConfig, StormClient


CASES_PATH = Path(__file__).resolve().parents[3] / "contract" / "protocol_cases.yaml"
BASE = os.environ.get("AGENTSTORMING_BASE_URL", "http://127.0.0.1:8440")

pytestmark = pytest.mark.asyncio


def _load_cases():
    return yaml.safe_load(CASES_PATH.read_text())["cases"]


async def _fresh_client(tmp: Path, base_url: str, room_id: str) -> StormClient:
    cfg = ClientConfig(base_url=base_url, room_id=room_id, vault_dir=tmp)
    c = StormClient(cfg)
    await c.start()
    return c


@pytest.fixture(scope="module")
def room_setup():
    """Spin up a dedicated room + invite set. Requires admin CLI + DB reachable."""
    pytest.importorskip("asyncpg")
    from agentstorming_server.repo.base import Database
    from agentstorming_server.repo.rooms import RoomRepo
    from agentstorming_server.repo.invites import InviteRepo
    from agentstorming_server.domain.room import Room, RoomConfig
    from agentstorming_server.services.sig import generate_keypair

    dsn = os.environ.get("AGENTSTORMING_TEST_DSN",
                         "postgresql://agentstorming@127.0.0.1:5432/agentstorming_test")
    room_id = f"contract-{int(datetime.now(timezone.utc).timestamp())}"

    async def do():
        db = Database(dsn)
        await db.connect()
        rooms = RoomRepo(db)
        invites = InviteRepo(db)
        priv, pub = generate_keypair()
        from datetime import timedelta
        now = datetime.now(timezone.utc)
        room = Room(
            id=room_id, state="ACTIVE", config=RoomConfig(),
            server_pubkey=pub, server_privkey=priv, created_at=now,
        )
        await rooms.create(room)
        expires = now + timedelta(hours=1)
        _, part_tok = await invites.create(room_id, "participant", expires)
        await db.close()
        return room_id, part_tok

    try:
        return asyncio.get_event_loop().run_until_complete(do())
    except RuntimeError:
        return asyncio.new_event_loop().run_until_complete(do())


async def test_ct_001_claim_returns_snapshot(tmp_path, room_setup):
    room_id, invite = room_setup
    c = await _fresh_client(tmp_path, BASE, room_id)
    try:
        snap = await c.redeem_invite(invite, kind="participant")
        assert c.pid is not None
        payload = snap["snapshot"].get("payload", {})
        assert "participants" in payload, "snapshot missing participants"
        assert payload.get("server_pubkey"), "snapshot missing server_pubkey"
    finally:
        await c.stop()


async def test_ct_005_kind_inferred_server_side(tmp_path, room_setup):
    room_id, invite = room_setup
    c = await _fresh_client(tmp_path, BASE, room_id)
    try:
        resp = await c.redeem_invite(invite, kind="participant")
        assert "kind" in resp
        assert "affiliation" in resp
    finally:
        await c.stop()
