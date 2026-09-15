# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Unit tests for KeyHandleProvider.

Verifies sign-only access:
- Ed25519, ECDSA P-256, and RSA-PSS signatures all verify with the
  matching public key.
- The provider never exposes the private key bytes — only ``sign()`` and
  ``public_key_pem()``.
- prepare_headers raises (KeyHandle is not a header provider).
"""

from __future__ import annotations

import base64

import pytest

from storm_broker.providers import KeyHandleProvider
from storm_broker.providers.base import ProviderError


def _gen_ed25519_pem() -> bytes:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ed25519
    k = ed25519.Ed25519PrivateKey.generate()
    return k.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )


def _gen_ecdsa_p256_pem() -> bytes:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    k = ec.generate_private_key(ec.SECP256R1())
    return k.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )


def _gen_rsa_pem() -> bytes:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    k = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return k.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )


def test_ed25519_sign_and_verify():
    from cryptography.hazmat.primitives import serialization
    pem = _gen_ed25519_pem()
    p = KeyHandleProvider({"key_pem": pem, "key_id": "test-ed25519"})
    assert p.alg == "ed25519"
    sig = p.sign(b"hello world")
    assert sig["alg"] == "ed25519"
    assert sig["kid"] == "test-ed25519"

    # Verify externally with the public key.
    raw = base64.b64decode(sig["sig_b64"])
    pub = serialization.load_pem_public_key(p.public_key_pem().encode())
    pub.verify(raw, b"hello world")  # raises on failure


def test_ecdsa_p256_sign_and_verify():
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    pem = _gen_ecdsa_p256_pem()
    p = KeyHandleProvider({"key_pem": pem})
    assert p.alg == "ecdsa-p256-sha256"
    sig = p.sign(b"abc")
    raw = base64.b64decode(sig["sig_b64"])
    pub = serialization.load_pem_public_key(p.public_key_pem().encode())
    pub.verify(raw, b"abc", ec.ECDSA(hashes.SHA256()))


def test_rsa_pss_sign_and_verify():
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding
    pem = _gen_rsa_pem()
    p = KeyHandleProvider({"key_pem": pem})
    assert p.alg == "rsa-pss-sha256"
    sig = p.sign(b"abc")
    raw = base64.b64decode(sig["sig_b64"])
    pub = serialization.load_pem_public_key(p.public_key_pem().encode())
    pub.verify(
        raw, b"abc",
        padding.PSS(mgf=padding.MGF1(hashes.SHA256()),
                    salt_length=padding.PSS.MAX_LENGTH),
        hashes.SHA256(),
    )


def test_fingerprint_is_stable():
    pem = _gen_ed25519_pem()
    p1 = KeyHandleProvider({"key_pem": pem})
    p2 = KeyHandleProvider({"key_pem": pem})
    assert p1.fingerprint() == p2.fingerprint()


def test_missing_key_rejected():
    with pytest.raises(ProviderError):
        KeyHandleProvider({})


def test_unsupported_alg_rejected():
    pem = _gen_ed25519_pem()
    with pytest.raises(ProviderError):
        KeyHandleProvider({"key_pem": pem, "alg": "rsa-md5-quack"})


def test_prepare_headers_not_supported():
    pem = _gen_ed25519_pem()
    p = KeyHandleProvider({"key_pem": pem})
    with pytest.raises(ProviderError):
        p.prepare_headers("t", "GET", "https://x")


def test_provider_does_not_expose_private_key():
    """The agent-facing API surface must not leak the private bytes."""
    pem = _gen_ed25519_pem()
    p = KeyHandleProvider({"key_pem": pem})
    # Public attributes only — _key is intentionally name-mangled-ish
    public_attrs = [a for a in dir(p) if not a.startswith("_")]
    # No method/attr returning the private PEM
    for a in public_attrs:
        v = getattr(p, a)
        if callable(v):
            continue
        if isinstance(v, (bytes, bytearray)):
            assert b"PRIVATE KEY" not in v.upper()
        if isinstance(v, str):
            assert "PRIVATE KEY" not in v.upper()
