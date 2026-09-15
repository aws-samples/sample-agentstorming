# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""SendGrid provider — Bearer (SG.<key>), pinned to api.sendgrid.com."""

from __future__ import annotations

from typing import Any

from .bearer_token import BearerTokenProvider


class SendGridProvider(BearerTokenProvider):
    name = "sendgrid"

    def __init__(self, config: dict[str, Any]) -> None:
        config = dict(config)
        config.setdefault("host_allowlist", ["api.sendgrid.com"])
        config.setdefault("header_format", "Bearer {token}")
        super().__init__(config)
