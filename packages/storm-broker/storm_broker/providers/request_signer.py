# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""RequestSignerProvider — HMAC / custom-signing schemes.

Base for sign-on-behalf flows: agent sends an unsigned request, broker
signs it, agent forwards. The agent never sees the signing key.

Concrete examples:
- AWS SigV4 (subclass `AwsSigV4Provider`)
- Custom HMAC-SHA256 enterprise schemes
- GCP service-account JWT (sign a JWT, return Bearer of it)
- Apple JWT (APNS) — sign with ECDSA private key

Subclasses override `sign_request`.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from .base import Provider, ProviderResult, ProviderError


class RequestSignerProvider(Provider):
    """Generic HMAC sign-on-behalf.

    Config:
        secret: ... OR secret_file / secret_env
        algorithm: "hmac-sha256" (default) | "hmac-sha512"
        header_name: where to put the signature
        canonical_request_format: how to build the message to sign
    """
    name = "request_signer"

    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__(config)
        self.algorithm = config.get("algorithm", "hmac-sha256")
        self.header_name = config.get("header_name", "X-Signature")
        self._secret = self._load_secret()

    def _load_secret(self) -> bytes:
        if "secret" in self.config:
            return str(self.config["secret"]).encode("utf-8")
        f = self.config.get("secret_file")
        if f:
            return Path(f).expanduser().read_bytes().strip()
        env = self.config.get("secret_env")
        if env:
            v = os.environ.get(env)
            if v is None:
                raise ProviderError(f"env var not set: {env}")
            return v.encode("utf-8")
        raise ProviderError("no secret configured")

    def prepare_headers(
        self, target: str, method: str, url: str, body: bytes | None = None
    ) -> ProviderResult:
        import hmac, hashlib
        # Default canonical format: "{method}\n{url}\n{body_sha256}"
        body_hash = hashlib.sha256(body or b"").hexdigest()
        canonical = f"{method}\n{url}\n{body_hash}".encode("utf-8")
        if self.algorithm == "hmac-sha256":
            sig = hmac.new(self._secret, canonical, hashlib.sha256).hexdigest()
        elif self.algorithm == "hmac-sha512":
            sig = hmac.new(self._secret, canonical, hashlib.sha512).hexdigest()
        else:
            raise ProviderError(f"unknown algorithm: {self.algorithm}")
        return ProviderResult(headers={self.header_name: sig})
