# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Provider abstract base."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


class ProviderError(Exception):
    """Provider failed to prepare a request (creds missing, refresh failed, etc.)."""


@dataclass
class ProviderResult:
    """What a provider returns to the broker.

    Two main flavours:
    - For SDK calls (`AWS_CONTAINER_CREDENTIALS_FULL_URI` style): return
      `credentials_json` — the agent's SDK fetches it and uses normally.
    - For HTTPS proxy interception: return `headers` to inject + optional
      `body_transform` (e.g., SigV4 signing).
    """
    # Either headers-to-inject (for proxy mode) ...
    headers: dict[str, str] = field(default_factory=dict)
    # ... or AWS-style credentials JSON (for SDK container-creds mode) ...
    credentials_json: dict[str, Any] | None = None
    # ... or a signed-request blob to forward verbatim.
    signed_body: bytes | None = None
    # Optional: tell the proxy where to upstream the request (handy for
    # OAuth2 token endpoints).
    upstream_url: str | None = None


class Provider:
    """Base class for credential providers.

    Subclasses implement one of:
      - prepare_headers(target, request) — for proxy-mode SaaS
      - prepare_credentials(target) — for container-creds-mode SDKs
      - sign_request(target, request) — for SigV4-style
    """

    name: str = "base"

    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config

    def prepare_headers(self, target: str, method: str, url: str, body: bytes | None = None) -> ProviderResult:
        raise NotImplementedError

    def prepare_credentials(self, target: str) -> ProviderResult:
        raise NotImplementedError
