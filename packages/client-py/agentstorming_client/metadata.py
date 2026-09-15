# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Local metadata store mirroring the room's snapshot + incremental events."""

from __future__ import annotations

import asyncio
from typing import Any


class MetadataStore:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._state: dict[str, Any] = {}

    async def apply(self, event: dict[str, Any]) -> None:
        etype = event.get("type", "")
        payload = event.get("payload", {}) or {}
        async with self._lock:
            if etype == "org.agentstorming.metadata_snapshot":
                self._state = dict(payload)
                return
            parts = self._state.setdefault("participants", [])
            if etype == "org.agentstorming.participant_joined":
                pid = payload.get("pid")
                if pid and not any(p["pid"] == pid for p in parts):
                    parts.append({
                        "pid": pid,
                        "pubkey": payload.get("pubkey"),
                        "affiliation": payload.get("affiliation", "member"),
                        "deputy_rank": None,
                    })
            elif etype == "org.agentstorming.participant_left":
                pid = payload.get("pid")
                self._state["participants"] = [p for p in parts if p["pid"] != pid]
            elif etype == "org.agentstorming.affiliation_changed":
                pid = payload.get("pid")
                new_aff = payload.get("new_affiliation")
                new_rank = payload.get("deputy_rank")
                for p in parts:
                    if p["pid"] == pid:
                        if new_aff:
                            p["affiliation"] = new_aff
                        p["deputy_rank"] = new_rank
            elif etype == "org.agentstorming.moderator_changed":
                self._state["moderator_pid"] = payload.get("new_moderator_pid")
            elif etype == "org.agentstorming.original_moderator_changed":
                self._state["original_moderator_pid"] = payload.get("new_original_moderator_pid")
            elif etype == "org.agentstorming.hand_raised":
                rh = self._state.setdefault("raised_hands", [])
                rh.append({
                    "hand_id": payload.get("hand_id"),
                    "pid": event.get("sender"),
                    "raised_ts": event.get("ts_sender"),
                })
            elif etype in ("org.agentstorming.hand_lowered", "org.agentstorming.go_speak_granted"):
                hid = payload.get("hand_id")
                self._state["raised_hands"] = [h for h in self._state.get("raised_hands", []) if h.get("hand_id") != hid]
                if etype == "org.agentstorming.go_speak_granted":
                    self._state["active_grant"] = {
                        "grant_id": payload.get("grant_id"),
                        "pid": payload.get("pid"),
                        "hand_id": hid,
                        "ttl_expires_at": payload.get("ttl_expires_at"),
                    }
            elif etype == "org.agentstorming.go_speak_expired":
                self._state["active_grant"] = None
            elif etype == "org.agentstorming.room_frozen":
                self._state["room_state"] = "FROZEN"
            elif etype == "org.agentstorming.room_unfrozen":
                self._state["room_state"] = "ACTIVE"
            elif etype == "org.agentstorming.room_terminated":
                self._state["room_state"] = "TERMINATED"
            elif etype == "org.agentstorming.summary_updated":
                # text not in payload — clients must request snapshot
                pass

    async def snapshot(self) -> dict[str, Any]:
        async with self._lock:
            import copy
            return copy.deepcopy(self._state)

    async def moderator_pid(self) -> str | None:
        async with self._lock:
            return self._state.get("moderator_pid")

    async def active_grant_for(self, pid: str) -> dict | None:
        async with self._lock:
            g = self._state.get("active_grant")
            if g and g.get("pid") == pid:
                return dict(g)
            return None

    async def server_pubkey_b64(self) -> str | None:
        async with self._lock:
            return self._state.get("server_pubkey")

    async def peer_pubkey_b64(self, pid: str) -> str | None:
        async with self._lock:
            for p in self._state.get("participants", []):
                if p.get("pid") == pid:
                    return p.get("pubkey")
            return None
