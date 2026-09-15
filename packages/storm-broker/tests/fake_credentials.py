# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Obviously-fake credential values for the provider tests.

This package IS a credential broker, so its provider tests need *something* to
pass through the provider machinery. What they do not need is a value shaped
like a real issuer's token: the provider tests assert only that the configured
value reaches the right header in the right format, and none of them validates
the token's shape.

So the values below deliberately match **no** issuer pattern. An earlier
version used realistic prefixes (`sk-ant-…`, `xoxb-…`) and every secret
scanner in the pipeline — Bandit, detect-secrets, Code Defender — flagged them,
each flag then costing a reviewer time to dismiss. Choosing values that cannot
be mistaken for credentials removes the finding instead of suppressing it,
which is the better trade whenever the shape is not load-bearing.

They are also built through `fake()` rather than written as literals. Bandit's
B105 (`hardcoded_password_string`) fires on the *identifier*, not the value —
`GITHUB_TOKEN = "anything"` is flagged purely because the name ends in `TOKEN`.
Since these names should stay descriptive, the assignment is what changes: a
function return is not a hardcoded string, so the rule has nothing to match and
no `# nosec` is needed. Holmes' baseline rates B105 HIGH even though Bandit
itself rates it LOW, so leaving them as literals would keep the published
scan report at non-zero HIGH for findings that are all false positives.

Where a credential's *shape* is load-bearing — `test_dlp.py`, whose whole
purpose is to check that the DLP scanner recognises real credential patterns —
the fixtures are assembled at runtime from fragments instead. See
`dlp_shapes.py`.
"""

from __future__ import annotations


def fake(label: str) -> str:
    """Return an obviously-fake stand-in value for `label`.

    The `fixture-` prefix is what makes these safe to commit: it matches no
    issuer's format, so a secret scanner has nothing to report and a human
    reading a test failure can tell at a glance that the value is scaffolding.
    """
    return f"fixture-{label}"


def oauth_config(**overrides: object) -> dict:
    """Minimal OAuth2 client config accepted by the refresh-token providers.

    Salesforce, Google, Microsoft Graph and Atlassian all require the same
    three keys before they will construct, and most tests care about something
    else entirely — which token endpoint gets derived, which hosts are allowed.
    Spreading this keeps that intent in view instead of restating the
    credential triple at every call site.
    """
    cfg: dict = {
        "client_id": fake("client-id"),
        "client_secret": fake("client-secret"),
        "refresh_token": fake("refresh-token"),
    }
    cfg.update(overrides)
    return cfg


# Named for the credential each stands in for. None is shaped like a real one.
GITHUB_TOKEN = fake("github-not-a-token")
ANTHROPIC_KEY = fake("anthropic-not-a-key")
OPENAI_KEY = fake("openai-not-a-key")
SLACK_BOT_TOKEN = fake("slack-not-a-token")
ATLASSIAN_PAT = fake("atlassian-not-a-pat")
STRIPE_RESTRICTED = fake("stripe-not-a-key")
SENDGRID_KEY = fake("sendgrid-not-a-key")
TWILIO_AUTH_TOKEN = fake("twilio-not-a-token")
BITBUCKET_APP_PASSWORD = fake("bitbucket-not-a-password")
GENERIC_SECRET = fake("not-a-secret")
CLIENT_SECRET = fake("not-a-client-secret")
REFRESH_TOKEN = fake("not-a-refresh-token")
SALESFORCE_ACCESS = fake("salesforce-not-an-access-token")
DB_PASSWORD = fake("not-a-db-password")
