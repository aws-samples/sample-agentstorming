# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Unit test: §4.7 key_revocation marks the participant revoked and
subsequent events are rejected."""

from __future__ import annotations

import asyncio
import base64
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from agentstorming_server.domain.event import Envelope, TYPE_KEY_REVOCATION, TYPE_MESSAGE
from agentstorming_server.domain.ids import kid_from_pubkey, make_pid
from agentstorming_server.domain.participant import Participant
from agentstorming_server.domain.room import Room, RoomConfig
from agentstorming_server.services import sig as sig_mod
from agentstorming_server.services.auth import Principal
from agentstorming_server.services.events_app import EventAppError, PostEventService


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")


def _mkkey() -> tuple[bytes, bytes]:
    sk = Ed25519PrivateKey.generate()
    return sk.private_bytes_raw(), sk.public_key().public_bytes_raw()  # type: ignore[attr-defined]


class _FakeParticipants:
    def __init__(self, p: Participant) -> None:
        self._p = p
        self.revoked_calls: list[tuple[str, str]] = []

    async def get_by_pubkey(self, room_id, pubkey):
        return None

    async def set_pubkey(self, room_id, pid, pubkey):
        self._p.pubkey = pubkey

    async def set_revoked(self, room_id, pid, when):
        self.revoked_calls.append((room_id, pid))
        self._p.revoked_at = when


class _FakeRooms:
    def __init__(self, room): self._room = room
    async def get(self, room_id): return self._room


class _FakeEvents:
    async def append(self, room_id, env, raw_dict):
        env.seq = 1
        return env


class _FakeHands: ...
class _FakeGrants:
    async def active_for_pid(self, *a, **kw): return None
    async def consume(self, *a, **kw): return None
class _FakeMutes:
    async def is_muted(self, *a, **kw): return False


class _FakeReplay:
    async def check_and_register(self, pid, nonce, iat): return None


class _FakeTokens:
    def __init__(self):
        self.revoked = []

    async def revoke_all_for_pid(self, room_id, pid):
        self.revoked.append((room_id, pid))


def _build_revocation(priv_old, pub_old, pid, room_id) -> dict[str, Any]:
    env = {
        "id": str(uuid.uuid4()),
        "type": TYPE_KEY_REVOCATION,
        "room_id": room_id,
        "sender": pid,
        "ts_sender": datetime.now(timezone.utc).isoformat(),
        "iat": datetime.now(timezone.utc).isoformat(),
        "nonce": _b64(b"\x00" * 16),
        "payload": {"reason": "compromised"},
    }
    env["sig"] = {
        "alg": "ed25519",
        "kid": kid_from_pubkey(pub_old),
        "val": sig_mod.sign_envelope(env, priv_old),
    }
    return env


def _build_message(priv, pub, pid, room_id, text) -> dict[str, Any]:
    env = {
        "id": str(uuid.uuid4()),
        "type": TYPE_MESSAGE,
        "room_id": room_id,
        "sender": pid,
        "ts_sender": datetime.now(timezone.utc).isoformat(),
        "iat": datetime.now(timezone.utc).isoformat(),
        "nonce": _b64(uuid.uuid4().bytes),
        "payload": {"text": text},
    }
    env["sig"] = {
        "alg": "ed25519",
        "kid": kid_from_pubkey(pub),
        "val": sig_mod.sign_envelope(env, priv),
    }
    return env


def _make_service(participant):
    rooms = _FakeRooms(Room(
        id=participant.room_id, state="ACTIVE", config=RoomConfig(),
        server_pubkey=b"\x00" * 32, server_privkey=b"\x00" * 32,
        created_at=datetime.now(timezone.utc),
    ))
    participants = _FakeParticipants(participant)
    tokens = _FakeTokens()
    svc = PostEventService(
        rooms=rooms, participants=participants, events=_FakeEvents(),
        hands=_FakeHands(), grants=_FakeGrants(), mutes=_FakeMutes(),
        replay=_FakeReplay(), build_system_event=lambda *a, **kw: None,
        tokens=tokens,
    )
    return svc, participants, tokens


def _run(coro): return asyncio.run(coro)


def test_key_revocation_marks_participant_and_revokes_tokens():
    priv, pub = _mkkey()
    room_id = "r1"
    pid = make_pid(pub, room_id)
    p = Participant(
        room_id=room_id, pid=pid, pubkey=pub, affiliation="member",
        deputy_rank=None, joined_at=datetime.now(timezone.utc),
    )
    svc, participants, tokens = _make_service(p)

    rev_dict = _build_revocation(priv, pub, pid, room_id)
    env = Envelope.model_validate(rev_dict)
    principal = Principal(pid=pid, room_id=room_id, participant=p)

    _run(svc.post(principal, env, raw_dict=rev_dict))

    assert participants.revoked_calls == [(room_id, pid)]
    assert tokens.revoked == [(room_id, pid)]
    assert p.revoked_at is not None


def test_events_after_revocation_are_rejected():
    priv, pub = _mkkey()
    room_id = "r1"
    pid = make_pid(pub, room_id)
    p = Participant(
        room_id=room_id, pid=pid, pubkey=pub, affiliation="member",
        deputy_rank=None, joined_at=datetime.now(timezone.utc),
        revoked_at=datetime.now(timezone.utc),
    )
    svc, _, _ = _make_service(p)
    msg_dict = _build_message(priv, pub, pid, room_id, "should be rejected")
    env = Envelope.model_validate(msg_dict)
    principal = Principal(pid=pid, room_id=room_id, participant=p)

    with pytest.raises(EventAppError) as exc:
        _run(svc.post(principal, env, raw_dict=msg_dict))
    assert exc.value.detail["code"] == "org.agentstorming.err.key_revoked"
