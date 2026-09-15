# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Metadata snapshot assembler."""

from __future__ import annotations

import base64
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from ..domain.event import TYPE_METADATA_SNAPSHOT, Signature, Envelope
from ..repo.attachments import AttachmentRepo  # noqa: F401 (future)
from ..repo.documents import DocumentRepo, SummaryRepo
from ..repo.events import EventRepo
from ..repo.grants import GrantRepo
from ..repo.hands import HandRepo
from ..repo.participants import ParticipantRepo
from ..repo.rooms import RoomRepo


def _b64url(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")


class SnapshotService:
    def __init__(
        self,
        rooms: RoomRepo,
        participants: ParticipantRepo,
        hands: HandRepo,
        grants: GrantRepo,
        documents: DocumentRepo,
        summaries: SummaryRepo,
        events: EventRepo,
    ) -> None:
        self.rooms = rooms
        self.participants = participants
        self.hands = hands
        self.grants = grants
        self.documents = documents
        self.summaries = summaries
        self.events = events

    async def build_payload(self, room_id: str) -> dict[str, Any]:
        room = await self.rooms.get(room_id)
        if room is None:
            raise ValueError(f"unknown room {room_id}")
        parts = await self.participants.list_all(room_id)
        moderator = next((p for p in parts if p.affiliation == "original-moderator"), None) or \
                    next((p for p in parts if p.deputy_rank is not None), None)
        orig = next((p for p in parts if p.affiliation == "original-moderator"), None)
        owner = next((p for p in parts if p.affiliation == "room-owner"), None)
        deputies = sorted([p for p in parts if p.deputy_rank is not None], key=lambda p: p.deputy_rank)
        hands_rows = await self.hands.list_active(room_id)
        active_grant = await self.grants.active(room_id)
        docs = await self.documents.list(room_id)
        summary = await self.summaries.get(room_id)
        return {
            "room_id": room.id,
            "room_state": room.state,
            "server_pubkey": _b64url(room.server_pubkey),
            "config": room.config.to_json(),
            "participants": [
                {
                    "pid": p.pid,
                    "pubkey": _b64url(p.pubkey),
                    "affiliation": p.affiliation,
                    "deputy_rank": p.deputy_rank,
                    "joined_at": p.joined_at.isoformat() if p.joined_at else None,
                    "last_seen_at": p.last_seen_at.isoformat() if p.last_seen_at else None,
                }
                for p in parts
            ],
            "moderator_pid": moderator.pid if moderator else None,
            "original_moderator_pid": orig.pid if orig else None,
            "owner_pid": owner.pid if owner else None,
            "deputies": [{"rank": p.deputy_rank, "pid": p.pid} for p in deputies],
            "raised_hands": [
                {"hand_id": str(h["hand_id"]), "pid": h["pid"],
                 "raised_ts": h["raised_ts"].isoformat() if h["raised_ts"] else None,
                 "state": h["state"]}
                for h in hands_rows
            ],
            "active_grant": ({
                "grant_id": str(active_grant["grant_id"]),
                "pid": active_grant["pid"],
                "hand_id": str(active_grant["hand_id"]) if active_grant["hand_id"] else None,
                "ttl_expires_at": active_grant["ttl_expires_at"].isoformat() if active_grant["ttl_expires_at"] else None,
            } if active_grant else None),
            "documents": [
                {k: (str(v) if k == "id" else v) for k, v in d.items() if k != "created_ts" and k != "updated_ts"}
                for d in docs
            ],
            "summary": summary,
        }

    async def build_raw_dict(self, room_id: str, *, sign_func) -> dict[str, Any]:
        """Build + sign a snapshot envelope; return the raw dict.

        No Pydantic round-trip — the caller can serialise the dict via
        the same JSON encoder that uvicorn uses, preserving byte-for-
        byte canonical bytes.
        """
        payload = await self.build_payload(room_id)
        ts = datetime.now(timezone.utc).isoformat()
        env_dict: dict[str, Any] = {
            "id": str(uuid4()),
            "type": TYPE_METADATA_SNAPSHOT,
            "room_id": room_id,
            "sender": "system",
            "ts_sender": ts,
            "ts_server": None,
            "iat": ts,
            "nonce": _b64url(uuid4().bytes),
            "reply_to": None,
            "mentions": [],
            "payload": payload,
        }
        sig_val = sign_func(env_dict)
        env_dict["sig"] = {"alg": "ed25519", "kid": "server", "val": sig_val}
        return env_dict

    async def build_envelope(self, room_id: str, *, sign_func) -> Envelope:
        """Kept for callers that want a typed model. Prefer build_raw_dict."""
        env_dict = await self.build_raw_dict(room_id, sign_func=sign_func)
        return Envelope.model_validate(env_dict)
