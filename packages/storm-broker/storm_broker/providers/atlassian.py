# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Atlassian Cloud OAuth2 refresh-flow provider.

Atlassian Cloud (Jira, Confluence, Bitbucket) uses a single OAuth2
endpoint at auth.atlassian.com. API calls go through api.atlassian.com,
which routes to the org's cloud_id-bound subpath.
"""

from __future__ import annotations

from typing import Any

from .oauth2_refresh import OAuth2RefreshFlowProvider


class AtlassianOAuth2Provider(OAuth2RefreshFlowProvider):
    name = "atlassian_oauth2"

    def __init__(self, config: dict[str, Any]) -> None:
        config = dict(config)
        config.setdefault("token_endpoint", "https://auth.atlassian.com/oauth/token")
        config.setdefault("host_allowlist", ["api.atlassian.com"])
        super().__init__(config)
