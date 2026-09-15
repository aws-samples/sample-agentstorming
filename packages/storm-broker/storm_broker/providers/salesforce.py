# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Salesforce OAuth2 refresh-flow provider.

Salesforce uses the standard OAuth2 refresh-token grant. The token
endpoint is org-specific (login.salesforce.com for prod,
test.salesforce.com for sandbox); REST traffic goes to the org's
``my.salesforce.com`` instance host.

Config:
    instance_host: e.g. acme.my.salesforce.com  (required — drives host allowlist)
    sandbox: bool, defaults False; when True uses test.salesforce.com
    client_id, client_secret, refresh_token: as for OAuth2RefreshFlowProvider
"""

from __future__ import annotations

from typing import Any

from .oauth2_refresh import OAuth2RefreshFlowProvider


class SalesforceProvider(OAuth2RefreshFlowProvider):
    name = "salesforce"

    def __init__(self, config: dict[str, Any]) -> None:
        config = dict(config)
        instance_host = config.get("instance_host")
        if not instance_host:
            from .base import ProviderError
            raise ProviderError("salesforce provider requires instance_host")
        sandbox = bool(config.pop("sandbox", False))
        config.setdefault(
            "token_endpoint",
            "https://test.salesforce.com/services/oauth2/token"
            if sandbox
            else "https://login.salesforce.com/services/oauth2/token",
        )
        # Lock the data-plane to the org's instance host. The token
        # endpoint host is allowed only for the refresh call (handled
        # internally), not for tool-call URLs.
        config.setdefault("host_allowlist", [instance_host])
        super().__init__(config)
