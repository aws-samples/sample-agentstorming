# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""ID value objects used throughout the domain."""

from __future__ import annotations

import base64
import hashlib
from dataclasses import dataclass
from typing import Final

PID_SEPARATOR: Final = "@"


def fingerprint_pubkey(pubkey: bytes) -> str:
    """Return the 43-char base64url sha256 of a raw Ed25519 pubkey (32 bytes)."""
    if len(pubkey) != 32:
        raise ValueError(f"ed25519 pubkey must be 32 bytes, got {len(pubkey)}")
    digest = hashlib.sha256(pubkey).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def make_pid(pubkey: bytes, room_id: str) -> str:
    return f"{fingerprint_pubkey(pubkey)}{PID_SEPARATOR}{room_id}"


@dataclass(frozen=True)
class ParticipantId:
    fingerprint: str
    room_id: str

    @classmethod
    def parse(cls, pid: str) -> "ParticipantId":
        if PID_SEPARATOR not in pid:
            raise ValueError(f"invalid PID: {pid!r}")
        fp, room = pid.split(PID_SEPARATOR, 1)
        return cls(fp, room)

    def __str__(self) -> str:  # pragma: no cover
        return f"{self.fingerprint}{PID_SEPARATOR}{self.room_id}"


def kid_from_pubkey(pubkey: bytes) -> str:
    """First 16 hex chars of sha256(pubkey)."""
    return hashlib.sha256(pubkey).hexdigest()[:16]
