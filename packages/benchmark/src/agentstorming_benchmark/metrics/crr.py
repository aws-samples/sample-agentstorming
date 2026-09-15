# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Cross-Reference Rate (CRR).

Fraction of assistant messages that reference another specific prior
message, either by:
  - speaker name (e.g. "the mathematician noted...", "I agree with @physics-scientist"),
  - message number or round index,
  - paraphrase of a prior speaker's argument (LLM-judge fallback).

The regex variant is fast and used by default; the LLM-judge variant is
used on a subsample (default 10%) of questions for validation. The paper
reports the regex CRR; the LLM-judge CRR is included in the supplement.
"""

from __future__ import annotations

import re
from typing import Sequence

from ..types import Message
from ..personas import SPECIALISTS


_SPEAKERS = list(SPECIALISTS.keys()) + ["project-lead", "moderator", "orchestrator", "single",
                                        "drafter", "reviser", "critic"]


def _mentions_peer(msg: Message, peers: set[str]) -> bool:
    text = msg.content.lower()
    if "@" in text:
        for p in peers:
            if f"@{p.lower()}" in text:
                return True
    for p in peers:
        if f"the {p.lower()}" in text:
            return True
        name_tokens = p.lower().replace("-", " ").split()
        # e.g. "deep-learning-scientist" -> check "deep learning scientist" as a phrase
        phrase = " ".join(name_tokens)
        if phrase in text and len(name_tokens) >= 2:
            return True
    # Agreeing/disagreeing/building on patterns
    patterns = [
        r"\bi\s+agree\s+with\b",
        r"\bi\s+disagree\s+with\b",
        r"\bbuilding\s+on\b",
        r"\btaking\s+.{0,20}\s+point\b",
        r"\bto\s+.{0,20}\s+point\b",
        r"\bin\s+response\s+to\b",
        r"\bas\s+(?:the|.{0,30})\s+noted\b",
        r"\bextending\s+.{0,30}\s+argument\b",
        r"\bcontra(?:ry)?\s+to\b",
        r"\bcontradict(?:ing|s)?\b",
        r"\bcounter(?:ing|-example)\b",
    ]
    return any(re.search(p, text) for p in patterns)


def cross_reference_rate(transcript: Sequence[Message]) -> float:
    assistants = [m for m in transcript if m.role == "assistant"]
    if len(assistants) < 2:
        return 0.0
    n_with_ref = 0
    peer_set: set[str] = set()
    for i, m in enumerate(assistants):
        if i == 0:
            peer_set.add(m.speaker)
            continue
        if _mentions_peer(m, peer_set):
            n_with_ref += 1
        peer_set.add(m.speaker)
    return n_with_ref / (len(assistants) - 1)
