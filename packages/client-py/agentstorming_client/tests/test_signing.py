# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Client-side signing must match server-side canonicalisation byte-for-byte."""

from __future__ import annotations

from agentstorming_client.signing import (
    canonicalise,
    generate_keypair,
    sign_envelope,
    verify_envelope,
)


def test_roundtrip():
    priv, pub = generate_keypair()
    env = {
        "id": "x", "type": "t", "room_id": "r", "sender": "s",
        "ts_sender": "2026-05-09T00:00:00Z", "iat": "2026-05-09T00:00:00Z",
        "nonce": "abc", "payload": {"text": "hi"},
    }
    env["sig"] = {"alg": "ed25519", "kid": "k", "val": sign_envelope(env, priv)}
    assert verify_envelope(env, pub)


def test_canonicalise_matches_server():
    # Import server copy; both must produce identical bytes.
    import importlib.util, pathlib
    server_path = pathlib.Path(__file__).resolve().parents[3] / "server" / "agentstorming_server" / "services" / "jcs.py"
    spec = importlib.util.spec_from_file_location("server_jcs", server_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]

    cases = [
        {"a": 1, "b": "two", "c": [1, 2, 3]},
        {"nested": {"y": 2, "x": 1}},
        {"unicode": "héllo", "num": 42},
    ]
    for c in cases:
        assert canonicalise(c) == mod.canonicalise(c)
