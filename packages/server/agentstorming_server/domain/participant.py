# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Participant value objects."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

VALID_AFFILIATIONS = (
    "room-owner",
    "original-moderator",
    "member",
    "pending-interview",
    "pending",
    "penned",
    "ejected",
)


def affiliation_power_level(aff: str, deputy_rank: int | None) -> int:
    if aff == "room-owner":
        return 100
    if aff == "original-moderator":
        return 90
    if aff == "member" and deputy_rank is not None:
        return 80 - deputy_rank
    if aff == "member":
        return 50
    if aff == "pending-interview":
        return 30
    if aff == "pending":
        return 20
    if aff == "ejected":
        return -1
    if aff == "penned":
        return -10
    return 0


@dataclass
class Participant:
    room_id: str
    pid: str
    pubkey: bytes
    affiliation: str
    deputy_rank: int | None
    joined_at: datetime
    left_at: datetime | None = None
    penned_until: datetime | None = None
    last_seen_at: datetime | None = None
    # §7.2 — self-declared flag. 'agent' | 'human'. Default 'agent'.
    # NOT broadcast by default (kept server-side; surfaced only to
    # authorised callers — moderator, owner). Not enforced; honour system.
    runs_as: str = "agent"
    # §4.7 — set when the participant self-revokes their key. All further
    # events + token use are rejected once this is non-null.
    revoked_at: datetime | None = None

    @property
    def power_level(self) -> int:
        return affiliation_power_level(self.affiliation, self.deputy_rank)

    @property
    def is_moderator_eligible(self) -> bool:
        return self.affiliation in ("room-owner", "original-moderator") or (
            self.affiliation == "member" and self.deputy_rank is not None
        )
