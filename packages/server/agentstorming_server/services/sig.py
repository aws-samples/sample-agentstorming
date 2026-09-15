# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Ed25519 signing + verification of Storm event envelopes.

Signing strips ``seq``, ``ts_server`` and ``sig`` from the envelope before
canonicalising. Clients and servers share this contract; this module is
the server-side copy. A byte-identical copy lives in the client SDK.
"""

from __future__ import annotations

import base64
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from .jcs import canonicalise

_SIGNED_FIELD_BLACKLIST = ("seq", "ts_server", "sig")


def _strip_for_signing(envelope: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in envelope.items() if k not in _SIGNED_FIELD_BLACKLIST}


def _b64url_encode(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")


def _b64url_decode(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


def generate_keypair() -> tuple[bytes, bytes]:
    """Return (private_raw_32, public_raw_32)."""
    sk = Ed25519PrivateKey.generate()
    priv = sk.private_bytes_raw()  # type: ignore[attr-defined]
    pub = sk.public_key().public_bytes_raw()  # type: ignore[attr-defined]
    return priv, pub


def sign_envelope(envelope: dict[str, Any], private_key: bytes) -> str:
    """Sign an envelope dict. Returns the base64url signature value."""
    canonical = canonicalise(_strip_for_signing(envelope))
    sk = Ed25519PrivateKey.from_private_bytes(private_key)
    return _b64url_encode(sk.sign(canonical))


def verify_envelope(envelope: dict[str, Any], public_key: bytes) -> bool:
    """Verify an envelope with its embedded sig field. Returns bool."""
    sig_field = envelope.get("sig") or {}
    sig_val = sig_field.get("val")
    if not sig_val or sig_field.get("alg") != "ed25519":
        return False
    canonical = canonicalise(_strip_for_signing(envelope))
    pk = Ed25519PublicKey.from_public_bytes(public_key)
    try:
        pk.verify(_b64url_decode(sig_val), canonical)
        return True
    except InvalidSignature:
        return False
    except Exception:
        return False


def sign_blob(blob: bytes, private_key: bytes) -> str:
    sk = Ed25519PrivateKey.from_private_bytes(private_key)
    return _b64url_encode(sk.sign(blob))


def verify_blob(blob: bytes, signature_b64url: str, public_key: bytes) -> bool:
    try:
        pk = Ed25519PublicKey.from_public_bytes(public_key)
    except (ValueError, TypeError):
        # Malformed public key. Treat as a verification failure rather
        # than crashing the request handler.
        return False
    try:
        sig_bytes = _b64url_decode(signature_b64url)
    except (ValueError, TypeError):
        return False
    try:
        pk.verify(sig_bytes, blob)
        return True
    except InvalidSignature:
        return False
