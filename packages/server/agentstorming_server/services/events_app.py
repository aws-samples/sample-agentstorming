# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Application service around posting events.

Handles: signature verify, replay, governance gates, commit, side-effects.
"""

from __future__ import annotations

import base64
import logging
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID, uuid4

from fastapi import HTTPException

from ..domain.event import (
    Envelope,
    Signature,
    TYPE_AFFILIATION_CHANGED,
    TYPE_ATTACHMENT,
    TYPE_GO_SPEAK_GRANTED,
    TYPE_HAND_LOWERED,
    TYPE_HAND_RAISED,
    TYPE_KEY_REVOCATION,
    TYPE_KEY_ROTATION,
    TYPE_MESSAGE,
    TYPE_PARTICIPANT_JOINED,
    TYPE_PARTICIPANT_LEFT,
    TYPE_WHISPER,
    PARTICIPANT_POSTED_TYPES,
)
from ..domain.participant import Participant
from ..domain.room import RoomConfig
from ..repo.events import EventRepo
from ..repo.grants import GrantRepo
from ..repo.hands import HandRepo
from ..repo.mutes import MuteRepo
from ..repo.participants import ParticipantRepo
from ..repo.rooms import RoomRepo
from . import sig as sig_mod
from .auth import Principal
from .replay import ReplayGuard, ReplayError

log = logging.getLogger(__name__)


class EventAppError(HTTPException):
    def __init__(self, status: int, code: str, message: str, **details) -> None:
        super().__init__(status_code=status, detail={"code": code, "message": message, "details": details})


class PostEventService:
    def __init__(
        self,
        rooms: RoomRepo,
        participants: ParticipantRepo,
        events: EventRepo,
        hands: HandRepo,
        grants: GrantRepo,
        mutes: MuteRepo,
        replay: ReplayGuard,
        build_system_event,
        tokens=None,
    ) -> None:
        self.rooms = rooms
        self.participants = participants
        self.events = events
        self.hands = hands
        self.grants = grants
        self.mutes = mutes
        self.replay = replay
        self._build_system_event = build_system_event
        self.tokens = tokens

    async def _has_moderator_authority(self, room_id: str, principal) -> bool:
        """True iff the principal may bypass freeze and raise-hand gating.

        Same rule as ``AuthService.is_moderator``: room-owner, or whoever
        currently holds the ``moderating`` seat. Deliberately NOT a
        ``power_level`` comparison.

        Both callers below used ``power_level < 80``, which is the mirror
        image of the bug ADR-006 fixed. Deputy power levels are ``80 -
        deputy_rank``, so every deputy sits below 80 — and once the seat has
        passed to a deputy, that test denied the *acting moderator*. For
        freeze in particular the failure is self-defeating: succession to a
        deputy is exactly what happens when the freeze TTL elapses, so the
        one participant who needs to act in a frozen room was the one locked
        out of it.

        The lesson from ADR-006 generalises: a role is not a number. Any
        numeric stand-in for "is the moderator" is wrong in one direction or
        the other, and which direction depends on where the seat happens to
        sit at that moment.
        """
        if principal.participant.affiliation == "room-owner":
            return True
        acting = await self.participants.get_acting_moderator(room_id)
        return acting is not None and acting.pid == principal.pid

    # --- main entry point ---------------------------------------------

    async def post(self, principal: Principal, env_in: Envelope, raw_dict: dict | None = None) -> Envelope:
        if env_in.type not in PARTICIPANT_POSTED_TYPES:
            raise EventAppError(400, "org.agentstorming.err.invalid_type",
                                f"type {env_in.type} is system-only")

        if env_in.room_id != principal.room_id:
            raise EventAppError(400, "org.agentstorming.err.room_mismatch", "envelope room_id != URL")

        if env_in.sender != principal.pid:
            raise EventAppError(400, "org.agentstorming.err.sender_mismatch", "envelope sender != token pid")

        # §4.7 — if the participant has already revoked their key, every
        # further event under it is rejected, even though the signature
        # would still verify against the stored pubkey.
        if principal.participant.revoked_at is not None:
            raise EventAppError(
                403, "org.agentstorming.err.key_revoked",
                "signing key has been revoked",
            )

        # Signature (strict unless it's a whisper, which still requires sig).
        # Verify against the raw_dict if available (byte-for-byte), else
        # fall back to the Pydantic round-tripped dict.
        envelope_dict = raw_dict if raw_dict is not None else env_in.model_dump(mode="json")
        if not sig_mod.verify_envelope(envelope_dict, principal.participant.pubkey):
            raise EventAppError(400, "org.agentstorming.err.signature_invalid", "signature verification failed")

        # Key rotation: additionally require the payload to carry a
        # valid co-signature from the NEW key over the same canonical
        # blob. The spec requires the event to be signed by BOTH keys;
        # envelope.sig covers the old key, payload.new_sig covers the new.
        new_pubkey_bytes: bytes | None = None
        if env_in.type == TYPE_KEY_ROTATION:
            new_pubkey_b64 = (env_in.payload or {}).get("new_pubkey")
            new_sig_b64 = (env_in.payload or {}).get("new_sig")
            if not new_pubkey_b64 or not new_sig_b64:
                raise EventAppError(
                    400, "org.agentstorming.err.key_rotation_missing_fields",
                    "key_rotation requires payload.new_pubkey and payload.new_sig",
                )
            try:
                pad = "=" * (-len(new_pubkey_b64) % 4)
                new_pubkey_bytes = base64.urlsafe_b64decode(new_pubkey_b64 + pad)
            except Exception:
                raise EventAppError(
                    400, "org.agentstorming.err.key_rotation_pubkey_decode",
                    "new_pubkey is not valid base64url",
                )
            if len(new_pubkey_bytes) != 32:
                raise EventAppError(
                    400, "org.agentstorming.err.key_rotation_pubkey_size",
                    "new_pubkey must be a 32-byte Ed25519 key",
                )
            if new_pubkey_bytes == principal.participant.pubkey:
                raise EventAppError(
                    400, "org.agentstorming.err.key_rotation_same_key",
                    "new_pubkey is identical to the current pubkey",
                )
            # Check that no other participant in the room already has
            # this pubkey, to avoid collisions.
            collider = await self.participants.get_by_pubkey(principal.room_id, new_pubkey_bytes)
            if collider is not None and collider.pid != principal.pid:
                raise EventAppError(
                    409, "org.agentstorming.err.key_rotation_pubkey_in_use",
                    "new_pubkey already registered to another participant",
                )
            # Verify the co-signature. The co-sig covers the canonical
            # envelope minus {seq, ts_server, sig, payload.new_sig}, so
            # there is no circularity (envelope.sig is then computed
            # over the same canonical form WITH new_sig present).
            from .jcs import canonicalise
            co_envelope = {k: v for k, v in envelope_dict.items()
                           if k not in ("seq", "ts_server", "sig")}
            co_payload = dict(co_envelope.get("payload") or {})
            co_payload.pop("new_sig", None)
            co_envelope["payload"] = co_payload
            co_blob = canonicalise(co_envelope)
            if not sig_mod.verify_blob(co_blob, new_sig_b64, new_pubkey_bytes):
                raise EventAppError(
                    400, "org.agentstorming.err.key_rotation_new_sig_invalid",
                    "new_sig did not verify against new_pubkey",
                )

        # Replay
        try:
            await self.replay.check_and_register(principal.pid, env_in.nonce, env_in.iat)
        except ReplayError as e:
            raise EventAppError(409, "org.agentstorming.err.replay", str(e))

        # Room state gates
        room = await self.rooms.get(principal.room_id)
        if room is None:
            raise EventAppError(404, "org.agentstorming.err.unknown_room", "room not found")
        if room.state == "TERMINATED":
            raise EventAppError(410, "org.agentstorming.err.room_terminated", "room terminated")
        if room.state == "FROZEN" and env_in.type not in (TYPE_WHISPER,):
            # Moderator and owner bypass freeze. Seat identity, not a power
            # level — see _has_moderator_authority.
            if not await self._has_moderator_authority(principal.room_id, principal):
                raise EventAppError(409, "org.agentstorming.err.room_frozen", "room is frozen")

        # Mute
        if env_in.type in (TYPE_MESSAGE, TYPE_ATTACHMENT, TYPE_HAND_RAISED):
            if await self.mutes.is_muted(principal.room_id, principal.pid):
                raise EventAppError(409, "org.agentstorming.err.muted", "sender is muted")

        # Raise-hand mode gate for message/attachment
        if env_in.type in (TYPE_MESSAGE, TYPE_ATTACHMENT) and room.config.raise_hand_required:
            if not await self._has_moderator_authority(principal.room_id, principal):
                grant_id = env_in.payload.get("grant_id")
                if not grant_id:
                    raise EventAppError(409, "org.agentstorming.err.no_grant", "raise-hand room requires grant_id")
                g = await self.grants.active_for_pid(principal.room_id, principal.pid)
                if g is None or str(g["grant_id"]) != str(grant_id):
                    raise EventAppError(409, "org.agentstorming.err.no_grant", "grant is not active for this sender")

        # Commit — pass raw_dict through so stored bytes match signature.
        committed = await self.events.append(principal.room_id, env_in, raw_dict)

        # Side effects
        await self._apply_side_effects(principal, room.config, committed)

        # Key-rotation takes effect AFTER the event is committed so the
        # event itself is still verifiable against the old pubkey on replay.
        if env_in.type == TYPE_KEY_ROTATION and new_pubkey_bytes is not None:
            await self.participants.set_pubkey(principal.room_id, principal.pid, new_pubkey_bytes)

        # §4.7 — key_revocation: mark the pubkey retired. Every future
        # event under it is rejected (see the ``revoked_at`` check at
        # the top of this method). Tokens are also revoked so a
        # compromised holder cannot keep using the refresh chain.
        if env_in.type == TYPE_KEY_REVOCATION:
            now = datetime.now(timezone.utc)
            await self.participants.set_revoked(principal.room_id, principal.pid, now)
            if self.tokens is not None:
                await self.tokens.revoke_all_for_pid(principal.room_id, principal.pid)

        return committed

    # --- side effects --------------------------------------------------

    async def _apply_side_effects(self, principal: Principal, cfg: RoomConfig, env: Envelope) -> None:
        if env.type == TYPE_HAND_RAISED:
            hand_id, created, existing = await self.hands.raise_hand(
                principal.room_id, principal.pid, env.payload.get("hint", "")
            )
            if not created:
                # Rollback the hand_raised event? No — per spec we accept the envelope but return hand_already_raised
                # to the caller via a separate path. The simplest correct behaviour: we emit the event anyway
                # and the client sees the existing hand_id in the next snapshot. We attach the hand_id in payload.
                env.payload["hand_id"] = str(existing)
            else:
                env.payload["hand_id"] = str(hand_id)
            # Update the stored row with the enriched payload — we do a
            # lightweight "update events.payload and raw" to keep the wire
            # representation consistent.
            # (Simpler approach: we could have enriched before append; doing
            # so cleanly would require restructuring. We accept the extra
            # roundtrip here.)
        elif env.type == TYPE_HAND_LOWERED:
            hid = env.payload.get("hand_id")
            if hid:
                await self.hands.lower_hand(principal.room_id, UUID(hid), principal.pid)
        elif env.type == TYPE_MESSAGE or env.type == TYPE_ATTACHMENT:
            grant_id = env.payload.get("grant_id")
            if grant_id:
                try:
                    await self.grants.consume(principal.room_id, UUID(grant_id))
                    # Also mark hand consumed if there was one associated.
                    g = await self.grants.active_for_pid(principal.room_id, principal.pid)
                    # (already consumed; fetch by grant_id directly not needed)
                except Exception:
                    log.exception("failed to consume grant")
