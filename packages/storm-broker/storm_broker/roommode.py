# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Verification of server-signed room-mode transitions (Stage-13 §Planning mode).

The broker must know whether its room is in ``planning`` or ``active`` mode
before it can enforce the write-effect denial. It cannot simply take the
agent's word for it: the agent is the untrusted party in this design, and a
jailbroken persona claiming "we're active now" would defeat the control
entirely.

Instead the agent relays the room's ``org.agentstorming.mode_promoted``
event exactly as the storm server signed it, and the broker verifies that
signature against the room's server public key (configured out of band at
broker start, alongside the credential material).

Note what this module deliberately does NOT do: it never canonicalises
JSON. ADR-003 warns that every additional JCS implementation is a chance
for byte-level drift that breaks signatures silently, and there are already
three copies (spec, Python SDK, TypeScript SDK). So the caller hands over
the *exact canonical bytes* the server signed; we verify the signature over
those bytes and then read the claims out of the same bytes we just proved.
There is no re-serialisation step in which drift could hide.
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from datetime import datetime, timezone

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric import ed25519

TYPE_MODE_PROMOTED = "org.agentstorming.mode_promoted"
TYPE_METADATA_SNAPSHOT = "org.agentstorming.metadata_snapshot"

# Both carry the room's mode and both are server-signed system events:
#   mode_promoted     → payload.to_mode          (a transition)
#   metadata_snapshot → payload.config.mode      (the current truth)
# The snapshot matters because an agent that starts *after* a promotion, or
# after the owner demoted the room without emitting a transition, would
# otherwise never learn the current mode.
ACCEPTED_TYPES = frozenset({TYPE_MODE_PROMOTED, TYPE_METADATA_SNAPSHOT})

# A signed promotion older than this is refused. Bounds how long a captured
# envelope stays useful to an agent trying to re-arm "active" after an owner
# has demoted the room back to planning.
DEFAULT_MAX_AGE_SECONDS = 24 * 3600


class ModeVerificationError(Exception):
    """The relayed envelope is not a valid, fresh promotion for this room."""


@dataclass
class VerifiedMode:
    mode: str
    room_id: str
    iat: datetime
    promoted_by: str | None
    source_type: str = TYPE_MODE_PROMOTED


def _b64url_decode(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


def verify_mode_envelope(
    canonical: bytes,
    signature_b64url: str,
    server_pubkey: bytes,
    *,
    expected_room_id: str,
    last_accepted_iat: datetime | None = None,
    max_age_seconds: int = DEFAULT_MAX_AGE_SECONDS,
    now: datetime | None = None,
) -> VerifiedMode:
    """Verify a relayed ``mode_promoted`` envelope. Raises on any failure.

    ``canonical`` is the byte string the server signed — the JCS form of the
    envelope with ``seq``, ``ts_server`` and ``sig`` stripped.
    """
    if len(server_pubkey) != 32:
        raise ModeVerificationError("server pubkey must be 32 raw bytes")

    key = ed25519.Ed25519PublicKey.from_public_bytes(server_pubkey)
    try:
        key.verify(_b64url_decode(signature_b64url), canonical)
    except (InvalidSignature, ValueError) as e:
        raise ModeVerificationError("signature does not verify") from e

    # Only now do we look at the content, and only at the bytes we verified.
    try:
        env = json.loads(canonical.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise ModeVerificationError("signed payload is not UTF-8 JSON") from e
    if not isinstance(env, dict):
        raise ModeVerificationError("signed payload is not a JSON object")

    ev_type = env.get("type")
    if ev_type not in ACCEPTED_TYPES:
        raise ModeVerificationError(f"wrong event type: {ev_type!r}")
    if env.get("room_id") != expected_room_id:
        raise ModeVerificationError(
            f"envelope is for room {env.get('room_id')!r}, not {expected_room_id!r}"
        )
    if env.get("sender") != "system":
        raise ModeVerificationError(f"{ev_type} must be a system event")

    payload = env.get("payload") or {}
    if ev_type == TYPE_MODE_PROMOTED:
        mode = payload.get("to_mode")
    else:
        mode = ((payload.get("config") or {}).get("mode"))
    if mode not in ("planning", "active"):
        raise ModeVerificationError(f"invalid mode in {ev_type}: {mode!r}")

    iat_raw = env.get("iat") or env.get("ts_sender")
    if not isinstance(iat_raw, str):
        raise ModeVerificationError("envelope carries no usable iat")
    try:
        iat = datetime.fromisoformat(iat_raw.replace("Z", "+00:00"))
    except ValueError as e:
        raise ModeVerificationError(f"unparseable iat: {iat_raw!r}") from e
    if iat.tzinfo is None:
        iat = iat.replace(tzinfo=timezone.utc)

    now = now or datetime.now(timezone.utc)
    age = (now - iat).total_seconds()
    if age > max_age_seconds:
        raise ModeVerificationError(f"envelope is {int(age)}s old (max {max_age_seconds}s)")
    if age < -300:
        raise ModeVerificationError("envelope is from the future beyond clock skew")

    # Monotonic: refuse to re-apply an envelope at or before the last one we
    # accepted, so a captured promotion cannot be replayed after a demotion.
    if last_accepted_iat is not None and iat <= last_accepted_iat:
        raise ModeVerificationError("envelope is not newer than the last accepted one")

    return VerifiedMode(
        mode=mode,
        room_id=expected_room_id,
        iat=iat,
        promoted_by=payload.get("promoted_by"),
        source_type=ev_type,
    )
