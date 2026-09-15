# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""BasicAuthProvider — Bitbucket app password, internal SaaS, legacy."""

from __future__ import annotations

import base64
import os
from pathlib import Path
from typing import Any

from .base import Provider, ProviderResult, ProviderError


class BasicAuthProvider(Provider):
    """HTTP Basic auth.

    Config:
        username: …
        password: … OR password_file / password_env
        host_allowlist: list of hostnames
    """
    name = "basic_auth"

    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__(config)
        self.host_allowlist = set(config.get("host_allowlist", []))
        self._username = config["username"]
        self._password = self._load_password()

    def _load_password(self) -> str:
        if "password" in self.config:
            return str(self.config["password"])
        f = self.config.get("password_file")
        if f:
            return Path(f).expanduser().read_text().strip()
        env = self.config.get("password_env")
        if env:
            v = os.environ.get(env)
            if v is None:
                raise ProviderError(f"env var not set: {env}")
            return v
        raise ProviderError("no password / password_file / password_env")

    def prepare_headers(
        self, target: str, method: str, url: str, body: bytes | None = None
    ) -> ProviderResult:
        if self.host_allowlist:
            from urllib.parse import urlparse
            host = urlparse(url).hostname or ""
            if host not in self.host_allowlist:
                raise ProviderError(f"host {host!r} not in allowlist")
        creds = f"{self._username}:{self._password}".encode("utf-8")
        token = base64.b64encode(creds).decode("ascii")
        return ProviderResult(headers={"Authorization": f"Basic {token}"})
