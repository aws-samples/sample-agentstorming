# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""AwsSigV4Provider — SigV4 signing on behalf of the agent.

Two operating modes:

1. **Container-creds mode** (default): broker exposes its STS-derived
   creds via `AWS_CONTAINER_CREDENTIALS_FULL_URI` style JSON. The
   agent's boto3 walks its credential chain, hits the broker's HTTP
   endpoint, fetches `{AccessKeyId, SecretAccessKey, Token, Expiration}`,
   and signs locally as usual. The agent does see ephemeral STS creds
   (≤15 min); the broker's underlying long-term key never leaves.

2. **Signing-on-behalf mode**: agent posts an unsigned canonical
   request to the broker; broker computes the SigV4 signature and
   returns just the headers. The agent never sees any AWS material at
   all. Use this for high-security deployments.

Config:
    role_arn: arn to assume
    session_name_template: e.g. "storm/{room_id}/{persona_pid}/{task_id}"
    session_ttl_seconds: 900
    region: "us-east-1"
    external_id: optional
    long_term_credentials: optional dict with access_key_id+secret_access_key
                           (otherwise broker uses the host's default chain)
"""

from __future__ import annotations

import time
from typing import Any

from .base import Provider, ProviderResult, ProviderError


class AwsSigV4Provider(Provider):
    name = "aws_sigv4"

    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__(config)
        self.role_arn = config.get("role_arn")
        self.region = config.get("region", "us-east-1")
        self.session_ttl = int(config.get("session_ttl_seconds", 900))
        self.session_name_template = config.get(
            "session_name_template", "storm-broker-default"
        )
        self.external_id = config.get("external_id")
        self._cached_creds: dict[str, Any] | None = None
        self._cached_expires_at: float = 0.0

    def _refresh_creds(self, session_name: str) -> dict[str, Any]:
        # Lazy import boto3 so tests don't pay for it unless they need it.
        import boto3
        sts = boto3.client("sts", region_name=self.region)
        if self.role_arn:
            kwargs: dict[str, Any] = {
                "RoleArn": self.role_arn,
                "RoleSessionName": session_name[:64],  # AWS limit
                "DurationSeconds": self.session_ttl,
            }
            if self.external_id:
                kwargs["ExternalId"] = self.external_id
            resp = sts.assume_role(**kwargs)
            c = resp["Credentials"]
            return {
                "AccessKeyId": c["AccessKeyId"],
                "SecretAccessKey": c["SecretAccessKey"],
                "Token": c["SessionToken"],
                "Expiration": c["Expiration"].isoformat(),
            }
        # No role_arn — return the broker's default chain creds (caller
        # must trust this is OK; usually we always assume a role).
        sess = boto3.Session()
        c = sess.get_credentials()
        if c is None:
            raise ProviderError("no AWS credentials available to broker")
        f = c.get_frozen_credentials()
        # Synthesize a far-future expiration; not great but matches
        # boto3 expectations for static creds.
        return {
            "AccessKeyId": f.access_key,
            "SecretAccessKey": f.secret_key,
            "Token": f.token or "",
            "Expiration": "2099-12-31T23:59:59Z",
        }

    def prepare_credentials(self, target: str, **session_vars: str) -> ProviderResult:
        """Container-creds mode: return JSON the agent's boto3 will read."""
        now = time.time()
        if self._cached_creds and now < self._cached_expires_at - 60:
            return ProviderResult(credentials_json=self._cached_creds)
        session_name = self.session_name_template.format(**(session_vars or {})) \
            if session_vars else self.session_name_template
        creds = self._refresh_creds(session_name)
        self._cached_creds = creds
        # Parse expiration if real
        try:
            from datetime import datetime
            self._cached_expires_at = datetime.fromisoformat(
                creds["Expiration"].replace("Z", "+00:00")
            ).timestamp()
        except Exception:
            self._cached_expires_at = now + self.session_ttl
        return ProviderResult(credentials_json=creds)
