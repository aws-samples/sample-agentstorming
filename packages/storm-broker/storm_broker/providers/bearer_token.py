# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""BearerTokenProvider — covers ~70% of SaaS APIs.

GitHub PATs/App tokens, Slack bot/user/app tokens, Stripe keys,
Notion, Linear, Asana, Jira (PAT mode), Anthropic, OpenAI, Cloudflare,
DataDog, PagerDuty, Sentry, Twilio, SendGrid, HubSpot, Cohere,
Mistral, Together, Groq, Perplexity, Replicate, Hugging Face, Pinecone,
Weaviate, …

Most of them use `Authorization: Bearer <token>` but variations exist:
- GitHub:    Authorization: token <token>   (legacy) or Bearer
- Slack:     Authorization: Bearer <token>
- Anthropic: x-api-key: <token>
- SendGrid:  Authorization: Bearer <token>

Hence the configurable `header_name` and `header_format`.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from .base import Provider, ProviderResult, ProviderError


class BearerTokenProvider(Provider):
    """Inject a bearer-style header on outbound requests.

    Config:
        token: the token (DO NOT put in YAML — read from a file or env)
        token_file: path to a file containing the token
        header_name: default "Authorization"
        header_format: e.g. "Bearer {token}", "token {token}", "{token}"
        host_allowlist: list of hostnames this provider is authorised for
    """
    name = "bearer_token"

    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__(config)
        self.header_name = config.get("header_name", "Authorization")
        self.header_format = config.get("header_format", "Bearer {token}")
        self.host_allowlist = set(config.get("host_allowlist", []))
        self._token = self._load_token()

    def _load_token(self) -> str:
        if "token" in self.config:
            return str(self.config["token"])
        token_file = self.config.get("token_file")
        if token_file:
            p = Path(token_file).expanduser()
            if not p.exists():
                raise ProviderError(f"token file not found: {token_file}")
            return p.read_text().strip()
        env = self.config.get("token_env")
        if env:
            v = os.environ.get(env)
            if v is None:
                raise ProviderError(f"env var not set: {env}")
            return v
        raise ProviderError("no token / token_file / token_env in config")

    def prepare_headers(
        self, target: str, method: str, url: str, body: bytes | None = None
    ) -> ProviderResult:
        if self.host_allowlist:
            from urllib.parse import urlparse
            host = urlparse(url).hostname or ""
            if host not in self.host_allowlist:
                raise ProviderError(
                    f"host {host!r} not in allowlist {sorted(self.host_allowlist)}"
                )
        return ProviderResult(
            headers={self.header_name: self.header_format.format(token=self._token)},
        )

    def prepare_credentials(self, target: str = "") -> ProviderResult:
        """Return the bare token in a credentials_json blob.

        Used by SDK paths that need an api_key directly (rather than an
        injected Authorization header). The token is short-lived to
        the call frame — the SDK is expected to pass it as kwargs and
        not write it to disk.
        """
        return ProviderResult(
            credentials_json={"api_key": self._token},
        )
