# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""KeyHandleProvider — sign-only access to a private key.

The agent never receives the private-key bytes. It calls
``sign(payload)`` and gets back a signature. The broker holds the key
and decides — per capability rules — whether to honour the request.

This base class supports software-resident keys loaded via the
`cryptography` library:

- Ed25519 (the obvious choice for fresh deployments)
- ECDSA P-256 / P-384 (for compat with existing JWT/COSE/JWS pipelines)
- RSA-PSS / RSA-PKCS1v15 (for legacy PKI)

Platform-native subclasses plug in by overriding ``_sign_raw``:

- **PKCS11KeyHandleProvider** — calls ``C_Sign`` on a PKCS#11 token
  (Linux HSM, YubiHSM, AWS CloudHSM-via-PKCS#11, Nitrokey, etc.).
  Implementation hint: `python-pkcs11` library; the key never leaves
  the token.
- **MacKeychainKeyHandleProvider** — uses Security.framework's
  ``SecKeyCreateSignature``. Implementation hint: ctypes wrapper or
  the `pyobjc-framework-Security` package; the key lives in the
  Keychain (and possibly the Secure Enclave on Apple Silicon).
- **WindowsCNGKeyHandleProvider** — calls CNG ``NCryptSignHash``.
  Implementation hint: `cryptography` actually has a CNG backend
  upstream but it is not exposed; alternatively use ctypes against
  ``ncrypt.dll``. Key handle stored in DPAPI-protected blob.
- **AwsKmsKeyHandleProvider** — wraps boto3 ``kms.sign``. Already
  partially covered by AwsSigV4Provider for SigV4 use-cases; this
  one is for arbitrary-payload signing.

Why a separate class instead of overloading RequestSignerProvider:
RequestSigner mutates an HTTP request; KeyHandle returns a raw
signature for arbitrary-payload (JWT, COSE, message-bus signatures,
mTLS handshake, RTC DTLS, …).
"""

from __future__ import annotations

import base64
import hashlib
from pathlib import Path
from typing import Any

from .base import Provider, ProviderError, ProviderResult


_SUPPORTED_ALGS = {
    "ed25519",
    "ecdsa-p256-sha256",
    "ecdsa-p384-sha384",
    "rsa-pss-sha256",
    "rsa-pkcs1-sha256",
}


class KeyHandleProvider(Provider):
    """Software-resident sign-only key handle.

    Config:
      key_file: PEM-encoded private key path (read at startup, never
        re-read; on Linux you may want to ``mlock`` the broker process
        memory — the broker startup script does this when run under
        systemd with ``LimitMEMLOCK=infinity``).
      key_pem: alternative — pass PEM directly (for tests).
      passphrase_env: env var holding PEM passphrase if encrypted.
      alg: algorithm hint; auto-detected from key type if absent.
      key_id (kid): caller-visible identifier (returned with the
        signature for JWS/COSE ``kid`` headers).
    """

    name = "key_handle"

    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__(config)
        # Lazy-import cryptography so unit tests for unrelated providers
        # don't pay the import cost.
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import (
            ed25519, ec, rsa,
        )

        self._serialization = serialization
        self._ed25519 = ed25519
        self._ec = ec
        self._rsa = rsa

        pem = self._load_pem()
        passphrase = self._load_passphrase()
        try:
            self._key = serialization.load_pem_private_key(pem, password=passphrase)
        except Exception as e:
            raise ProviderError(f"failed to load private key: {e}") from e

        self.kid = config.get("key_id") or config.get("kid") or "unspecified"
        self.alg = config.get("alg") or self._detect_alg()
        if self.alg not in _SUPPORTED_ALGS:
            raise ProviderError(f"unsupported alg: {self.alg!r}")

    # ----- helpers --------------------------------------------------------

    def _load_pem(self) -> bytes:
        if "key_pem" in self.config:
            v = self.config["key_pem"]
            return v.encode("utf-8") if isinstance(v, str) else v
        if "key_file" in self.config:
            return Path(self.config["key_file"]).expanduser().read_bytes()
        raise ProviderError("KeyHandleProvider requires key_file or key_pem")

    def _load_passphrase(self) -> bytes | None:
        import os
        env = self.config.get("passphrase_env")
        if env:
            v = os.environ.get(env)
            if v is None:
                raise ProviderError(f"passphrase env var not set: {env}")
            return v.encode("utf-8")
        if "passphrase" in self.config:
            v = self.config["passphrase"]
            return v.encode("utf-8") if isinstance(v, str) else v
        return None

    def _detect_alg(self) -> str:
        if isinstance(self._key, self._ed25519.Ed25519PrivateKey):
            return "ed25519"
        if isinstance(self._key, self._ec.EllipticCurvePrivateKey):
            curve_name = self._key.curve.name
            if curve_name == "secp256r1":
                return "ecdsa-p256-sha256"
            if curve_name == "secp384r1":
                return "ecdsa-p384-sha384"
            raise ProviderError(f"unsupported EC curve: {curve_name}")
        if isinstance(self._key, self._rsa.RSAPrivateKey):
            return "rsa-pss-sha256"
        raise ProviderError(f"unsupported key type: {type(self._key).__name__}")

    # ----- core operations ------------------------------------------------

    def sign(self, payload: bytes) -> dict[str, str]:
        """Sign ``payload`` and return ``{alg, kid, sig_b64}``.

        The agent never sees the private key; only the signature.
        """
        sig = self._sign_raw(payload)
        return {
            "alg": self.alg,
            "kid": self.kid,
            "sig_b64": base64.b64encode(sig).decode("ascii"),
        }

    def public_key_pem(self) -> str:
        pem = self._key.public_key().public_bytes(
            encoding=self._serialization.Encoding.PEM,
            format=self._serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        return pem.decode("ascii")

    def _sign_raw(self, payload: bytes) -> bytes:
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import padding, ec

        if self.alg == "ed25519":
            return self._key.sign(payload)
        if self.alg == "ecdsa-p256-sha256":
            return self._key.sign(payload, ec.ECDSA(hashes.SHA256()))
        if self.alg == "ecdsa-p384-sha384":
            return self._key.sign(payload, ec.ECDSA(hashes.SHA384()))
        if self.alg == "rsa-pss-sha256":
            return self._key.sign(
                payload,
                padding.PSS(mgf=padding.MGF1(hashes.SHA256()),
                            salt_length=padding.PSS.MAX_LENGTH),
                hashes.SHA256(),
            )
        if self.alg == "rsa-pkcs1-sha256":
            return self._key.sign(
                payload, padding.PKCS1v15(), hashes.SHA256(),
            )
        raise ProviderError(f"unhandled alg: {self.alg}")

    def fingerprint(self) -> str:
        """Stable fingerprint of the public key (SHA-256 of SPKI), for audit."""
        pem = self.public_key_pem().encode("ascii")
        return hashlib.sha256(pem).hexdigest()

    # KeyHandleProvider is not a header/credential provider — it does not
    # implement prepare_headers / prepare_credentials. Callers use sign().
    def prepare_headers(
        self, target: str, method: str, url: str, body: bytes | None = None,
    ) -> ProviderResult:  # pragma: no cover
        raise ProviderError("KeyHandleProvider does not support prepare_headers; use sign()")
