# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Server-side DLP backstop.

The Storm spec requires (Stage 13 addendum) that the server SHALL
apply a DLP scan to incoming events as a backstop to the agent-side
DLP. This module is the implementation.

Patterns are intentionally aligned with the storm-broker DLP set
(packages/storm-broker/storm_broker/dlp.py) so a leak detected on
either side fires the same `org.agentstorming.security_violation`
event.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass


DEFAULT_PATTERNS: list[tuple[str, str]] = [
    ("aws_access_key", r"\bAKIA[A-Z0-9]{16}\b"),
    ("aws_temp_key", r"\bASIA[A-Z0-9]{16}\b"),
    ("anthropic_key", r"\bsk-ant-[A-Za-z0-9_-]{20,}\b"),
    ("openai_key", r"\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}\b"),
    ("github_pat", r"\bghp_[A-Za-z0-9]{36}\b"),
    ("github_oauth", r"\bgho_[A-Za-z0-9]{36}\b"),
    ("github_user_token", r"\bghu_[A-Za-z0-9]{36}\b"),
    ("github_server_token", r"\bghs_[A-Za-z0-9]{36}\b"),
    ("gitlab_pat", r"\bglpat-[A-Za-z0-9_-]{20,}\b"),
    ("atlassian_token", r"\bATATT3xFfGF0[A-Za-z0-9_-]{40,}\b"),
    ("slack_bot_token", r"\bxoxb-[A-Za-z0-9-]{10,}\b"),
    ("slack_user_token", r"\bxoxp-[A-Za-z0-9-]{10,}\b"),
    ("stripe_live_key", r"\bsk_live_[A-Za-z0-9]{24,}\b"),
    ("stripe_test_key", r"\bsk_test_[A-Za-z0-9]{24,}\b"),
    ("twilio_account_sid", r"\bAC[a-f0-9]{32}\b"),
    ("sendgrid_key", r"\bSG\.[A-Za-z0-9_-]{22}\.[A-Za-z0-9_-]{43}\b"),
    ("jwt", r"\bey[A-Za-z0-9_-]+\.ey[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b"),
    ("pem_private_key", r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    ("postgres_url_creds", r"postgresql://[^\s:/@]+:[^\s@/]+@"),
    ("mysql_url_creds", r"mysql://[^\s:/@]+:[^\s@/]+@"),
    ("mongodb_url_creds", r"mongodb(?:\+srv)?://[^\s:/@]+:[^\s@/]+@"),
    ("high_entropy_40", r"[A-Za-z0-9_\-./+=]{40,}"),
]


@dataclass(frozen=True)
class DlpMatch:
    pattern_name: str
    span: tuple[int, int]


class DLPScanner:
    def __init__(self, patterns: list[tuple[str, str]] | None = None) -> None:
        self._compiled = [(name, re.compile(rx)) for name, rx in (patterns or DEFAULT_PATTERNS)]

    def scan(self, text: str) -> list[DlpMatch]:
        if not text:
            return []
        out: list[DlpMatch] = []
        for name, rx in self._compiled:
            for m in rx.finditer(text):
                if name == "high_entropy_40" and not _high_entropy(m.group(0)):
                    continue
                out.append(DlpMatch(name, m.span()))
        return out

    def is_clean(self, text: str) -> bool:
        return len(self.scan(text)) == 0


def _high_entropy(s: str) -> bool:
    if len(s) < 32:
        return False
    counts = Counter(s)
    total = len(s)
    h = -sum((c / total) * math.log2(c / total) for c in counts.values())
    return h >= 4.5


# Module-level default scanner reused by visibility.py
_DEFAULT = DLPScanner()


def event_dlp_violation(ev: dict) -> DlpMatch | None:
    """Return the first DLP match in an event's payload text, or None.

    Scans the event's payload `text` field (most-common message body
    location) and any string values in payload (catches whispers /
    attachments / etc.).
    """
    payload = ev.get("payload") or {}
    # Quick path: text field
    text = payload.get("text") or ""
    if text:
        m = _DEFAULT.scan(text)
        if m:
            return m[0]
    # Slow path: string values in payload
    for key, val in payload.items():
        if isinstance(val, str) and val and key != "text":
            m = _DEFAULT.scan(val)
            if m:
                return m[0]
    return None
