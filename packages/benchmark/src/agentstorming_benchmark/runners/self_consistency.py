# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Self-consistency baseline (Wang et al. 2022, arXiv:2203.11171).

Generate K independent samples with temperature > 0, then majority-vote.
"""

from __future__ import annotations

import asyncio
import time
from collections import Counter

from .base import Runner, register
from ..types import Question, Message, TokenUsage, RunResult
from ..models import ChatMessage, converse, extract_multiple_choice, extract_numeric
from ..personas import NEUTRAL_EXPERT


def build_user_prompt(q: Question) -> str:
    """Format question as MCQ or math prompt."""
    if q.dataset in ("mmlu_pro", "gpqa_diamond", "musr", "musr_murder", "truthfulqa_mc", "arc_challenge", "xdomain_v4", "musr_object_placements", "musr_team_allocation"):
        from ..datasets.mmlu_pro import format_mcq_prompt
        return format_mcq_prompt(q)
    if q.dataset in ("math500", "dabstep_hard"):
        from ..datasets.math500 import format_math_prompt
        return format_math_prompt(q)
    # Fallback: generic prompt.
    return f"Question: {q.question}\n\nProvide your answer."


def extract_answer(q: Question, text: str) -> str:
    """Extract answer from model response."""
    if q.dataset in ("mmlu_pro", "gpqa_diamond", "musr", "musr_murder", "truthfulqa_mc", "arc_challenge", "xdomain_v4", "musr_object_placements", "musr_team_allocation"):
        letters = [chr(ord("A") + i) for i in range(len(q.choices or []))]
        return extract_multiple_choice(text, letters, choice_texts=q.choices)
    return extract_numeric(text)


@register("sc_k5")
class SelfConsistencyK5Runner(Runner):
    """Self-consistency with K=5 samples."""

    async def run(self, question: Question, seed: int) -> RunResult:
        t0 = time.time()
        usage = TokenUsage()
        user_prompt = build_user_prompt(question)
        transcript = [
            Message(role="system", speaker="system", content=NEUTRAL_EXPERT),
            Message(role="user", speaker="user", content=user_prompt),
        ]

        # Generate K=5 independent samples.
        tasks = []
        for sample_idx in range(5):
            temp = 0.7 + 0.1 * (sample_idx % 3)  # Vary temperature slightly.
            tasks.append(
                converse(
                    [ChatMessage(role="user", text=user_prompt)],
                    system=NEUTRAL_EXPERT,
                    temperature=temp,
                )
            )

        results = await asyncio.gather(*tasks)
        answers = []
        for idx, (text, u) in enumerate(results):
            usage += u
            transcript.append(Message(role="assistant", speaker=f"sample-{idx+1}", content=text))
            ans = extract_answer(question, text)
            answers.append(ans)

        # Majority vote.
        counts = Counter(answers)
        final_answer, _ = counts.most_common(1)[0] if counts else ("", 0)

        return self._build_result(
            question,
            seed,
            transcript,
            final_answer,
            usage,
            t0,
            final_statement=f"Majority vote from {answers}: {final_answer}",
            participant_turns=5,
        )


@register("sc_k11")
class SelfConsistencyK11Runner(Runner):
    """Self-consistency with K=11 samples."""

    async def run(self, question: Question, seed: int) -> RunResult:
        t0 = time.time()
        usage = TokenUsage()
        user_prompt = build_user_prompt(question)
        transcript = [
            Message(role="system", speaker="system", content=NEUTRAL_EXPERT),
            Message(role="user", speaker="user", content=user_prompt),
        ]

        # Generate K=11 independent samples.
        tasks = []
        for sample_idx in range(11):
            temp = 0.6 + 0.15 * ((sample_idx + seed) % 4)  # Vary temperature.
            tasks.append(
                converse(
                    [ChatMessage(role="user", text=user_prompt)],
                    system=NEUTRAL_EXPERT,
                    temperature=min(temp, 1.0),
                )
            )

        results = await asyncio.gather(*tasks)
        answers = []
        for idx, (text, u) in enumerate(results):
            usage += u
            transcript.append(Message(role="assistant", speaker=f"sample-{idx+1}", content=text))
            ans = extract_answer(question, text)
            answers.append(ans)

        # Majority vote.
        counts = Counter(answers)
        final_answer, _ = counts.most_common(1)[0] if counts else ("", 0)

        return self._build_result(
            question,
            seed,
            transcript,
            final_answer,
            usage,
            t0,
            final_statement=f"Majority vote from {answers}: {final_answer}",
            participant_turns=11,
        )
