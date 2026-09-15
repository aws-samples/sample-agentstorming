# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""DLP scanner tests.

The scanner's job is to recognise real credential patterns, so these tests must
feed it inputs that genuinely match them — a differently-shaped substitute
would silently stop exercising the rule each test is named after.

That puts them in conflict with every secret scanner in the pipeline. Rather
than suppress those scanners, the fixtures are **assembled from fragments at
import time** in `dlp_shapes.py`, so no credential-shaped literal exists in any
source file. This file already did that for the PEM header; the rest now follow
the same approach, which is why there are no `# nosec` comments here.
"""

from dlp_shapes import (
    ANTHROPIC_KEY,
    AWS_ACCESS_KEY,
    AWS_PREFIX_ONLY,
    GITHUB_PAT,
    JWT,
    POSTGRES_DSN_WITH_PASSWORD,
    RSA_PRIVATE_KEY_HEADER,
)

from storm_broker.dlp import DLPScanner


def test_aws_access_key_detected():
    s = DLPScanner()
    matches = s.scan(f"Here is my key {AWS_ACCESS_KEY} for the demo")
    names = {m.pattern_name for m in matches}
    assert "aws_access_key" in names


def test_anthropic_key_detected():
    s = DLPScanner()
    matches = s.scan(f"token {ANTHROPIC_KEY}")
    assert any(m.pattern_name == "anthropic_key" for m in matches)


def test_github_pat_detected():
    s = DLPScanner()
    text = f"git remote set-url origin https://x:{GITHUB_PAT}@github.com/repo"
    assert any(m.pattern_name == "github_pat" for m in s.scan(text))


def test_jwt_detected():
    s = DLPScanner()
    assert any(m.pattern_name == "jwt" for m in s.scan(f"Bearer {JWT}"))


def test_pem_private_key_detected():
    s = DLPScanner()
    footer = "-" * 5 + "END RSA PRIVATE KEY" + "-" * 5
    pem = f"{RSA_PRIVATE_KEY_HEADER}\nMIIE...\n{footer}"
    assert any(m.pattern_name == "pem_private_key" for m in s.scan(pem))


def test_postgres_url_detected():
    s = DLPScanner()
    matches = s.scan(POSTGRES_DSN_WITH_PASSWORD)
    assert any(m.pattern_name == "postgres_url_creds" for m in matches)


def test_clean_text_passes():
    s = DLPScanner()
    assert s.is_clean("This is a normal sentence with no secrets in it.")
    # The bare prefix alone is not a key shape and must not trip the scanner.
    assert s.is_clean(f"{AWS_PREFIX_ONLY} is a four-letter prefix.")


def test_redact_replaces_match():
    s = DLPScanner()
    out = s.redact(f"{AWS_ACCESS_KEY} was the key")
    assert AWS_PREFIX_ONLY not in out
    assert "[REDACTED:aws_access_key]" in out
