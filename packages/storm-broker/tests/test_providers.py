# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Provider tests."""

import base64
import os
import pytest

from storm_broker.providers import (
    BearerTokenProvider, BasicAuthProvider, RequestSignerProvider,
)
from storm_broker.providers.base import ProviderError
from fake_credentials import GITHUB_TOKEN, fake


def test_bearer_token_inline():
    p = BearerTokenProvider({"token": GITHUB_TOKEN, "host_allowlist": ["api.github.com"]})
    r = p.prepare_headers("github", "GET", "https://api.github.com/user")
    assert r.headers == {"Authorization": f"Bearer {GITHUB_TOKEN}"}


def test_bearer_token_format_override():
    secret = fake("api-key")
    p = BearerTokenProvider({
        "token": secret, "header_name": "X-Api-Key", "header_format": "{token}",
    })
    r = p.prepare_headers("anth", "POST", "https://api.anthropic.com/v1/messages")
    assert r.headers == {"X-Api-Key": secret}


def test_bearer_token_host_allowlist_blocks():
    p = BearerTokenProvider({"token": fake("token"), "host_allowlist": ["api.allowed.com"]})
    with pytest.raises(ProviderError):
        p.prepare_headers("blocked", "GET", "https://api.blocked.com/")


def test_bearer_token_from_env(monkeypatch):
    from_env = fake("token-from-env")
    monkeypatch.setenv("STORM_TEST_TOKEN", from_env)
    # nosec B105 — "STORM_TEST_TOKEN" is an environment variable *name*; the
    # token itself is set above via monkeypatch and never appears literally.
    p = BearerTokenProvider({"token_env": "STORM_TEST_TOKEN"})  # nosec B105
    r = p.prepare_headers("x", "GET", "https://x")
    assert r.headers["Authorization"] == f"Bearer {from_env}"


def test_bearer_missing_token_raises():
    with pytest.raises(ProviderError):
        BearerTokenProvider({})


def test_basic_auth():
    # `secret` as a local name is what detect-secrets' keyword detector matches,
    # not the value — which comes from fake(). Renamed rather than allowlisted.
    user, passphrase = "alice", fake("password")
    p = BasicAuthProvider({"username": user, "password": passphrase})
    r = p.prepare_headers("x", "GET", "https://x")
    expected = base64.b64encode(f"{user}:{passphrase}".encode()).decode("ascii")
    assert r.headers == {"Authorization": f"Basic {expected}"}


def test_request_signer_hmac_sha256():
    p = RequestSignerProvider({"secret": fake("signing-key"), "algorithm": "hmac-sha256"})
    r = p.prepare_headers("x", "POST", "https://x/y", body=b"hello")
    sig = r.headers["X-Signature"]
    # Just confirm it's a 64-hex string
    assert len(sig) == 64 and all(c in "0123456789abcdef" for c in sig)


def test_request_signer_unknown_algo():
    p = RequestSignerProvider({"secret": fake("signing-key"), "algorithm": "rot13"})
    with pytest.raises(ProviderError):
        p.prepare_headers("x", "GET", "https://x")


# ---- AwsSigV4Provider: container-creds mode vends STS sessions only ---------


def _stub_boto3(monkeypatch, assumed: list):
    """A boto3 stand-in: STS AssumeRole works; the default chain must not be read."""
    import sys
    import types
    from datetime import datetime, timedelta, timezone

    class _Sts:
        def assume_role(self, **kwargs):
            assumed.append(kwargs)
            return {"Credentials": {
                "AccessKeyId": "ASIA" + "X" * 16, "SecretAccessKey": fake("sts-secret"),
                "SessionToken": fake("sts-token"),
                "Expiration": datetime.now(timezone.utc) + timedelta(seconds=kwargs["DurationSeconds"]),
            }}

    def _session(*_a, **_k):
        raise AssertionError("the provider read the broker's own default-chain credentials")

    mod = types.ModuleType("boto3")
    mod.client = lambda *_a, **_k: _Sts()
    mod.Session = _session
    monkeypatch.setitem(sys.modules, "boto3", mod)


def test_aws_provider_without_role_arn_refuses_rather_than_leaking_host_credentials(monkeypatch):
    from storm_broker.providers import AwsSigV4Provider
    _stub_boto3(monkeypatch, [])
    p = AwsSigV4Provider({"region": "us-east-1"})
    with pytest.raises(ProviderError, match="requires role_arn"):
        p.prepare_credentials("aws")


def test_aws_provider_vends_a_short_lived_sts_session(monkeypatch):
    from datetime import datetime, timezone
    from storm_broker.providers import AwsSigV4Provider
    assumed: list = []
    _stub_boto3(monkeypatch, assumed)
    p = AwsSigV4Provider({"role_arn": "arn:aws:iam::123456789012:role/agent", "session_ttl_seconds": 1800})
    creds = p.prepare_credentials("aws").credentials_json
    assert assumed and assumed[0]["DurationSeconds"] == 1800
    expires = datetime.fromisoformat(creds["Expiration"])
    assert (expires - datetime.now(timezone.utc)).total_seconds() <= 1800
    assert creds["AccessKeyId"].startswith("ASIA") and creds["Token"]


# ---- DBConnectionBrokerProvider: no silent cleartext to a remote database ----


def _db_config(**overrides):
    cfg = {"driver": "raw_tcp", "host": "127.0.0.1", "port": 5432, "password": fake("db-password")}
    cfg.update(overrides)
    return cfg


