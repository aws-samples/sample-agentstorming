# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Orchestrator-worker runner.

Structure:
  1. Orchestrator receives the question.
  2. Orchestrator dispatches the question, with a small contextual wrapper,
     to each of N specialist workers *in parallel* (no worker sees another).
  3. Orchestrator receives each worker's reply and synthesises the final answer.

This is the pattern used by AutoGen, MetaGPT, CrewAI, and most \"crew\"-style
frameworks. Critically, workers never see each other's outputs; only the
orchestrator integrates them.
"""

from __future__ import annotations

import asyncio
import time

from .base import Runner, register
from ..types import Question, Message, TokenUsage, RunResult
from ..models import ChatMessage, converse, extract_multiple_choice, extract_numeric
from ..personas import SPECIALISTS, ORCHESTRATOR
from ..datasets.mmlu_pro import format_mcq_prompt
from ..datasets.math500 import format_math_prompt
from ..config import CONSTANTS, PERSONAS


def _build_prompt(q: Question) -> str:
    if q.dataset in ("mmlu_pro", "gpqa_diamond", "musr", "musr_murder", "truthfulqa_mc", "arc_challenge"):
        return format_mcq_prompt(q)
    return format_math_prompt(q)


def _extract(q: Question, text: str) -> str:
    if q.dataset in ("mmlu_pro", "gpqa_diamond", "musr", "musr_murder", "truthfulqa_mc", "arc_challenge"):
        letters = [chr(ord("A") + i) for i in range(len(q.choices or []))]
        return extract_multiple_choice(text, letters, choice_texts=q.choices)
    return extract_numeric(text)


@register("o_worker")
class OrchestratorWorkerRunner(Runner):
    async def run(self, question: Question, seed: int) -> RunResult:
        t0 = time.time()
        usage = TokenUsage()
        user_prompt = _build_prompt(question)
        specialists = PERSONAS.specialists[: CONSTANTS.worker_count]

        transcript: list[Message] = [
            Message(role="system", speaker="system", content=ORCHESTRATOR),
            Message(role="user", speaker="orchestrator", content=user_prompt),
        ]

        async def ask_worker(name: str) -> tuple[str, TokenUsage]:
            sys = SPECIALISTS[name]
            text, u = await converse(
                [ChatMessage(role="user", text=user_prompt)],
                system=sys,
                temperature=min(0.6 + 0.1 * (seed % 5), 1.0),
            )
            return name, text, u

        tasks = [ask_worker(s) for s in specialists]
        results = await asyncio.gather(*tasks)
        worker_replies_block = []
        for name, reply, u in results:
            usage += u
            transcript.append(Message(role="assistant", speaker=name, content=reply))
            worker_replies_block.append(f"## Worker {name}\\n{reply}\\n")

        synthesis_prompt = (
            f"Question:\\n{user_prompt}\\n\\n"
            f"Your specialist workers have each answered independently.\\n\\n"
            f"{chr(10).join(worker_replies_block)}\\n\\n"
            f"Synthesise the single best answer. End with `Final answer: X` on its own line."
        )
        final_text, u = await converse(
            [ChatMessage(role="user", text=synthesis_prompt)],
            system=ORCHESTRATOR,
            temperature=0.3,
        )
        usage += u
        transcript.append(Message(role="assistant", speaker="orchestrator", content=final_text))
        ans = _extract(question, final_text)
        return self._build_result(
            question, seed, transcript, ans, usage, t0,
            final_statement=final_text,
            moderator_turns=1, participant_turns=len(specialists),
        )
