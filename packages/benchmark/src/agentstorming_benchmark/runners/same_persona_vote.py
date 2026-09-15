# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Multi-agent same-persona voting baseline.

N independent agents, all with the same persona/prompt and same model, each
generate an answer. Then majority vote.

This is the null hypothesis for Agent Storming: tests whether the benefit comes
from diversity (model + persona) or just from "N agents vote" alone.
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


@register("same_persona_n5")
class SamePersonaN5VoteRunner(Runner):
    """N=5 agents, all same persona (neutral expert), same model, majority vote."""

    async def run(self, question: Question, seed: int) -> RunResult:
        t0 = time.time()
        usage = TokenUsage()
        user_prompt = build_user_prompt(question)
        transcript = [
            Message(role="system", speaker="system", content=NEUTRAL_EXPERT),
            Message(role="user", speaker="user", content=user_prompt),
        ]

        # Generate N=5 independent responses from same model + same persona.
        # Vary temperature slightly to avoid identical outputs.
        tasks = []
        for agent_idx in range(5):
            temp = 0.7 + 0.05 * (agent_idx % 3)  # Slight variation: 0.7, 0.75, 0.8
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
            transcript.append(Message(role="assistant", speaker=f"agent-{idx+1}", content=text))
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


@register("same_persona_n8")
class SamePersonaN8VoteRunner(Runner):
    """N=8 agents, all same persona (neutral expert), same model, majority vote.

    Matches the panel size of Agent Storming for direct comparison.
    """

    async def run(self, question: Question, seed: int) -> RunResult:
        t0 = time.time()
        usage = TokenUsage()
        user_prompt = build_user_prompt(question)
        transcript = [
            Message(role="system", speaker="system", content=NEUTRAL_EXPERT),
            Message(role="user", speaker="user", content=user_prompt),
        ]

        # Generate N=8 independent responses.
        tasks = []
        for agent_idx in range(8):
            temp = 0.65 + 0.05 * (agent_idx % 5)  # 0.65, 0.70, 0.75, 0.80, 0.85
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
            transcript.append(Message(role="assistant", speaker=f"agent-{idx+1}", content=text))
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
            participant_turns=8,
        )
