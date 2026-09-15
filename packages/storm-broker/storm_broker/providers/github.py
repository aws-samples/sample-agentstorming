# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""GitHub provider — extends BearerTokenProvider with the
"token <pat>" header convention (and optional Bearer for App tokens).
"""

from __future__ import annotations

from typing import Any

from .bearer_token import BearerTokenProvider


class GitHubProvider(BearerTokenProvider):
    """Default header_format = 'Bearer {token}', host_allowlist =
    api.github.com. Subclass override for legacy 'token <pat>' if
    config.legacy_format is true."""

    name = "github"

    def __init__(self, config: dict[str, Any]) -> None:
        config = dict(config)
        config.setdefault("host_allowlist", ["api.github.com"])
        if config.pop("legacy_format", False):
            config.setdefault("header_format", "token {token}")
        else:
            config.setdefault("header_format", "Bearer {token}")
        super().__init__(config)
