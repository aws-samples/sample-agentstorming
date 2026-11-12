# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Tests for SaaS-specific providers."""

import base64

import pytest

from storm_broker.providers import (
    AnthropicProvider, BitbucketProvider, GitHubProvider, JiraProvider,
    OpenAIProvider, SendGridProvider, SlackProvider, StripeProvider,
    TwilioProvider,
)
from storm_broker.providers.base import ProviderError
from fake_credentials import fake, oauth_config, ANTHROPIC_KEY, ATLASSIAN_PAT, BITBUCKET_APP_PASSWORD, GITHUB_TOKEN, OPENAI_KEY, SALESFORCE_ACCESS, SENDGRID_KEY, SLACK_BOT_TOKEN, STRIPE_RESTRICTED, TWILIO_AUTH_TOKEN


def test_github_default_bearer():
    p = GitHubProvider({"token": GITHUB_TOKEN})
    r = p.prepare_headers("github", "GET", "https://api.github.com/user")
    assert r.headers == {"Authorization": f"Bearer {GITHUB_TOKEN}"}


def test_github_legacy_format():
    p = GitHubProvider({"token": GITHUB_TOKEN, "legacy_format": True})
    r = p.prepare_headers("github", "GET", "https://api.github.com/user")
    assert r.headers == {"Authorization": f"token {GITHUB_TOKEN}"}


def test_github_blocks_other_hosts():
    p = GitHubProvider({"token": GITHUB_TOKEN})
    with pytest.raises(ProviderError):
        p.prepare_headers("github", "GET", "https://api.attacker.com/")


def test_anthropic_uses_x_api_key():
    p = AnthropicProvider({"token": ANTHROPIC_KEY})
    r = p.prepare_headers("anth", "POST", "https://api.anthropic.com/v1/messages")
    assert r.headers == {"x-api-key": ANTHROPIC_KEY}


def test_anthropic_blocks_other_hosts():
    p = AnthropicProvider({"token": ANTHROPIC_KEY})
    with pytest.raises(ProviderError):
        p.prepare_headers("anth", "POST", "https://attacker.com/")


def test_openai_bearer():
    p = OpenAIProvider({"token": OPENAI_KEY})
    r = p.prepare_headers("openai", "POST", "https://api.openai.com/v1/chat/completions")
    assert r.headers == {"Authorization": f"Bearer {OPENAI_KEY}"}


def test_slack_bearer():
    p = SlackProvider({"token": SLACK_BOT_TOKEN})
    r = p.prepare_headers("slack", "POST", "https://slack.com/api/chat.postMessage")
    assert r.headers == {"Authorization": f"Bearer {SLACK_BOT_TOKEN}"}


def test_slack_allows_hooks_subdomain():
    p = SlackProvider({"token": SLACK_BOT_TOKEN})
    r = p.prepare_headers("slack", "POST", "https://hooks.slack.com/services/abc/def/ghi")
    assert "Authorization" in r.headers


def test_jira_uses_host_config():
    p = JiraProvider({"token": ATLASSIAN_PAT, "host": "acme.atlassian.net"})
    r = p.prepare_headers("jira", "GET", "https://acme.atlassian.net/rest/api/3/issue/STORM-1")
    assert r.headers == {"Authorization": f"Bearer {ATLASSIAN_PAT}"}


def test_jira_blocks_other_hosts():
    p = JiraProvider({"token": fake("token"), "host": "acme.atlassian.net"})
    with pytest.raises(ProviderError):
        p.prepare_headers("jira", "GET", "https://other.atlassian.net/")


def test_stripe_bearer_pinned_to_api_stripe_com():
    p = StripeProvider({"token": STRIPE_RESTRICTED})
    r = p.prepare_headers("stripe", "GET", "https://api.stripe.com/v1/charges")
    assert r.headers == {"Authorization": f"Bearer {STRIPE_RESTRICTED}"}


def test_stripe_blocks_other_hosts():
    p = StripeProvider({"token": STRIPE_RESTRICTED})
    with pytest.raises(ProviderError):
        p.prepare_headers("stripe", "GET", "https://attacker.com/")


