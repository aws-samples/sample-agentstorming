# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Helper for building + signing system-posted events."""

from __future__ import annotations

import base64
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from ..domain.event import Envelope
from ..repo.rooms import RoomRepo
from . import sig as sig_mod


def _b64url(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")


class SystemEventFactory:
    def __init__(self, rooms: RoomRepo) -> None:
        self._rooms = rooms

    async def build(self, room_id: str, etype: str, *, payload: dict[str, Any],
                    mentions: list[str] | None = None) -> tuple[Envelope, dict[str, Any]]:
        """Return (Envelope, raw_dict).

        ``raw_dict`` contains the exact bytes that were signed and MUST
        be handed back to events.append so that downstream clients can
        re-verify the signature byte-for-byte.
        """
        room = await self._rooms.get(room_id)
        if room is None:
            raise ValueError(f"unknown room {room_id}")
        ts = datetime.now(timezone.utc)
        env_dict: dict[str, Any] = {
            "id": str(uuid4()),
            "type": etype,
            "room_id": room_id,
            "sender": "system",
            "ts_sender": ts.isoformat(),
            "ts_server": None,
            "iat": ts.isoformat(),
            "nonce": _b64url(uuid4().bytes),
            "reply_to": None,
            "mentions": mentions or [],
            "payload": payload,
        }
        val = sig_mod.sign_envelope(env_dict, room.server_privkey)
        env_dict["sig"] = {"alg": "ed25519", "kid": "server", "val": val}
        return Envelope.model_validate(env_dict), env_dict
