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
