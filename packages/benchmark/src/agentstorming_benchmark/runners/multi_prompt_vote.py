# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Multi-prompt majority vote baseline.

Generate K distinct prompt rewritings, run the same model on each, majority-vote.
Tests whether prompt diversity (without model diversity) helps.
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
    return f"Question: {q.question}\n\nProvide your answer."


def extract_answer(q: Question, text: str) -> str:
    """Extract answer from model response."""
    if q.dataset in ("mmlu_pro", "gpqa_diamond", "musr", "musr_murder", "truthfulqa_mc", "arc_challenge", "xdomain_v4", "musr_object_placements", "musr_team_allocation"):
        letters = [chr(ord("A") + i) for i in range(len(q.choices or []))]
        return extract_multiple_choice(text, letters, choice_texts=q.choices)
    return extract_numeric(text)


# Five distinct prompt templates.
PROMPT_VARIANTS = [
    "Original",  # Use base prompt as-is.
    "Step-by-step",
    "Expert reasoning",
    "Verify and conclude",
    "First principles",
]


def rewrite_prompt(base_prompt: str, variant: str) -> str:
    """Rewrite the prompt according to the variant style."""
    if variant == "Original":
        return base_prompt
    if variant == "Step-by-step":
        return f"{base_prompt}\n\nSolve this step-by-step, showing your reasoning at each stage."
    if variant == "Expert reasoning":
        return f"You are an expert in this domain. {base_prompt}\n\nProvide a careful, expert-level analysis."
    if variant == "Verify and conclude":
        return f"{base_prompt}\n\nWork through the problem, then verify your answer before stating it clearly."
    if variant == "First principles":
        return f"{base_prompt}\n\nApproach this from first principles, building up from fundamental concepts."
    return base_prompt


@register("mpv_k5")
class MultiPromptVoteK5Runner(Runner):
    """Multi-prompt vote with K=5 prompt variants."""

    async def run(self, question: Question, seed: int) -> RunResult:
        t0 = time.time()
        usage = TokenUsage()
        base_prompt = build_user_prompt(question)
        transcript = [
            Message(role="system", speaker="system", content=NEUTRAL_EXPERT),
            Message(role="user", speaker="user", content=f"Base: {base_prompt}"),
        ]

        # Generate K=5 responses with different prompt rewritings.
        tasks = []
        for idx, variant in enumerate(PROMPT_VARIANTS):
            rewritten = rewrite_prompt(base_prompt, variant)
            tasks.append(
                converse(
                    [ChatMessage(role="user", text=rewritten)],
                    system=NEUTRAL_EXPERT,
                    temperature=0.7,
                )
            )

        results = await asyncio.gather(*tasks)
        answers = []
        for idx, (text, u) in enumerate(results):
            usage += u
            variant = PROMPT_VARIANTS[idx]
            transcript.append(Message(role="assistant", speaker=f"prompt-{variant}", content=text))
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
