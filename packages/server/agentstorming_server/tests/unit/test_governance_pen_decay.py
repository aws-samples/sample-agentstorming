# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Unit test: pen expiry decays penned -> member and emits affiliation_changed."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace

from agentstorming_server.domain.event import TYPE_AFFILIATION_CHANGED
from agentstorming_server.services.governance import GovernanceTicker


class _FakeConn:
    def __init__(self, rows):
        self._rows = rows

    async def fetch(self, *_args, **_kw):
        return self._rows

    async def execute(self, *_a, **_kw):
        return None


class _FakeDB:
    def __init__(self, rows):
        self._rows = rows

    @asynccontextmanager
    async def acquire(self):
        yield _FakeConn(self._rows)


class _FakeParticipants:
    def __init__(self):
        self.cleared: list[tuple[str, str]] = []
        self.set_aff: list[tuple[str, str, str]] = []

    async def clear_penned_until(self, room_id, pid):
        self.cleared.append((room_id, pid))

    async def set_affiliation(self, room_id, pid, affiliation, deputy_rank=None):
        self.set_aff.append((room_id, pid, affiliation))


def test_pen_expiry_decays_and_emits_event():
    rows = [{"room_id": "r1", "pid": "p1"}, {"room_id": "r1", "pid": "p2"}]
    db = _FakeDB(rows)
    participants = _FakeParticipants()

    published: list[tuple[str, str, dict]] = []

    async def publish(room_id, etype, payload=None):
        published.append((room_id, etype, payload or {}))

    ticker = GovernanceTicker(
        db=db,
        rooms=SimpleNamespace(),
        participants=participants,
        hands=SimpleNamespace(),
        grants=SimpleNamespace(),
        mutes=SimpleNamespace(),
        events=SimpleNamespace(),
        tokens=SimpleNamespace(),
        publish_system_event=publish,
    )

    asyncio.run(ticker._expire_pens())

    assert participants.cleared == [("r1", "p1"), ("r1", "p2")]
    assert participants.set_aff == [
        ("r1", "p1", "member"),
        ("r1", "p2", "member"),
    ]
    assert [(e[0], e[1]) for e in published] == [
        ("r1", TYPE_AFFILIATION_CHANGED),
        ("r1", TYPE_AFFILIATION_CHANGED),
    ]
    for _, _, payload in published:
        assert payload["new_affiliation"] == "member"
        assert payload["reason"] == "pen_expired"
