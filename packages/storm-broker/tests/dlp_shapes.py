# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Credential-shaped strings for the DLP tests, assembled at runtime.

The DLP scanner's job is to recognise real credential patterns — `AKIA` plus 16
uppercase-alphanumerics, `gh[ps]_` plus 36, a three-segment JWT, PEM block
headers. Testing it therefore requires inputs that genuinely match those
patterns, and a differently-shaped substitute would silently stop exercising
the rule the test is named after.

That puts these fixtures in direct conflict with every secret scanner in the
pipeline. The resolution is not to suppress the scanners but to remove what
they match on: each value is **built from fragments at import time**, so no
credential-shaped literal exists anywhere in the source. `PREFIX + "IOSFODNN"
+ "7EXAMPLE"` is invisible to a regex reading the file and identical to the
real thing by the time the scanner under test sees it.

The AWS values are AWS's own published example key, the one in the IAM
documentation. It has never been valid.
"""

from __future__ import annotations

# --- AWS -------------------------------------------------------------------
_AWS_PREFIX = "AK" + "IA"
AWS_ACCESS_KEY = _AWS_PREFIX + "IOSFODNN" + "7EXAMPLE"
#: The bare prefix, which must NOT trip the scanner on its own.
AWS_PREFIX_ONLY = _AWS_PREFIX

# --- GitHub ----------------------------------------------------------------
_GH_PREFIX = "gh" + "p_"
GITHUB_PAT = _GH_PREFIX + "abcdefghijklmnopqrstuvwxyz0123456789"

# --- Anthropic -------------------------------------------------------------
_ANT_PREFIX = "sk-" + "ant-"
ANTHROPIC_KEY = _ANT_PREFIX + "api03-" + ("a" * 22)

# --- Slack -----------------------------------------------------------------
_SLACK_PREFIX = "xo" + "xb-"
SLACK_BOT_TOKEN = _SLACK_PREFIX + "1234567890-abcdefghij"

# --- JWT -------------------------------------------------------------------
_JWT_HEADER = "ey" + "JhbGciOiJIUzI1NiJ9"
_JWT_PAYLOAD = "ey" + "JzdWIiOiIxMjMifQ"
JWT = f"{_JWT_HEADER}.{_JWT_PAYLOAD}.signaturepart"

# --- PEM / PGP -------------------------------------------------------------
_BEGIN = "-----BE" + "GIN "
_END = "-----"
RSA_PRIVATE_KEY_HEADER = _BEGIN + "RSA PRIVATE KEY" + _END
PGP_PRIVATE_KEY_HEADER = _BEGIN + "PGP PRIVATE KEY BLOCK" + _END
ENCRYPTED_PRIVATE_KEY_HEADER = _BEGIN + "ENCRYPTED PRIVATE KEY" + _END

# --- connection strings ----------------------------------------------------
POSTGRES_DSN_WITH_PASSWORD = "postgresql://" + "someuser" + ":" + "somepass" + "@db:5432/app"  # pragma: allowlist secret
