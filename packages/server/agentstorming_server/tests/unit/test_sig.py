# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Sign / verify round-trip + canonicalisation tests."""

from __future__ import annotations

from agentstorming_server.services.jcs import canonicalise
from agentstorming_server.services.sig import generate_keypair, sign_envelope, verify_envelope


def test_canonicalise_object_keys_sorted():
    assert canonicalise({"b": 1, "a": 2}) == b'{"a":2,"b":1}'


def test_canonicalise_nested():
    assert canonicalise({"a": [1, 2, {"c": 3, "b": 4}]}) == b'{"a":[1,2,{"b":4,"c":3}]}'


def test_canonicalise_strings_utf8_preserved():
    out = canonicalise({"k": "héllo"})
    assert b"\\u" not in out  # no escaping of non-ASCII
    assert b"h\xc3\xa9llo" in out


def test_sign_verify_roundtrip():
    priv, pub = generate_keypair()
    envelope = {
        "id": "x", "type": "t", "room_id": "r", "sender": "s",
        "ts_sender": "2026-05-09T00:00:00Z", "iat": "2026-05-09T00:00:00Z",
        "nonce": "abc", "payload": {"text": "hi"},
    }
    val = sign_envelope(envelope, priv)
    envelope["sig"] = {"alg": "ed25519", "kid": "k", "val": val}
    assert verify_envelope(envelope, pub)


def test_verify_rejects_tampered():
    priv, pub = generate_keypair()
    envelope = {
        "id": "x", "type": "t", "room_id": "r", "sender": "s",
        "ts_sender": "2026-05-09T00:00:00Z", "iat": "2026-05-09T00:00:00Z",
        "nonce": "abc", "payload": {"text": "hi"},
    }
    val = sign_envelope(envelope, priv)
    envelope["sig"] = {"alg": "ed25519", "kid": "k", "val": val}
    envelope["payload"] = {"text": "tampered"}
    assert not verify_envelope(envelope, pub)


def test_signing_ignores_seq_and_ts_server():
    """Sign once; then set seq+ts_server; signature must still verify."""
    priv, pub = generate_keypair()
    envelope = {
        "id": "x", "type": "t", "room_id": "r", "sender": "s",
        "ts_sender": "2026-05-09T00:00:00Z", "iat": "2026-05-09T00:00:00Z",
        "nonce": "abc", "payload": {"text": "hi"},
    }
    val = sign_envelope(envelope, priv)
    envelope["sig"] = {"alg": "ed25519", "kid": "k", "val": val}
    envelope["seq"] = 42
    envelope["ts_server"] = "2026-05-09T00:00:05Z"
    assert verify_envelope(envelope, pub)
