# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""OpenAI provider — Authorization: Bearer …"""

from __future__ import annotations

from typing import Any

from .bearer_token import BearerTokenProvider


class OpenAIProvider(BearerTokenProvider):
    name = "openai"

    def __init__(self, config: dict[str, Any]) -> None:
        config = dict(config)
        config.setdefault("host_allowlist", ["api.openai.com"])
        config.setdefault("header_format", "Bearer {token}")
        super().__init__(config)
