# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Ed25519 signing + JCS canonicalisation (client-side copy)."""

from __future__ import annotations

import base64
import json
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

_SIGNED_FIELD_BLACKLIST = ("seq", "ts_server", "sig")


def _strip(envelope: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in envelope.items() if k not in _SIGNED_FIELD_BLACKLIST}


def _canon(v: Any) -> str:
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        if v.is_integer():
            return str(int(v))
        return json.dumps(v, allow_nan=False)
    if isinstance(v, str):
        return json.dumps(v, ensure_ascii=False)
    if isinstance(v, list):
        return "[" + ",".join(_canon(x) for x in v) + "]"
    if isinstance(v, dict):
        items = sorted(v.items(), key=lambda kv: kv[0])
        return "{" + ",".join(json.dumps(k, ensure_ascii=False) + ":" + _canon(val) for k, val in items) + "}"
    raise TypeError(type(v).__name__)


def canonicalise(envelope: dict[str, Any]) -> bytes:
    """Canonicalise an envelope-shaped dict (strips signing blacklist fields).

    For non-envelope data (e.g. owner signed-request bodies) use
    :func:`canonicalise_any` which keeps every field.
    """
    return _canon(_strip(envelope)).encode("utf-8")


def canonicalise_any(value: Any) -> bytes:
    """Canonicalise arbitrary JSON-compatible data — no fields stripped."""
    return _canon(value).encode("utf-8")


def sign_blob(blob: bytes, private_key: bytes) -> str:
    sk = Ed25519PrivateKey.from_private_bytes(private_key)
    return b64url(sk.sign(blob))


def verify_blob(blob: bytes, signature_b64url: str, public_key: bytes) -> bool:
    try:
        pk = Ed25519PublicKey.from_public_bytes(public_key)
        sig_bytes = b64url_decode(signature_b64url)
    except (ValueError, TypeError):
        return False
    try:
        pk.verify(sig_bytes, blob)
        return True
    except InvalidSignature:
        return False


def b64url(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")


def b64url_decode(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


def generate_keypair() -> tuple[bytes, bytes]:
    sk = Ed25519PrivateKey.generate()
    priv = sk.private_bytes_raw()  # type: ignore[attr-defined]
    pub = sk.public_key().public_bytes_raw()  # type: ignore[attr-defined]
    return priv, pub


def sign_envelope(envelope: dict[str, Any], private_key: bytes) -> str:
    sk = Ed25519PrivateKey.from_private_bytes(private_key)
    return b64url(sk.sign(canonicalise(envelope)))


def verify_envelope(envelope: dict[str, Any], public_key: bytes) -> bool:
    sig = envelope.get("sig") or {}
    val = sig.get("val")
    if not val or sig.get("alg") != "ed25519":
        return False
    try:
        Ed25519PublicKey.from_public_bytes(public_key).verify(b64url_decode(val), canonicalise(envelope))
        return True
    except (InvalidSignature, Exception):
        return False
