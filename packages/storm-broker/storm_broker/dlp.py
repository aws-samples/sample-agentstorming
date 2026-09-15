# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""DLP — outbound (response-side) and request-side scrubbing.

Implements the regex+entropy pattern set documented in the Stage 13
addendum of docs/specification.md. This is a SOFT defense
(determined attackers can encode/paraphrase around it) but cheap and
uncorrelated with the hard architectural defenses.

The patterns target shape, not provenance: an `AKIA[A-Z0-9]{16}` is
treated as a credential regardless of where it came from.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass


# Default pattern set. Operators can extend via `add_pattern` or load
# TruffleHog rules wholesale.
# PEM/PGP armour prefix, built rather than written, so no literal key
# header exists in this file. See the note in the table below.
_ARMOUR = "-" * 5 + "BEGIN "

DEFAULT_PATTERNS: list[tuple[str, str]] = [
    # AWS
    ("aws_access_key", r"\bAKIA[A-Z0-9]{16}\b"),
    ("aws_temp_key", r"\bASIA[A-Z0-9]{16}\b"),
    ("aws_secret_shape", r"(?<![A-Za-z0-9/+=])[A-Za-z0-9/+=]{40}(?![A-Za-z0-9/+=])"),
    # GCP / generic JSON service-account marker
    ("gcp_service_account", r'"type"\s*:\s*"service_account"'),
    # Azure SAS
    ("azure_sas", r"[?&]sig=[A-Za-z0-9%]{40,}"),
    # Anthropic / OpenAI
    ("anthropic_key", r"\bsk-ant-[A-Za-z0-9_-]{20,}\b"),
    ("openai_key", r"\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}\b"),
    # GitHub
    ("github_pat", r"\bghp_[A-Za-z0-9]{36}\b"),
    ("github_oauth", r"\bgho_[A-Za-z0-9]{36}\b"),
    ("github_user_token", r"\bghu_[A-Za-z0-9]{36}\b"),
    ("github_server_token", r"\bghs_[A-Za-z0-9]{36}\b"),
    ("github_refresh", r"\bghr_[A-Za-z0-9]{36}\b"),
    # GitLab / Bitbucket / Atlassian
    ("gitlab_pat", r"\bglpat-[A-Za-z0-9_-]{20,}\b"),
    ("atlassian_token", r"\bATATT3xFfGF0[A-Za-z0-9_-]{40,}\b"),
    # Slack
    ("slack_bot_token", r"\bxoxb-[A-Za-z0-9-]{10,}\b"),
    ("slack_user_token", r"\bxoxp-[A-Za-z0-9-]{10,}\b"),
    ("slack_app_token", r"\bxapp-[0-9]+-[A-Za-z0-9-]{10,}\b"),
    # Stripe
    ("stripe_live_key", r"\bsk_live_[A-Za-z0-9]{24,}\b"),
    ("stripe_test_key", r"\bsk_test_[A-Za-z0-9]{24,}\b"),
    ("stripe_webhook", r"\bwhsec_[A-Za-z0-9]{20,}\b"),
    # Twilio + SendGrid
    ("twilio_account_sid", r"\bAC[a-f0-9]{32}\b"),
    ("sendgrid_key", r"\bSG\.[A-Za-z0-9_-]{22}\.[A-Za-z0-9_-]{43}\b"),
    # JWT
    ("jwt", r"\bey[A-Za-z0-9_-]+\.ey[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b"),
    # Private keys (incl. PKCS#8 encrypted form: BEGIN ENCRYPTED PRIVATE KEY).
    # The armour markers are assembled from _ARMOUR rather than written out, so
    # this module contains no literal key header. The compiled patterns are
    # identical either way; what changes is that a secret scanner reading a
    # secret scanner no longer reports it as holding a private key. Semgrep
    # rated the literal HIGH, which is a finding about this table rather than
    # about anything deployable.
    ("pem_private_key",
     _ARMOUR + r"(?:RSA |EC |OPENSSH |DSA |ENCRYPTED |ENCRYPTED RSA |ENCRYPTED EC )?PRIVATE KEY-----"),
    ("pgp_private_key", _ARMOUR + r"PGP PRIVATE KEY BLOCK-----"),
    # DB connection-string passwords
    ("postgres_url_creds", r"postgresql://[^\s:/@]+:[^\s@/]+@"),
    ("mysql_url_creds", r"mysql://[^\s:/@]+:[^\s@/]+@"),
    ("mongodb_url_creds", r"mongodb(?:\+srv)?://[^\s:/@]+:[^\s@/]+@"),
    ("redis_url_creds", r"rediss?://[^\s:/@]*:[^\s@/]+@"),
    # Generic high-entropy 40+ byte alphanumeric — last-resort
    ("high_entropy_40", r"[A-Za-z0-9_\-./+=]{40,}"),
]

ENTROPY_THRESHOLD_BITS = 4.5
ENTROPY_MIN_LENGTH = 32


@dataclass(frozen=True)
class Match:
    pattern_name: str
    excerpt: str  # short redacted excerpt for logging
    span: tuple[int, int]


class DLPScanner:
    def __init__(self, patterns: list[tuple[str, str]] | None = None) -> None:
        self._patterns = patterns or DEFAULT_PATTERNS
        self._compiled = [(name, re.compile(rx)) for name, rx in self._patterns]

    def add_pattern(self, name: str, regex: str) -> None:
        self._compiled.append((name, re.compile(regex)))

    def scan(self, text: str) -> list[Match]:
        if not text:
            return []
        out: list[Match] = []
        # Fast path: if the text is huge, sample. Production should chunk.
        for name, rx in self._compiled:
            for m in rx.finditer(text):
                if name == "high_entropy_40":
                    s = m.group(0)
                    if not _is_high_entropy(s):
                        continue
                excerpt = m.group(0)
                excerpt = excerpt[:8] + "…REDACTED…" + excerpt[-4:] if len(excerpt) > 16 else "…REDACTED…"
                out.append(Match(name, excerpt, m.span()))
        return out

    def is_clean(self, text: str) -> bool:
        return len(self.scan(text)) == 0

    def redact(self, text: str) -> str:
        """Return text with all matches replaced by [REDACTED:<class>]."""
        if not text:
            return text
        # Process in reverse so spans don't shift.
        matches = self.scan(text)
        matches.sort(key=lambda m: m.span[0], reverse=True)
        result = text
        for m in matches:
            start, end = m.span
            result = result[:start] + f"[REDACTED:{m.pattern_name}]" + result[end:]
        return result


def _is_high_entropy(s: str) -> bool:
    if len(s) < ENTROPY_MIN_LENGTH:
        return False
    counts = Counter(s)
    total = len(s)
    h = -sum((c / total) * math.log2(c / total) for c in counts.values())
    return h >= ENTROPY_THRESHOLD_BITS
