# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Slack provider — Bearer xox{b,p,a,e} tokens against slack.com APIs."""

from __future__ import annotations

from typing import Any

from .bearer_token import BearerTokenProvider


class SlackProvider(BearerTokenProvider):
    name = "slack"

    def __init__(self, config: dict[str, Any]) -> None:
        config = dict(config)
        config.setdefault("host_allowlist", ["slack.com", "hooks.slack.com"])
        config.setdefault("header_format", "Bearer {token}")
        super().__init__(config)
