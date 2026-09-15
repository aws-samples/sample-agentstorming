# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Governance ticker: TTL expiries, freeze detection, deputy promotion."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from ..domain.event import (
    TYPE_AFFILIATION_CHANGED,
    TYPE_GO_SPEAK_EXPIRED,
    TYPE_METADATA_SNAPSHOT,
    TYPE_MODERATOR_CHANGED,
    TYPE_PARTICIPANT_DISCONNECTED,
    TYPE_ROOM_FROZEN,
    TYPE_ROOM_UNFROZEN,
    TYPE_UNMUTE,
)
from ..repo.base import Database
from ..repo.events import EventRepo
from ..repo.grants import GrantRepo
from ..repo.hands import HandRepo
from ..repo.mutes import MuteRepo
from ..repo.participants import ParticipantRepo
from ..repo.rooms import RoomRepo
from ..repo.tokens import TokenRepo

log = logging.getLogger(__name__)


class GovernanceTicker:
    def __init__(
        self,
        db: Database,
        rooms: RoomRepo,
        participants: ParticipantRepo,
        hands: HandRepo,
        grants: GrantRepo,
        mutes: MuteRepo,
        events: EventRepo,
        tokens: TokenRepo,
        *,
        tick_seconds: float = 1.0,
        build_system_event=None,
        publish_system_event=None,
        snapshot=None,
    ) -> None:
        self._db = db
        self.rooms = rooms
        self.participants = participants
        self.hands = hands
        self.grants = grants
        self.mutes = mutes
        self.events = events
        self.tokens = tokens
        self._tick_seconds = tick_seconds
        self._stopping = asyncio.Event()
        self._task: asyncio.Task | None = None
        self._build_system_event = build_system_event
        self._publish_system_event = publish_system_event
        self._snapshot = snapshot
        # room_id -> monotonic epoch seconds of last published snapshot.
        self._last_snapshot_at: dict[str, float] = {}
        # room_id -> monotonic epoch seconds of last participant_disconnected
        # notice, keyed by pid too so we only emit once per grace crossing.
        self._disconnected_seen: dict[tuple[str, str], bool] = {}

    async def start(self) -> None:
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        self._stopping.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass

    async def _run(self) -> None:
        while not self._stopping.is_set():
            try:
                async with self._db.acquire() as conn:
                    lock_id = 778_000
                    got = await conn.fetchval("SELECT pg_try_advisory_lock($1)", lock_id)
                if got:
                    try:
                        await self._expire_grants()
                        await self._expire_mutes()
                        await self._check_moderator_presence()
                        await self._expire_pens()
                        await self._emit_snapshots()
                        await self._detect_disconnects()
                    finally:
                        async with self._db.acquire() as conn:
                            await conn.execute("SELECT pg_advisory_unlock($1)", lock_id)
            except Exception:
                log.exception("governance tick error")
            await asyncio.sleep(self._tick_seconds)

    # ----- grants

    async def _expire_grants(self) -> None:
        for g in await self.grants.list_active():
            now = datetime.now(timezone.utc)
            if g["ttl_expires_at"].astimezone(timezone.utc) <= now:
                await self.grants.expire(g["room_id"], g["grant_id"])
                if g["hand_id"]:
                    await self.hands.mark_expired(g["room_id"], g["hand_id"])
                await self._publish_system_event(
                    g["room_id"], TYPE_GO_SPEAK_EXPIRED,
                    payload={"grant_id": str(g["grant_id"]), "pid": g["pid"]},
                )

    # ----- mutes

    async def _expire_mutes(self) -> None:
        for m in await self.mutes.list_expiring():
            await self._publish_system_event(
                m["room_id"], TYPE_UNMUTE, payload={"pid": m["pid"]},
            )
            async with self._db.acquire() as conn:
                await conn.execute(
                    "DELETE FROM mutes WHERE room_id=$1 AND pid=$2 AND expires_at <= now()",
                    m["room_id"], m["pid"],
                )

    # ----- moderator presence + freeze

    async def _check_moderator_presence(self) -> None:
        async with self._db.acquire() as conn:
            rows = await conn.fetch("SELECT id, state, config, freeze_since FROM rooms WHERE state IN ('ACTIVE','FROZEN')")
        for r in rows:
            room_id = r["id"]
            room = await self.rooms.get(room_id)
            if not room:
                continue
            grace = room.config.disconnect_grace_seconds
            mod = await self.participants.get_by_affiliation(room_id, "original-moderator")
            if not mod:
                mod = await self.participants.get_deputy(room_id, 1)

            # Before any moderator has ever joined, the room waits — do
            # not freeze a never-moderated room. If it already is
            # FROZEN (e.g., legacy state), unfreeze it.
            if mod is None:
                if r["state"] == "FROZEN":
                    await self.rooms.set_state(room_id, "ACTIVE")
                    await self._publish_system_event(
                        room_id, TYPE_ROOM_UNFROZEN, payload={"reason": "no_moderator_claimed_yet"},
                    )
                continue

            now = datetime.now(timezone.utc)
            mod_present = False
            if mod.last_seen_at:
                if (now - mod.last_seen_at.astimezone(timezone.utc)).total_seconds() <= grace:
                    mod_present = True

            if r["state"] == "ACTIVE" and not mod_present:
                await self.rooms.set_state(room_id, "FROZEN", freeze_since=now)
                await self._publish_system_event(
room_id, TYPE_ROOM_FROZEN,
                    payload={"reason": "moderator_absent", "ttl_seconds": grace * room.config.freeze_multiplier},
                )
            elif r["state"] == "FROZEN":
                if mod_present:
                    await self.rooms.set_state(room_id, "ACTIVE")
                    await self._publish_system_event(
                        room_id, TYPE_ROOM_UNFROZEN, payload={"reason": "moderator_returned"},
                    )
                else:
                    freeze_ttl = grace * room.config.freeze_multiplier
                    if r["freeze_since"]:
                        elapsed = (now - r["freeze_since"].astimezone(timezone.utc)).total_seconds()
                        if elapsed >= freeze_ttl:
                            # Try to promote a deputy.
                            promoted = False
                            for rank in range(1, 11):
                                dep = await self.participants.get_deputy(room_id, rank)
                                if dep and dep.last_seen_at and \
                                   (now - dep.last_seen_at.astimezone(timezone.utc)).total_seconds() <= grace:
                                    await self.participants.set_affiliation(
                                        room_id, dep.pid, "original-moderator", deputy_rank=None,
                                    )
                                    promoted = True
                                    await self._publish_system_event(
                                        room_id, TYPE_MODERATOR_CHANGED,
                                        payload={"new_moderator_pid": dep.pid, "reason": "deputy_promotion"},
                                    )
                                    await self.rooms.set_state(room_id, "ACTIVE")
                                    await self._publish_system_event(
                                        room_id, TYPE_ROOM_UNFROZEN, payload={"reason": "deputy_promoted"},
                                    )
                                    break
                            if not promoted:
                                pass

    # ----- pens

    async def _expire_pens(self) -> None:
        async with self._db.acquire() as conn:
            rows = await conn.fetch(
                "SELECT room_id, pid FROM participants WHERE penned_until IS NOT NULL AND penned_until <= now()",
            )
        for r in rows:
            await self.participants.clear_penned_until(r["room_id"], r["pid"])
            await self.participants.set_affiliation(r["room_id"], r["pid"], "member", None)
            await self._publish_system_event(
                r["room_id"], TYPE_AFFILIATION_CHANGED,
                payload={
                    "pid": r["pid"],
                    "new_affiliation": "member",
                    "reason": "pen_expired",
                },
            )

    # ----- periodic metadata_snapshot (§16.3)

    async def _emit_snapshots(self) -> None:
        """Emit a metadata_snapshot every room.config.snapshot_interval_seconds.

        Uses the SnapshotService when injected so the payload is
        byte-for-byte identical to what /stream or /snapshot returns.
        Falls back to no-op when the ticker was wired without a snapshot
        service (e.g. in tests).
        """
        if self._snapshot is None:
            return
        now = datetime.now(timezone.utc).timestamp()
        async with self._db.acquire() as conn:
            rows = await conn.fetch(
                "SELECT id, config FROM rooms WHERE state IN ('ACTIVE','FROZEN')"
            )
        for r in rows:
            room_id = r["id"]
            room = await self.rooms.get(room_id)
            if room is None:
                continue
            interval = max(15, int(room.config.snapshot_interval_seconds))
            last = self._last_snapshot_at.get(room_id)
            if last is not None and (now - last) < interval:
                continue
            try:
                payload = await self._snapshot.build_payload(room_id)
            except Exception:
                log.exception("snapshot payload build failed for %s", room_id)
                continue
            await self._publish_system_event(
                room_id, TYPE_METADATA_SNAPSHOT, payload=payload,
            )
            self._last_snapshot_at[room_id] = now

    # ----- participant_disconnected (§7.2)

    async def _detect_disconnects(self) -> None:
        """Emit participant_disconnected once per participant whose
        last_seen_at is older than the room's disconnect_grace_seconds.
        The moderator's own disconnect is handled separately (freeze
        logic) and is NOT duplicated here."""
        async with self._db.acquire() as conn:
            rows = await conn.fetch(
                "SELECT id, config FROM rooms WHERE state IN ('ACTIVE','FROZEN')"
            )
        now = datetime.now(timezone.utc)
        for r in rows:
            room_id = r["id"]
            room = await self.rooms.get(room_id)
            if room is None:
                continue
            grace = max(5, int(room.config.disconnect_grace_seconds))
            for p in await self.participants.list_all(room_id):
                if p.affiliation in ("original-moderator", "room-owner"):
                    continue
                if p.last_seen_at is None:
                    continue
                elapsed = (now - p.last_seen_at.astimezone(timezone.utc)).total_seconds()
                key = (room_id, p.pid)
                if elapsed >= grace:
                    if self._disconnected_seen.get(key):
                        continue
                    await self._publish_system_event(
                        room_id, TYPE_PARTICIPANT_DISCONNECTED,
                        payload={
                            "pid": p.pid,
                            "last_seen_at": p.last_seen_at.isoformat(),
                            "grace_seconds": grace,
                        },
                    )
                    self._disconnected_seen[key] = True
                else:
                    # They came back — clear the flag so a future
                    # disconnect gets a fresh notice.
                    self._disconnected_seen.pop(key, None)