def test_sendgrid_bearer():
    p = SendGridProvider({"token": SENDGRID_KEY})
    r = p.prepare_headers("sendgrid", "POST", "https://api.sendgrid.com/v3/mail/send")
    assert r.headers == {"Authorization": f"Bearer {SENDGRID_KEY}"}


def test_twilio_basic_auth_with_account_sid():
    p = TwilioProvider({"account_sid": "ACxxxx", "auth_token": TWILIO_AUTH_TOKEN})
    r = p.prepare_headers(
        "twilio", "POST",
        "https://api.twilio.com/2010-04-01/Accounts/ACxxxx/Messages.json",
    )
    expected_b64 = base64.b64encode(f"ACxxxx:{TWILIO_AUTH_TOKEN}".encode()).decode("ascii")
    assert r.headers == {"Authorization": f"Basic {expected_b64}"}


def test_twilio_allows_lookups_subdomain():
    p = TwilioProvider({"account_sid": "ACxxxx", "auth_token": fake("auth-token")})
    r = p.prepare_headers(
        "twilio", "GET", "https://lookups.twilio.com/v2/PhoneNumbers/+15551234567"
    )
    assert "Authorization" in r.headers


def test_bitbucket_basic_auth_with_app_password():
    p = BitbucketProvider({"username": "alice", "app_password": BITBUCKET_APP_PASSWORD})
    r = p.prepare_headers(
        "bitbucket", "GET", "https://api.bitbucket.org/2.0/repositories/acme",
    )
    expected_b64 = base64.b64encode(f"alice:{BITBUCKET_APP_PASSWORD}".encode()).decode("ascii")
    assert r.headers == {"Authorization": f"Basic {expected_b64}"}


def test_bitbucket_blocks_other_hosts():
    p = BitbucketProvider({"username": "u", "password": fake("password")})
    with pytest.raises(ProviderError):
        p.prepare_headers("bitbucket", "GET", "https://bitbucket.attacker.com/")


def test_salesforce_requires_instance_host():
    from storm_broker.providers import SalesforceProvider
    with pytest.raises(ProviderError):
        SalesforceProvider(oauth_config())


def test_salesforce_uses_login_endpoint_for_prod(monkeypatch):
    from storm_broker.providers import SalesforceProvider

    p = SalesforceProvider(oauth_config(instance_host="acme.my.salesforce.com"))
    expected_endpoint = "https://login.salesforce.com/services/oauth2/token"
    assert p.token_endpoint == expected_endpoint
    assert "acme.my.salesforce.com" in p.host_allowlist


def test_salesforce_uses_test_endpoint_for_sandbox():
    from storm_broker.providers import SalesforceProvider
    p = SalesforceProvider(oauth_config(
        instance_host="acme--sb.sandbox.my.salesforce.com", sandbox=True,
    ))
    expected_endpoint = "https://test.salesforce.com/services/oauth2/token"
    assert p.token_endpoint == expected_endpoint


def test_salesforce_blocks_other_hosts(monkeypatch):
    from storm_broker.providers import SalesforceProvider

    p = SalesforceProvider(oauth_config(instance_host="acme.my.salesforce.com"))
    # Pre-seed an access token so we don't trigger a network call.
    p._access_token = fake("access-token")
    p._access_expires_at = 9_999_999_999.0
    with pytest.raises(ProviderError):
        p.prepare_headers(
            "salesforce", "GET", "https://other.my.salesforce.com/services/data/v60.0/",
        )


def test_salesforce_emits_bearer_with_refreshed_token(monkeypatch):
    from storm_broker.providers import SalesforceProvider

    p = SalesforceProvider(oauth_config(instance_host="acme.my.salesforce.com"))
    # Bypass the HTTP refresh — pretend we already minted an access token.
    p._access_token = SALESFORCE_ACCESS
    p._access_expires_at = 9_999_999_999.0
    r = p.prepare_headers(
        "salesforce", "GET",
        "https://acme.my.salesforce.com/services/data/v60.0/sobjects/Account",
    )
    assert r.headers == {"Authorization": f"Bearer {SALESFORCE_ACCESS}"}


def test_google_default_token_endpoint_and_hosts():
    from storm_broker.providers import GoogleOAuth2Provider
    p = GoogleOAuth2Provider(oauth_config())
    expected_endpoint = "https://oauth2.googleapis.com/token"
    assert p.token_endpoint == expected_endpoint
    assert "gmail.googleapis.com" in p.host_allowlist
    assert "drive.googleapis.com" in p.host_allowlist


