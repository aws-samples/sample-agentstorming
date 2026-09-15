# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Single-agent baselines: s1 (one turn) and s_refine (five turns of self-refine).

s_refine follows Madaan et al. (2023) \"Self-Refine\": draft → critique → revise,
stopping early if the critic says \"the answer is correct\".
"""

from __future__ import annotations

import time

from .base import Runner, register
from ..types import Question, Message, TokenUsage, RunResult
from ..models import ChatMessage, converse, extract_multiple_choice, extract_numeric
from ..personas import NEUTRAL_EXPERT
from ..datasets.mmlu_pro import format_mcq_prompt
from ..datasets.math500 import format_math_prompt
from ..config import CONSTANTS


def build_user_prompt(q: Question) -> str:
    if q.dataset in ("mmlu_pro", "gpqa_diamond", "arc_challenge", "xdomain_v4", "xdomain_v5", "musr", "musr_murder", "musr_object_placements", "musr_team_allocation", "truthfulqa_mc"):
        return format_mcq_prompt(q)
    return format_math_prompt(q)


def _extract(q: Question, text: str) -> str:
    if q.dataset in ("mmlu_pro", "gpqa_diamond", "musr", "musr_murder", "truthfulqa_mc", "arc_challenge", "xdomain_v4", "xdomain_v5", "musr_object_placements", "musr_team_allocation"):
        letters = [chr(ord("A") + i) for i in range(len(q.choices or []))]
        return extract_multiple_choice(text, letters, choice_texts=q.choices)
    return extract_numeric(text)


@register("s1")
class SingleOneShotRunner(Runner):
    async def run(self, question: Question, seed: int) -> RunResult:
        t0 = time.time()
        usage = TokenUsage()
        user_prompt = build_user_prompt(question)
        transcript = [
            Message(role="system", speaker="system", content=NEUTRAL_EXPERT),
            Message(role="user", speaker="user", content=user_prompt),
        ]
        text, u = await converse(
            [ChatMessage(role="user", text=user_prompt)],
            system=NEUTRAL_EXPERT,
            temperature=min(0.7 if seed == 0 else (0.4 + 0.3 * (seed % 3)), 1.0),
        )
        usage += u
        transcript.append(Message(role="assistant", speaker="single", content=text))
        ans = _extract(question, text)
        return self._build_result(
            question, seed, transcript, ans, usage, t0,
            final_statement=text, participant_turns=1,
        )


@register("s_refine")
class SingleSelfRefineRunner(Runner):
    async def run(self, question: Question, seed: int) -> RunResult:
        t0 = time.time()
        usage = TokenUsage()
        user_prompt = build_user_prompt(question)
        transcript = [
            Message(role="system", speaker="system", content=NEUTRAL_EXPERT),
            Message(role="user", speaker="user", content=user_prompt),
        ]
        # Draft
        text, u = await converse(
            [ChatMessage(role="user", text=user_prompt)],
            system=NEUTRAL_EXPERT,
            temperature=min(0.7 if seed == 0 else (0.4 + 0.3 * (seed % 3)), 1.0),
        )
        usage += u
        transcript.append(Message(role="assistant", speaker="drafter", content=text))
        current_answer_text = text
        for iteration in range(1, CONSTANTS.refine_turns):
            critique_prompt = (
                f"Here is a draft answer to a problem. Critique it carefully; if incorrect,"
                f" explain the specific error and propose a better answer.\\n\\nProblem:\\n{user_prompt}\\n\\n"
                f"Draft answer:\\n{current_answer_text}\\n\\nYour critique:"
            )
            critique, u = await converse(
                [ChatMessage(role="user", text=critique_prompt)],
                system=NEUTRAL_EXPERT,
                temperature=0.3,
            )
            usage += u
            transcript.append(Message(role="assistant", speaker=f"critic-{iteration}", content=critique))
            if "answer is correct" in critique.lower() or "no change" in critique.lower():
                break
            revise_prompt = (
                f"Given the critique, revise your answer. Produce a complete, improved solution"
                f" ending with `Final answer: X` on its own line.\\n\\nProblem:\\n{user_prompt}\\n\\n"
                f"Previous draft:\\n{current_answer_text}\\n\\nCritique:\\n{critique}\\n\\nRevised answer:"
            )
            revised, u = await converse(
                [ChatMessage(role="user", text=revise_prompt)],
                system=NEUTRAL_EXPERT,
                temperature=0.5,
            )
            usage += u
            transcript.append(Message(role="assistant", speaker=f"reviser-{iteration}", content=revised))
            current_answer_text = revised
        ans = _extract(question, current_answer_text)
        return self._build_result(
            question, seed, transcript, ans, usage, t0,
            final_statement=current_answer_text,
            participant_turns=CONSTANTS.refine_turns,
        )
