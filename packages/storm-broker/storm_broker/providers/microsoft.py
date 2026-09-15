# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Microsoft Entra ID OAuth2 refresh-flow provider for Microsoft Graph.

Token endpoint is tenant-scoped; data plane is graph.microsoft.com (commercial)
or graph.microsoft.us / microsoftgraph.chinacloudapi.cn for sovereign clouds.
"""

from __future__ import annotations

from typing import Any

from .oauth2_refresh import OAuth2RefreshFlowProvider


class MicrosoftGraphProvider(OAuth2RefreshFlowProvider):
    name = "microsoft_graph"

    SOVEREIGN_HOSTS = {
        "commercial": ("login.microsoftonline.com", "graph.microsoft.com"),
        "gcc_high":   ("login.microsoftonline.us", "graph.microsoft.us"),
        "china":      ("login.partner.microsoftonline.cn", "microsoftgraph.chinacloudapi.cn"),
    }

    def __init__(self, config: dict[str, Any]) -> None:
        config = dict(config)
        tenant = config.get("tenant", "common")
        cloud = config.pop("cloud", "commercial")
        if cloud not in self.SOVEREIGN_HOSTS:
            from .base import ProviderError
            raise ProviderError(f"unknown microsoft cloud: {cloud}")
        login_host, graph_host = self.SOVEREIGN_HOSTS[cloud]
        config.setdefault(
            "token_endpoint",
            f"https://{login_host}/{tenant}/oauth2/v2.0/token",
        )
        config.setdefault("host_allowlist", [graph_host])
        super().__init__(config)
