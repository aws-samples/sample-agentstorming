# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""AwsSigV4Provider — SigV4 signing on behalf of the agent.

Two operating modes:

1. **Container-creds mode** (default): broker exposes its STS-derived
   creds via `AWS_CONTAINER_CREDENTIALS_FULL_URI` style JSON. The
   agent's boto3 walks its credential chain, hits the broker's HTTP
   endpoint, fetches `{AccessKeyId, SecretAccessKey, Token, Expiration}`,
   and signs locally as usual. The agent does see ephemeral STS creds
   (``session_ttl_seconds``, default 30 min); the broker's underlying
   long-term key never leaves.

2. **Signing-on-behalf mode**: agent posts an unsigned canonical
   request to the broker; broker computes the SigV4 signature and
   returns just the headers. The agent never sees any AWS material at
   all. Use this for high-security deployments.

Config:
    role_arn: arn to assume. Required for container-creds mode: the broker
              vends only the short-lived STS session it assumes, never the
              credentials it holds itself.
    session_name_template: e.g. "storm-u{uid}-{persona_pid}". Rendered per
              request into the STS RoleSessionName, which CloudTrail records.
              ``{uid}`` is the agent's Unix uid as the broker verified it over
              SO_PEERCRED; every other placeholder is a value the agent
              supplied and is only as trustworthy as the agent. Characters
              outside STS's set ([A-Za-z0-9_+=,.@-]) become "-", and the
              result is cut to 64.
    session_ttl_seconds: 1800. At least 1200: botocore refreshes credentials
              that have under 15 minutes left on every call, and refuses
              ones under 10, so a session must outlive that window or every
              AWS call becomes a broker call and the broker's rate limit then
              fails real requests.
    region: "us-east-1"
    external_id: optional
    long_term_credentials: optional dict with access_key_id+secret_access_key
                           (otherwise broker uses the host's default chain)
"""

from __future__ import annotations

import re
import string
import threading
import time
from collections import OrderedDict
from typing import Any

from .base import Provider, ProviderResult, ProviderError

#: Session lifetime: long enough to clear botocore's 15-minute refresh window.
DEFAULT_TTL_SECONDS = 1800
MIN_TTL_SECONDS = 1200
#: Reuse a cached session only while it has more than this left.
REUSE_MARGIN_SECONDS = 15 * 60 + 60
CACHE_MAX = 256

#: Characters STS refuses in a RoleSessionName.
_NOT_STS = re.compile(r"[^A-Za-z0-9_+=,.@-]")


class _Unset(dict):
    def __missing__(self, key: str) -> str:
        return "unset"


def render_session_name(template: str, session_vars: dict[str, Any]) -> str:
    """The STS RoleSessionName for ``template``, always one STS accepts.

    The documented example template used "/", which STS refuses, so every
    credential fetch failed; and a placeholder the caller did not supply
    raised KeyError. Missing values render as "unset".
    """
    values = _Unset({k: str(v) for k, v in session_vars.items()})
    name = _NOT_STS.sub("-", string.Formatter().vformat(template, (), values))[:64]
    return name if len(name) >= 2 else "storm-broker"


class AwsSigV4Provider(Provider):
    name = "aws_sigv4"

    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__(config)
        self.role_arn = config.get("role_arn")
        self.region = config.get("region", "us-east-1")
        self.session_ttl = int(config.get("session_ttl_seconds", DEFAULT_TTL_SECONDS))
        if self.session_ttl < MIN_TTL_SECONDS:
            raise ProviderError(
                f"aws_sigv4 session_ttl_seconds must be at least {MIN_TTL_SECONDS}: botocore "
                "refreshes credentials with under 15 minutes left on every call"
            )
        self.session_name_template = config.get(
            "session_name_template", "storm-broker-default"
        )
        self.external_id = config.get("external_id")
        # Per session name: one cache for the provider handed whichever
        # persona asked first the credentials of the session made for another,
        # under that other persona's name in CloudTrail.
        self._cache: OrderedDict[str, tuple[dict[str, Any], float]] = OrderedDict()
        self._lock = threading.Lock()

    def _refresh_creds(self, session_name: str) -> dict[str, Any]:
        # Lazy import boto3 so tests don't pay for it unless they need it.
        import boto3
        sts = boto3.client("sts", region_name=self.region)
        if self.role_arn:
            kwargs: dict[str, Any] = {
                "RoleArn": self.role_arn,
                "RoleSessionName": session_name,
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
        # No role_arn: refuse. This used to hand the agent the broker's own
        # default-chain credentials, which can be long-term IAM user keys,
        # stamped with a made-up 2099 expiration so nothing ever refreshed
        # them. That broke the guarantee in the module docstring that the
        # broker's own key never leaves and the agent only sees a short-lived
        # STS session.
        raise ProviderError(
            "aws_sigv4 container-credentials mode requires role_arn: the broker "
            "vends only a short-lived STS session it assumes, never its own "
            "credentials. Set role_arn, or use signing-on-behalf mode."
        )

    def prepare_credentials(self, target: str, **session_vars: str) -> ProviderResult:
        """Container-creds mode: return JSON the agent's boto3 will read.

        A cached session is reused only while it has more than botocore's
        refresh window left (15 minutes, plus a minute): botocore asks again
        once a session is inside that window, and handing back the same one,
        as the cache did until a minute before expiry, made every AWS call a
        broker call. The cache holds at most ``CACHE_MAX`` sessions, oldest
        first out, because the session name includes labels the agent picks.
        """
        now = time.time()
        session_name = render_session_name(self.session_name_template, session_vars)
        with self._lock:
            cached = self._cache.get(session_name)
            if cached and now < cached[1] - REUSE_MARGIN_SECONDS:
                self._cache.move_to_end(session_name)
                return ProviderResult(credentials_json=cached[0])
        creds = self._refresh_creds(session_name)
        try:
            from datetime import datetime
            expires_at = datetime.fromisoformat(creds["Expiration"].replace("Z", "+00:00")).timestamp()
        except (ValueError, KeyError, AttributeError):
            expires_at = now + self.session_ttl
        with self._lock:
            for name in [n for n, (_c, exp) in self._cache.items() if exp <= now]:
                del self._cache[name]
            self._cache[session_name] = (creds, expires_at)
            self._cache.move_to_end(session_name)
            while len(self._cache) > CACHE_MAX:
                self._cache.popitem(last=False)
        return ProviderResult(credentials_json=creds)
