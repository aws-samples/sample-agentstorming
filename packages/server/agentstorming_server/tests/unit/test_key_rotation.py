# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Unit test: participant key rotation via org.agentstorming.key_rotation.

Verifies that:
  * a valid key_rotation event (co-signed by new key) is accepted,
  * the stored pubkey is swapped to the new one,
  * missing / wrong co-signatures are rejected.
"""

from __future__ import annotations

import asyncio
import base64
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from agentstorming_server.domain.event import Envelope, TYPE_KEY_ROTATION
from agentstorming_server.domain.ids import kid_from_pubkey, make_pid
from agentstorming_server.domain.participant import Participant
from agentstorming_server.domain.room import Room, RoomConfig
from agentstorming_server.services import sig as sig_mod
from agentstorming_server.services.auth import Principal
from agentstorming_server.services.events_app import EventAppError, PostEventService
from agentstorming_server.services.jcs import canonicalise


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")


def _mkkey() -> tuple[bytes, bytes]:
    sk = Ed25519PrivateKey.generate()
    return sk.private_bytes_raw(), sk.public_key().public_bytes_raw()  # type: ignore[attr-defined]


class _FakeParticipants:
    def __init__(self, p: Participant) -> None:
        self._p = p
        self.set_pubkey_calls: list[tuple[str, str, bytes]] = []

    async def get_by_pubkey(self, room_id, pubkey):
        return None

    async def set_pubkey(self, room_id, pid, pubkey):
        self.set_pubkey_calls.append((room_id, pid, pubkey))
        self._p.pubkey = pubkey


class _FakeRooms:
    def __init__(self, room): self._room = room
    async def get(self, room_id): return self._room


class _FakeEvents:
    async def append(self, room_id, env, raw_dict):
        # return env unchanged with a seq
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


def _build_envelope(priv_old, pub_old, priv_new, pub_new, pid, room_id) -> dict[str, Any]:
    env = {
        "id": str(uuid.uuid4()),
        "type": TYPE_KEY_ROTATION,
        "room_id": room_id,
        "sender": pid,
        "ts_sender": datetime.now(timezone.utc).isoformat(),
        "iat": datetime.now(timezone.utc).isoformat(),
        "nonce": _b64(b"\x00" * 16),
        "payload": {"new_pubkey": _b64(pub_new)},
    }
    # Co-sig: canonical envelope minus seq/ts_server/sig/payload.new_sig.
    co_env = {k: v for k, v in env.items() if k not in ("seq", "ts_server", "sig")}
    co_env["payload"] = {k: v for k, v in (co_env.get("payload") or {}).items() if k != "new_sig"}
    env["payload"]["new_sig"] = sig_mod.sign_blob(canonicalise(co_env), priv_new)
    # Old key signs envelope.sig over the full signed form.
    env["sig"] = {
        "alg": "ed25519",
        "kid": kid_from_pubkey(pub_old),
        "val": sig_mod.sign_envelope(env, priv_old),
    }
    return env


def _make_service(participant):
    rooms = _FakeRooms(Room(
        id=participant.room_id, state="ACTIVE", config=RoomConfig(),
        server_pubkey=b"\x00" * 32, server_privkey=b"\x00" * 32,
        created_at=datetime.now(timezone.utc),
    ))
    participants = _FakeParticipants(participant)
    svc = PostEventService(
        rooms=rooms, participants=participants, events=_FakeEvents(),
        hands=_FakeHands(), grants=_FakeGrants(), mutes=_FakeMutes(),
        replay=_FakeReplay(), build_system_event=lambda *a, **kw: None,
    )
    return svc, participants


def _run(coro): return asyncio.get_event_loop().run_until_complete(coro) if False else asyncio.run(coro)


def test_key_rotation_accepted_and_pubkey_swapped():
    priv_old, pub_old = _mkkey()
    priv_new, pub_new = _mkkey()
    room_id = "r1"
    pid = make_pid(pub_old, room_id)
    p = Participant(
        room_id=room_id, pid=pid, pubkey=pub_old, affiliation="member",
        deputy_rank=None, joined_at=datetime.now(timezone.utc),
    )
    svc, participants = _make_service(p)
    env_dict = _build_envelope(priv_old, pub_old, priv_new, pub_new, pid, room_id)
    env = Envelope.model_validate(env_dict)
    principal = Principal(pid=pid, room_id=room_id, participant=p)

    _run(svc.post(principal, env, raw_dict=env_dict))

    assert len(participants.set_pubkey_calls) == 1
    assert participants.set_pubkey_calls[0][2] == pub_new


def test_key_rotation_missing_new_sig_rejected():
    priv_old, pub_old = _mkkey()
    priv_new, pub_new = _mkkey()
    room_id = "r1"
    pid = make_pid(pub_old, room_id)
    p = Participant(
        room_id=room_id, pid=pid, pubkey=pub_old, affiliation="member",
        deputy_rank=None, joined_at=datetime.now(timezone.utc),
    )
    svc, _ = _make_service(p)
    env_dict = _build_envelope(priv_old, pub_old, priv_new, pub_new, pid, room_id)
    del env_dict["payload"]["new_sig"]
    # Re-sign envelope since payload changed.
    env_dict["sig"]["val"] = sig_mod.sign_envelope(env_dict, priv_old)
    env = Envelope.model_validate(env_dict)
    principal = Principal(pid=pid, room_id=room_id, participant=p)

    with pytest.raises(EventAppError) as exc:
        _run(svc.post(principal, env, raw_dict=env_dict))
    assert "key_rotation_missing_fields" in exc.value.detail["code"]


def test_key_rotation_wrong_new_sig_rejected():
    priv_old, pub_old = _mkkey()
    priv_new, pub_new = _mkkey()
    priv_other, _ = _mkkey()
    room_id = "r1"
    pid = make_pid(pub_old, room_id)
    p = Participant(
        room_id=room_id, pid=pid, pubkey=pub_old, affiliation="member",
        deputy_rank=None, joined_at=datetime.now(timezone.utc),
    )
    svc, _ = _make_service(p)
    env_dict = _build_envelope(priv_old, pub_old, priv_new, pub_new, pid, room_id)
    # Replace new_sig with a signature from the WRONG key.
    co_env = {k: v for k, v in env_dict.items() if k not in ("seq", "ts_server", "sig")}
    co_env["payload"] = {k: v for k, v in (co_env.get("payload") or {}).items() if k != "new_sig"}
    env_dict["payload"]["new_sig"] = sig_mod.sign_blob(canonicalise(co_env), priv_other)
    env_dict["sig"]["val"] = sig_mod.sign_envelope(env_dict, priv_old)
    env = Envelope.model_validate(env_dict)
    principal = Principal(pid=pid, room_id=room_id, participant=p)

    with pytest.raises(EventAppError) as exc:
        _run(svc.post(principal, env, raw_dict=env_dict))
    assert "key_rotation_new_sig_invalid" in exc.value.detail["code"]
