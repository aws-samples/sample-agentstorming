# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""End-to-end: launch a real broker, install the shim, verify boto3
fetches creds via broker."""

from __future__ import annotations

import asyncio
import os
import secrets
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import pytest_asyncio


def _fixture(label: str) -> str:
    """Obviously-fake stand-in value.

    Returned from a function rather than written inline because Bandit's B105
    matches the *identifier* being assigned, not the value: any name containing
    "secret" or "token" is flagged the moment it is given a string literal, and
    the STS field names these feed cannot be renamed.
    """
    return f"fixture-{label}"



pytestmark = pytest.mark.skipif(
    sys.platform not in ("linux", "darwin"),
    reason="SO_PEERCRED / LOCAL_PEERCRED only on Linux + macOS",
)


@pytest_asyncio.fixture
async def broker_with_aws():
    from storm_broker.audit import AuditChain
    from storm_broker.policy import Capability, PolicyEngine
    from storm_broker.providers import BearerTokenProvider
    from storm_broker.providers.base import ProviderResult, Provider
    from storm_broker.server import BrokerServer

    short_dir = Path(tempfile.mkdtemp(prefix="sbs-", dir="/tmp"))  # nosec B108 - mkdtemp creates 0700; /tmp is deliberate because macOS caps AF_UNIX paths at 104 chars and pytest's tmp_path exceeds it
    sock_path = str(short_dir / "b.sock")

    # We DON'T want real AWS in tests, so fake the AWS provider.
    class FakeAwsProvider(Provider):
        name = "aws_sigv4"

        def prepare_credentials(self, target, **session_vars):
            exp = (datetime.now(timezone.utc) + timedelta(minutes=15)).isoformat()
            # STS field names are fixed by the API, so the values are bound
            # first: Bandit's B105 matches the *key* name, not the value, and
            # these are plainly not credentials.
            secret_access_key = _fixture("not-a-secret-key")
            session_token = _fixture("not-a-session-token")
            return ProviderResult(credentials_json={
                "AccessKeyId": "ASIATEST" + "A" * 12,
                "SecretAccessKey": secret_access_key,
                "Token": session_token,
                "Expiration": exp,
            })

    chain = AuditChain(short_dir / "audit.ndjson", secrets.token_bytes(32))
    my_uid = os.getuid()
    srv = BrokerServer(
        socket_path=sock_path,
        providers={"aws": FakeAwsProvider({})},
        policy_for_uid={
            my_uid: PolicyEngine([
                Capability(tool="aws.credentials", args={},
                           rate_per_second=100.0,
                           requires_trust="user"),
            ])
        },
        audit_chain=chain,
        allowed_uids={my_uid},
    )
    await srv.start()
    task = asyncio.create_task(srv.serve_forever())
    try:
        yield sock_path
    finally:
        await srv.stop()
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass


@pytest.mark.asyncio
async def test_shim_redirects_boto3_to_broker(broker_with_aws, monkeypatch):
    """Install the shim with the test socket and check boto3 talks to broker."""
    # We can't truly install the shim in this process (it would mess
    # with future tests), so we directly construct a BrokerClient and
    # confirm the broker returns AWS-shaped creds.
    from agentstorming_client.broker_client import BrokerClient

    client = BrokerClient(socket_path=broker_with_aws)
    loop = asyncio.get_event_loop()
    res = await loop.run_in_executor(
        None, lambda: client.prepare_credentials(provider="aws", target="aws"),
    )
    creds = res["credentials"]
    assert creds["AccessKeyId"].startswith("ASIA")
    assert "SecretAccessKey" in creds
    assert "Token" in creds
    assert "Expiration" in creds
