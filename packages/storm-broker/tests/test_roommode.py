# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""The broker must verify a room-mode transition, never merely accept it.

The agent process is the untrusted party in the Stage-13 threat model. If it
could tell the broker "we're active now", planning mode would stop being a
control. So the broker only believes a ``mode_promoted`` event that the storm
server actually signed.
"""

from __future__ import annotations

import base64
import json
from datetime import datetime, timedelta, timezone

import pytest
from cryptography.hazmat.primitives.asymmetric import ed25519

from storm_broker.roommode import (
    ModeVerificationError,
    verify_mode_envelope,
)

ROOM = "test-room"


def _keypair() -> tuple[bytes, bytes]:
    priv = ed25519.Ed25519PrivateKey.generate()
    from cryptography.hazmat.primitives import serialization
    raw_priv = priv.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    raw_pub = priv.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return raw_priv, raw_pub


def _b64url(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")


def _envelope(
    *, room_id: str = ROOM, ev_type: str = "org.agentstorming.mode_promoted",
    to_mode: str = "active", sender: str = "system",
    iat: datetime | None = None,
) -> dict:
    iat = iat or datetime.now(timezone.utc)
    return {
        "id": "11111111-1111-1111-1111-111111111111",
        "type": ev_type,
        "room_id": room_id,
        "sender": sender,
        "ts_sender": iat.isoformat(),
        "iat": iat.isoformat(),
        "nonce": "AAAAAAAAAAAAAAAAAAAAAA",
        "payload": {"from_mode": "planning", "to_mode": to_mode,
                    "promoted_by": "moderator@test-room"},
    }


def _sign(env: dict, priv_raw: bytes) -> tuple[bytes, str]:
    """Return (canonical_bytes, signature). Canonical form is the caller's."""
    canonical = json.dumps(env, separators=(",", ":"), sort_keys=True).encode("utf-8")
    priv = ed25519.Ed25519PrivateKey.from_private_bytes(priv_raw)
    return canonical, _b64url(priv.sign(canonical))


def test_valid_promotion_is_accepted():
    priv, pub = _keypair()
    canonical, sig = _sign(_envelope(), priv)
    out = verify_mode_envelope(canonical, sig, pub, expected_room_id=ROOM)
    assert out.mode == "active"
    assert out.room_id == ROOM
    assert out.promoted_by == "moderator@test-room"


def test_demotion_is_also_expressible():
    priv, pub = _keypair()
    canonical, sig = _sign(_envelope(to_mode="planning"), priv)
    assert verify_mode_envelope(canonical, sig, pub, expected_room_id=ROOM).mode == "planning"


def test_forged_signature_is_refused():
    priv, pub = _keypair()
    canonical, _ = _sign(_envelope(), priv)
    other_priv, _ = _keypair()
    _, wrong_sig = _sign(_envelope(), other_priv)
    with pytest.raises(ModeVerificationError, match="signature"):
        verify_mode_envelope(canonical, wrong_sig, pub, expected_room_id=ROOM)


def test_tampered_bytes_are_refused():
    """The claims are read from the verified bytes, so edits break the sig."""
    priv, pub = _keypair()
    canonical, sig = _sign(_envelope(to_mode="planning"), priv)
    tampered = canonical.replace(b'"to_mode":"planning"', b'"to_mode":"active"  ')
    with pytest.raises(ModeVerificationError, match="signature"):
        verify_mode_envelope(tampered, sig, pub, expected_room_id=ROOM)


def test_envelope_for_another_room_is_refused():
    priv, pub = _keypair()
    canonical, sig = _sign(_envelope(room_id="someone-elses-room"), priv)
    with pytest.raises(ModeVerificationError, match="is for room"):
        verify_mode_envelope(canonical, sig, pub, expected_room_id=ROOM)


def test_wrong_event_type_is_refused():
    priv, pub = _keypair()
    canonical, sig = _sign(_envelope(ev_type="org.agentstorming.message"), priv)
    with pytest.raises(ModeVerificationError, match="wrong event type"):
        verify_mode_envelope(canonical, sig, pub, expected_room_id=ROOM)


def test_participant_posted_event_is_refused():
    """Only the server may transition the mode, not a participant."""
    priv, pub = _keypair()
    canonical, sig = _sign(_envelope(sender="member@test-room"), priv)
    with pytest.raises(ModeVerificationError, match="system event"):
        verify_mode_envelope(canonical, sig, pub, expected_room_id=ROOM)


def test_stale_envelope_is_refused():
    priv, pub = _keypair()
    old = datetime.now(timezone.utc) - timedelta(days=3)
    canonical, sig = _sign(_envelope(iat=old), priv)
    with pytest.raises(ModeVerificationError, match="old"):
        verify_mode_envelope(canonical, sig, pub, expected_room_id=ROOM)


def test_replay_of_an_already_applied_envelope_is_refused():
    """Stops a captured promotion re-arming 'active' after a demotion."""
    priv, pub = _keypair()
    iat = datetime.now(timezone.utc)
    canonical, sig = _sign(_envelope(iat=iat), priv)
    first = verify_mode_envelope(canonical, sig, pub, expected_room_id=ROOM)
    with pytest.raises(ModeVerificationError, match="not newer"):
        verify_mode_envelope(
            canonical, sig, pub, expected_room_id=ROOM,
            last_accepted_iat=first.iat,
        )


def test_invalid_mode_value_is_refused():
    priv, pub = _keypair()
    canonical, sig = _sign(_envelope(to_mode="yolo"), priv)
    with pytest.raises(ModeVerificationError, match="invalid mode"):
        verify_mode_envelope(canonical, sig, pub, expected_room_id=ROOM)


def test_snapshot_mode_is_read_from_config():
    """metadata_snapshot carries the mode at payload.config.mode."""
    priv, pub = _keypair()
    env = _envelope(ev_type="org.agentstorming.metadata_snapshot")
    env["payload"] = {"room_id": ROOM, "config": {"mode": "planning"}}
    canonical, sig = _sign(env, priv)
    out = verify_mode_envelope(canonical, sig, pub, expected_room_id=ROOM)
    assert out.mode == "planning"
    assert out.source_type == "org.agentstorming.metadata_snapshot"


def test_snapshot_without_a_mode_is_refused():
    priv, pub = _keypair()
    env = _envelope(ev_type="org.agentstorming.metadata_snapshot")
    env["payload"] = {"room_id": ROOM, "config": {}}
    canonical, sig = _sign(env, priv)
    with pytest.raises(ModeVerificationError, match="invalid mode"):
        verify_mode_envelope(canonical, sig, pub, expected_room_id=ROOM)


def test_short_pubkey_is_refused():
    priv, _ = _keypair()
    canonical, sig = _sign(_envelope(), priv)
    with pytest.raises(ModeVerificationError, match="32 raw bytes"):
        verify_mode_envelope(canonical, sig, b"tooshort", expected_room_id=ROOM)
