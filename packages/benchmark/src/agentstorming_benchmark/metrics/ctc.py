# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Convergence-to-Concrete (CtC).

Is the final statement produced by the configuration (for orchestrator-led,
moderator-led, or self-refine configs: the last assistant message; for MAD /
AS-free-nomod: the majority-vote summary) **concrete, actionable, testable**?

We grade the final statement on a 0-3 rubric via an LLM judge. Dimensions:
  - testable: names a specific quantity to be measured (0/1).
  - actionable: names a specific dataset, metric, or procedure (0/1).
  - traceable: references at least one prior speaker / piece of evidence (0/1).

Sum gives a score in [0, 3]. We report the mean across transcripts.
"""

from __future__ import annotations

import re

from ..models import ChatMessage, converse
from ..config import CONSTANTS, JUDGE_MODEL


JUDGE_SYSTEM = (
    "You are a strict rubric-based scorer. Read the candidate FINAL STATEMENT"
    " and respond with three lines of the form `KEY: 0` or `KEY: 1`, where"
    " KEY is one of `testable`, `actionable`, `traceable`. No extra text."
)

RUBRIC = (
    "Rubric:\n"
    "  testable = 1 iff the statement names a specific quantitative criterion"
    " that would let a human decide if the claim is true or false.\n"
    "  actionable = 1 iff the statement names a specific dataset, model,"
    " parameter, or procedure that someone could run.\n"
    "  traceable = 1 iff the statement credits or builds on prior contributions"
    " (naming a speaker, a specific argument, or a cited reference).\n"
    "Only answer with the three KEY: VALUE lines, nothing else."
)


_KEYS = ("testable", "actionable", "traceable")


def _parse(text: str) -> dict[str, int]:
    out = {k: 0 for k in _KEYS}
    for k in _KEYS:
        m = re.search(rf"{k}\s*:\s*([01])", text, re.I)
        if m:
            out[k] = int(m.group(1))
    return out


async def convergence_to_concrete(final_statement: str) -> tuple[int, dict[str, int]]:
    if not final_statement.strip():
        return 0, {k: 0 for k in _KEYS}
    prompt = (
        f"{RUBRIC}\n\n"
        f"FINAL STATEMENT:\n---\n{final_statement.strip()[:4000]}\n---\n"
    )
    text, _ = await converse(
        [ChatMessage(role="user", text=prompt)],
        system=JUDGE_SYSTEM,
        model=JUDGE_MODEL,
        temperature=CONSTANTS.judge_temperature,
        max_tokens=CONSTANTS.judge_max_output_tokens,
    )
    scores = _parse(text)
    return sum(scores.values()), scores
