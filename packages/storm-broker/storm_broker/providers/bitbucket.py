# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Bitbucket Cloud provider — HTTP Basic auth with app password.

Username is the Bitbucket username; password is the app password.
Pinned to api.bitbucket.org.
"""

from __future__ import annotations

from typing import Any

from .basic_auth import BasicAuthProvider


class BitbucketProvider(BasicAuthProvider):
    name = "bitbucket"

    def __init__(self, config: dict[str, Any]) -> None:
        config = dict(config)
        if "app_password" in config and "password" not in config:
            config["password"] = config.pop("app_password")
        config.setdefault("host_allowlist", ["api.bitbucket.org"])
        super().__init__(config)