def test_google_blocks_other_hosts():
    from storm_broker.providers import GoogleOAuth2Provider
    p = GoogleOAuth2Provider(oauth_config())
    p._access_token = fake("access-token")
    p._access_expires_at = 9_999_999_999.0
    with pytest.raises(ProviderError):
        p.prepare_headers("g", "GET", "https://attacker.com/")


def test_microsoft_graph_commercial_defaults():
    from storm_broker.providers import MicrosoftGraphProvider
    p = MicrosoftGraphProvider(oauth_config(tenant="abcd-1234"))
    expected_endpoint = "https://login.microsoftonline.com/abcd-1234/oauth2/v2.0/token"
    assert p.token_endpoint == expected_endpoint
    assert "graph.microsoft.com" in p.host_allowlist


def test_microsoft_graph_gcc_high_uses_us_endpoint():
    from storm_broker.providers import MicrosoftGraphProvider
    p = MicrosoftGraphProvider(oauth_config(tenant="abcd-1234", cloud="gcc_high"))
    assert "login.microsoftonline.us" in p.token_endpoint
    assert "graph.microsoft.us" in p.host_allowlist


def test_microsoft_graph_rejects_unknown_cloud():
    from storm_broker.providers import MicrosoftGraphProvider
    with pytest.raises(ProviderError):
        MicrosoftGraphProvider(oauth_config(tenant="t", cloud="atlantis"))


def test_atlassian_oauth2_defaults():
    from storm_broker.providers import AtlassianOAuth2Provider
    p = AtlassianOAuth2Provider(oauth_config())
    expected_endpoint = "https://auth.atlassian.com/oauth/token"
    assert p.token_endpoint == expected_endpoint
    assert p.host_allowlist == {"api.atlassian.com"}


class _EchoingTokenEndpoint:
    """A token endpoint that fails and echoes the request back, as some do."""

    def __init__(self, status: int, body: str, json_body=None):
        self.status, self.body, self.json_body = status, body, json_body

    def __call__(self, *args, **kwargs):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def post(self, url, data=None):
        endpoint = self

        class _Response:
            status_code = endpoint.status
            text = endpoint.body.format(**(data or {}))

            def json(self):
                if endpoint.json_body is None:
                    raise ValueError("not JSON")
                if endpoint.json_body == "too deep":
                    raise RecursionError("maximum recursion depth exceeded")
                return {k: v.format(**(data or {})) if isinstance(v, str) else v
                        for k, v in endpoint.json_body.items()}

        return _Response()


@pytest.mark.parametrize("status,body,json_body,expected", [
    (400, '{{"error":"invalid_grant","error_description":"bad token {refresh_token}"}}',
     {"error": "invalid_grant", "error_description": "bad token {refresh_token}"},
     "refresh failed: HTTP 400 (invalid_grant)"),
    (502, "<html>upstream sent {refresh_token} / {client_secret}</html>", None,
     "refresh failed: HTTP 502"),
    (400, "{{}}", {"error": "{client_secret}"}, "refresh failed: HTTP 400"),
    (400, "{{}}", {"error": {"message": "expired", "code": 190}}, "refresh failed: HTTP 400"),
    (400, "[[[[", "too deep", "refresh failed: HTTP 400"),
], ids=["oauth-error", "html", "non-standard-code", "object-error", "nested-too-deep"])
def test_a_failed_refresh_never_quotes_the_token_endpoint(monkeypatch, status, body, json_body, expected):
    """The error goes back to the agent. It used to carry 200 characters of the
    response body, which an echoing endpoint fills with the refresh token."""
    import httpx
    from storm_broker.providers import SalesforceProvider

    p = SalesforceProvider(oauth_config(instance_host="acme.my.salesforce.com"))
    monkeypatch.setattr(httpx, "Client", _EchoingTokenEndpoint(status, body, json_body))
    with pytest.raises(ProviderError) as e:
        p.prepare_headers("salesforce", "GET", "https://acme.my.salesforce.com/services/data/v60.0/")
    assert str(e.value) == expected
    assert p._refresh_token not in str(e.value) and p._client_secret not in str(e.value)
