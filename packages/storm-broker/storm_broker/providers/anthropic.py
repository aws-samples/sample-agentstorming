# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Anthropic provider — uses x-api-key header (not Authorization)."""

from __future__ import annotations

from typing import Any

from .bearer_token import BearerTokenProvider


class AnthropicProvider(BearerTokenProvider):
    name = "anthropic"

    def __init__(self, config: dict[str, Any]) -> None:
        config = dict(config)
        config.setdefault("host_allowlist", ["api.anthropic.com"])
        config.setdefault("header_name", "x-api-key")
        config.setdefault("header_format", "{token}")
        super().__init__(config)
