# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""FastAPI auth dependencies: bearer access token → Principal.

A Principal is the authenticated actor plus their affiliation on the
current room, evaluated on every request.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from fastapi import Depends, Header, HTTPException, Path

from ..domain.participant import Participant
from ..repo.admin_tokens import AdminTokenRepo
from ..repo.participants import ParticipantRepo
from ..repo.tokens import TokenRepo


@dataclass
class Principal:
    room_id: str
    pid: str
    participant: Participant


async def _require_bearer(authorization: str | None) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail={"code": "org.agentstorming.err.auth", "message": "missing bearer token"})
    return authorization[7:].strip()


class AuthGate:
    def __init__(self, tokens: TokenRepo, participants: ParticipantRepo, admin_tokens: AdminTokenRepo) -> None:
        self._tokens = tokens
        self._participants = participants
        self._admin_tokens = admin_tokens

    async def principal(self, room_id: str, authorization: str | None) -> Principal:
        token = await _require_bearer(authorization)
        row = await self._tokens.lookup_access(token)
        if not row or row["room_id"] != room_id:
            raise HTTPException(
                status_code=401,
                detail={"code": "org.agentstorming.err.auth", "message": "invalid or expired access token"},
            )
        p = await self._participants.get(room_id, row["pid"])
        if not p:
            raise HTTPException(
                status_code=401,
                detail={"code": "org.agentstorming.err.auth", "message": "participant not found"},
            )
        if p.affiliation in ("ejected", "penned"):
            raise HTTPException(
                status_code=403,
                detail={"code": "org.agentstorming.err.pen_active", "message": f"affiliation={p.affiliation}"},
            )
        if getattr(p, "revoked_at", None) is not None:
            # §4.7 — signing key was self-revoked; every bearer is refused.
            raise HTTPException(
                status_code=403,
                detail={"code": "org.agentstorming.err.key_revoked", "message": "signing key has been revoked"},
            )
        await self._participants.touch_seen(room_id, row["pid"], datetime.now(timezone.utc))
        return Principal(room_id=room_id, pid=row["pid"], participant=p)

    async def is_moderator(self, room_id: str, principal: Principal) -> bool:
        """True iff the principal may exercise moderator authority (§6.2, §6.4).

        Exactly two classes qualify:

        - the ``room-owner``, who is the room's superuser and may do
          anything the moderator can (§13.1); and
        - whoever currently holds the ``moderating`` role, as resolved by
          ``ParticipantRepo.get_acting_moderator`` — the seated
          ``original-moderator``, or the highest-ranked deputy once the
          seat has passed to them.

        A plain ``member`` does NOT qualify, and neither does a deputy who
        is merely in the succession line while an original-moderator is
        still seated: §12.4 reserves deputy assignment for "the current
        moderator", and §6.4 permits exactly one holder of ``moderating``.
        """
        if principal.participant.affiliation == "room-owner":
            return True
        acting = await self._participants.get_acting_moderator(room_id)
        return acting is not None and acting.pid == principal.pid

    async def require_moderator(self, room_id: str, authorization: str | None) -> Principal:
        """Gate an endpoint on moderator authority.

        Do NOT gate on ``power_level`` thresholds here. An ordinary
        ``member`` sits at power_level exactly 50, so the previous
        ``power_level < 50`` test admitted every member in the room and
        collapsed the moderator-only guarantee across every endpoint that
        called this — grants, mute, eject, pen, deputy assignment,
        documents, and the rolling summary. Identity of the seat holder is
        the only safe test.
        """
        principal = await self.principal(room_id, authorization)
        if not await self.is_moderator(room_id, principal):
            raise HTTPException(
                status_code=403,
                detail={"code": "org.agentstorming.err.forbidden", "message": "moderator required"},
            )
        return principal

    async def require_owner(self, room_id: str, authorization: str | None) -> Principal:
        principal = await self.principal(room_id, authorization)
        if principal.participant.affiliation != "room-owner":
            raise HTTPException(
                status_code=403,
                detail={"code": "org.agentstorming.err.forbidden", "message": "room-owner required"},
            )
        return principal

    async def require_admin(self, authorization: str | None) -> None:
        token = await _require_bearer(authorization)
        if not await self._admin_tokens.exists(token):
            raise HTTPException(
                status_code=401,
                detail={"code": "org.agentstorming.err.auth", "message": "invalid admin token"},
            )
