# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Google OAuth2 refresh-flow provider.

Covers Google Workspace APIs (Gmail, Drive, Sheets, Docs, Calendar,
Admin SDK) and GCP Cloud APIs that accept user-OAuth tokens. Token
endpoint is fixed; the data-plane host is configurable per-config so
the host_allowlist isn't pinned to *.googleapis.com unless the operator
explicitly opts in.
"""

from __future__ import annotations

from typing import Any

from .oauth2_refresh import OAuth2RefreshFlowProvider


class GoogleOAuth2Provider(OAuth2RefreshFlowProvider):
    name = "google_oauth2"

    DEFAULT_HOSTS = (
        "www.googleapis.com",
        "gmail.googleapis.com",
        "drive.googleapis.com",
        "sheets.googleapis.com",
        "docs.googleapis.com",
        "calendar.googleapis.com",
        "admin.googleapis.com",
    )

    def __init__(self, config: dict[str, Any]) -> None:
        config = dict(config)
        config.setdefault("token_endpoint", "https://oauth2.googleapis.com/token")
        config.setdefault("host_allowlist", list(self.DEFAULT_HOSTS))
        super().__init__(config)
