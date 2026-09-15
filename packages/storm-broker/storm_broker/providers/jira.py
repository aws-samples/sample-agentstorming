# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Jira/Atlassian Cloud provider — Bearer ATATT… tokens."""

from __future__ import annotations

from typing import Any

from .bearer_token import BearerTokenProvider


class JiraProvider(BearerTokenProvider):
    name = "jira"

    def __init__(self, config: dict[str, Any]) -> None:
        config = dict(config)
        host = config.get("host")
        if host:
            config.setdefault("host_allowlist", [host])
        config.setdefault("header_format", "Bearer {token}")
        super().__init__(config)
