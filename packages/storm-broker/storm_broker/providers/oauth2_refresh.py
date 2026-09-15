# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""OAuth2 refresh-flow provider.

Holds the refresh token, mints short-lived access tokens via the
provider's token endpoint, caches them with a small TTL, refreshes
when expiring. Covers Salesforce, Google Workspace, Microsoft Graph,
Atlassian Cloud OAuth, Box, Dropbox, OneDrive, HubSpot OAuth.

Config:
    client_id: ...
    client_secret: ... OR client_secret_file / client_secret_env
    refresh_token: ... OR refresh_token_file / refresh_token_env
    token_endpoint: e.g. https://oauth2.googleapis.com/token
    scopes: list[str]
    host_allowlist: list[str]
    refresh_lead_seconds: 60  (refresh this many seconds before expiry)
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .base import Provider, ProviderResult, ProviderError


class OAuth2RefreshFlowProvider(Provider):
    name = "oauth2_refresh"

    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__(config)
        self.token_endpoint = config["token_endpoint"]
        self.scopes = config.get("scopes", [])
        self.host_allowlist = set(config.get("host_allowlist", []))
        self.refresh_lead_seconds = int(config.get("refresh_lead_seconds", 60))
        self._client_id = config["client_id"]
        self._client_secret = self._load("client_secret")
        self._refresh_token = self._load("refresh_token")
        self._access_token: str | None = None
        self._access_expires_at: float = 0.0

    def _load(self, key: str) -> str:
        if key in self.config:
            return str(self.config[key])
        if f"{key}_file" in self.config:
            return Path(self.config[f"{key}_file"]).expanduser().read_text().strip()
        if f"{key}_env" in self.config:
            v = os.environ.get(self.config[f"{key}_env"])
            if v is None:
                raise ProviderError(f"env var not set: {self.config[f'{key}_env']}")
            return v
        raise ProviderError(f"no {key} configured")

    def _refresh_if_needed(self) -> None:
        now = time.time()
        if self._access_token and now < self._access_expires_at - self.refresh_lead_seconds:
            return
        # Lazy-import httpx so unit tests can mock without installing it.
        import httpx
        data = {
            "grant_type": "refresh_token",
            "refresh_token": self._refresh_token,
            "client_id": self._client_id,
            "client_secret": self._client_secret,
        }
        if self.scopes:
            data["scope"] = " ".join(self.scopes)
        with httpx.Client(timeout=30.0) as c:
            r = c.post(self.token_endpoint, data=data)
            if r.status_code != 200:
                raise ProviderError(f"refresh failed {r.status_code}: {r.text[:200]}")
            j = r.json()
        self._access_token = j["access_token"]
        expires_in = int(j.get("expires_in", 3600))
        self._access_expires_at = time.time() + expires_in
        # Some providers return a new refresh_token (rotation) — adopt it.
        if "refresh_token" in j:
            self._refresh_token = j["refresh_token"]

    def prepare_headers(
        self, target: str, method: str, url: str, body: bytes | None = None
    ) -> ProviderResult:
        if self.host_allowlist:
            host = urlparse(url).hostname or ""
            if host not in self.host_allowlist:
                raise ProviderError(f"host {host!r} not in allowlist")
        self._refresh_if_needed()
        return ProviderResult(headers={"Authorization": f"Bearer {self._access_token}"})