@pytest.mark.parametrize("host", ["127.0.0.1", "::1", "localhost"])
def test_db_connection_allows_loopback(host):
    from storm_broker.providers.db_connection import DBConnectionBrokerProvider
    DBConnectionBrokerProvider(_db_config(host=host))


@pytest.mark.parametrize("host", ["10.0.4.17", "db.internal.example", "2001:db8::5"])
def test_db_connection_refuses_cleartext_to_a_remote_host(host):
    from storm_broker.providers.db_connection import DBConnectionBrokerProvider
    with pytest.raises(ProviderError, match="cleartext"):
        DBConnectionBrokerProvider(_db_config(host=host))


def test_db_connection_remote_cleartext_needs_an_explicit_opt_in():
    from storm_broker.providers.db_connection import DBConnectionBrokerProvider
    DBConnectionBrokerProvider(_db_config(host="10.0.4.17", tls=False))


def test_db_connection_does_not_pretend_to_support_tls():
    from storm_broker.providers.db_connection import DBConnectionBrokerProvider
    with pytest.raises(ProviderError, match="does not support tls"):
        DBConnectionBrokerProvider(_db_config(host="10.0.4.17", tls=True))


def test_aws_sessions_are_cached_per_session_name(monkeypatch):
    """One cache for the provider handed persona B the session made for A."""
    from storm_broker.providers import AwsSigV4Provider
    assumed: list = []
    _stub_boto3(monkeypatch, assumed)
    p = AwsSigV4Provider({"role_arn": "arn:aws:iam::123456789012:role/agent",
                          "session_name_template": "storm-u{uid}-{persona_pid}"})
    p.prepare_credentials("aws", uid="1001", persona_pid="alpha")
    p.prepare_credentials("aws", uid="1001", persona_pid="alpha")
    p.prepare_credentials("aws", uid="1002", persona_pid="beta")
    assert [a["RoleSessionName"] for a in assumed] == ["storm-u1001-alpha", "storm-u1002-beta"]


@pytest.mark.parametrize("template,session_vars,expected", [
    ("storm/{room_id}/{persona_pid}/{task_id}", {"room_id": "r1", "persona_pid": "p@x"}, "storm-r1-p@x-unset"),
    ("{persona_pid}", {"persona_pid": "ünïcode and spaces"}, "-n-code-and-spaces"),
    ("x", {}, "storm-broker"),
    ("s-{v}", {"v": "a" * 100}, "s-" + "a" * 62),
])
def test_aws_session_names_are_always_valid_for_sts(template, session_vars, expected):
    import re

    from storm_broker.providers.aws import render_session_name
    name = render_session_name(template, session_vars)
    assert name == expected
    assert re.fullmatch(r"[A-Za-z0-9_+=,.@-]{2,64}", name)


@pytest.mark.parametrize("family,host", [("AF_INET", "127.0.0.1"), ("AF_INET6", "::1")])
def test_db_connection_reaches_a_loopback_listener_of_either_family(family, host):
    """::1 passed the loopback check but the provider opened only AF_INET sockets."""
    import socket
    fam = getattr(socket, family)
    try:
        srv = socket.socket(fam, socket.SOCK_STREAM)
        srv.bind((host, 0))
    except OSError:
        pytest.skip(f"no {family} loopback on this host")
    srv.listen(1)
    port = srv.getsockname()[1]
    from storm_broker.providers.db_connection import DBConnectionBrokerProvider
    p = DBConnectionBrokerProvider(_db_config(host=host, port=port))
    conn = p.open_connection()
    try:
        peer, _ = srv.accept()
        peer.close()
        assert conn.family == fam and conn.gettimeout() is None
    finally:
        conn.close()
        srv.close()


def test_aws_session_ttl_must_clear_botocores_refresh_window(monkeypatch):
    """At 900 s, botocore refreshed on every call and the broker's limit then
    failed real AWS requests."""
    from storm_broker.providers import AwsSigV4Provider
    _stub_boto3(monkeypatch, [])
    with pytest.raises(ProviderError, match="at least 1200"):
        AwsSigV4Provider({"role_arn": "arn:aws:iam::123456789012:role/agent", "session_ttl_seconds": 900})


def test_aws_a_session_inside_botocores_window_is_not_handed_back(monkeypatch):
    import time as _time

    from storm_broker.providers import AwsSigV4Provider
    assumed: list = []
    _stub_boto3(monkeypatch, assumed)
    p = AwsSigV4Provider({"role_arn": "arn:aws:iam::123456789012:role/agent"})
    p.prepare_credentials("aws", uid="1")
    p.prepare_credentials("aws", uid="1")
    assert len(assumed) == 1, "a fresh 30-minute session is reused"
    name = next(iter(p._cache))
    creds, _exp = p._cache[name]
    p._cache[name] = (creds, _time.time() + 14 * 60)   # inside botocore's 15 minutes
    p.prepare_credentials("aws", uid="1")
    assert len(assumed) == 2, "a session botocore would refresh must be replaced, not returned"


def test_aws_the_session_cache_is_bounded(monkeypatch):
    """Session names include agent-chosen labels, so the cache must not grow per label."""
    from storm_broker.providers import AwsSigV4Provider
    from storm_broker.providers.aws import CACHE_MAX
    _stub_boto3(monkeypatch, [])
    p = AwsSigV4Provider({"role_arn": "arn:aws:iam::123456789012:role/agent",
                          "session_name_template": "s-{persona_pid}"})
    for i in range(CACHE_MAX + 50):
        p.prepare_credentials("aws", persona_pid=f"p{i}")
    assert len(p._cache) == CACHE_MAX
