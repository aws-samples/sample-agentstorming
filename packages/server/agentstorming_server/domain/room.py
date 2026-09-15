# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Room value objects."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

RoomState = Literal["CREATED", "ACTIVE", "FROZEN", "TERMINATED"]

# Stage-13 addendum §Planning mode. Orthogonal to RoomState: a room can be
# ACTIVE (accepting events) while its deliberation mode is still `planning`
# (tool calls with `effects: write` denied at the broker).
RoomMode = Literal["planning", "active"]


@dataclass
class RoomConfig:
    visibility: Literal["private", "public"] = "private"
    # Defaults to `active` so adding this field does not silently start
    # denying write tools in rooms created before it existed. Operators
    # running agents with real side effects SHOULD create rooms in
    # `planning` and promote deliberately (§Planning mode).
    mode: RoomMode = "active"
    max_participants: int = 256
    deputies_enabled: bool = True
    raise_hand_required: bool = False
    go_speak_ttl_seconds: int = 300
    snapshot_interval_seconds: int = 180
    disconnect_grace_seconds: int = 90
    history_max_return: int = 1000
    pen_max_duration_seconds: int = 31_536_000
    attachments_enabled: bool = True
    attachments_max_bytes: int = 52_428_800
    attachments_backend: Literal["local", "s3"] = "local"
    freeze_multiplier: int = 3
    rate_limit_per_minute: int = 60

    def to_json(self) -> dict[str, Any]:
        return {
            "visibility": self.visibility,
            "mode": self.mode,
            "max_participants": self.max_participants,
            "deputies_enabled": self.deputies_enabled,
            "raise_hand_required": self.raise_hand_required,
            "go_speak_ttl_seconds": self.go_speak_ttl_seconds,
            "snapshot_interval_seconds": self.snapshot_interval_seconds,
            "disconnect_grace_seconds": self.disconnect_grace_seconds,
            "history_max_return": self.history_max_return,
            "pen_max_duration_seconds": self.pen_max_duration_seconds,
            "attachments": {
                "enabled": self.attachments_enabled,
                "max_bytes": self.attachments_max_bytes,
                "backend": self.attachments_backend,
            },
            "freeze_multiplier": self.freeze_multiplier,
            "rate_limit_per_minute": self.rate_limit_per_minute,
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "RoomConfig":
        atts = data.get("attachments", {}) or {}
        mode = data.get("mode", "active")
        if mode not in ("planning", "active"):
            mode = "active"
        return cls(
            visibility=data.get("visibility", "private"),
            mode=mode,
            max_participants=int(data.get("max_participants", 256)),
            deputies_enabled=bool(data.get("deputies_enabled", True)),
            raise_hand_required=bool(data.get("raise_hand_required", False)),
            go_speak_ttl_seconds=int(data.get("go_speak_ttl_seconds", 300)),
            snapshot_interval_seconds=int(data.get("snapshot_interval_seconds", 180)),
            disconnect_grace_seconds=int(data.get("disconnect_grace_seconds", 90)),
            history_max_return=int(data.get("history_max_return", 1000)),
            pen_max_duration_seconds=int(data.get("pen_max_duration_seconds", 31_536_000)),
            attachments_enabled=bool(atts.get("enabled", True)),
            attachments_max_bytes=int(atts.get("max_bytes", 52_428_800)),
            attachments_backend=atts.get("backend", "local"),
            freeze_multiplier=int(data.get("freeze_multiplier", 3)),
            rate_limit_per_minute=int(data.get("rate_limit_per_minute", 60)),
        )


@dataclass
class Room:
    id: str
    state: RoomState
    config: RoomConfig
    server_pubkey: bytes
    server_privkey: bytes
    created_at: datetime
    freeze_since: datetime | None = None
    terminated_at: datetime | None = None
    # Human-facing metadata for public discovery.
    title: str | None = None
    description: str | None = None
