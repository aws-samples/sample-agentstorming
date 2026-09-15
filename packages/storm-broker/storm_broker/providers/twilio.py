# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Twilio provider — HTTP Basic auth.

Username is the Account SID, password is the Auth Token. Pinned to
api.twilio.com (REST) and friends.
"""

from __future__ import annotations

from typing import Any

from .basic_auth import BasicAuthProvider


class TwilioProvider(BasicAuthProvider):
    name = "twilio"

    def __init__(self, config: dict[str, Any]) -> None:
        config = dict(config)
        # Twilio uses "username = Account SID" convention; allow user to
        # set the SID either as `account_sid` (preferred) or `username`.
        if "account_sid" in config and "username" not in config:
            config["username"] = config.pop("account_sid")
        if "auth_token" in config and "password" not in config:
            config["password"] = config.pop("auth_token")
        config.setdefault(
            "host_allowlist",
            [
                "api.twilio.com",
                "lookups.twilio.com",
                "verify.twilio.com",
                "messaging.twilio.com",
            ],
        )
        super().__init__(config)
