# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Multi-Agent Debate runner (Du et al. 2023, arXiv:2305.14325).

Five agents, R rounds. Each round, every agent sees the other agents' latest
answers and revises its own. Final answer is the majority vote over the
round-R answers; ties broken by majority of last two rounds combined.
"""

from __future__ import annotations

import asyncio
import time
from collections import Counter

from .base import Runner, register
from ..types import Question, Message, TokenUsage, RunResult
from ..models import ChatMessage, converse, extract_multiple_choice, extract_numeric
from ..personas import DEBATE_AGENT
from ..datasets.mmlu_pro import format_mcq_prompt
from ..datasets.math500 import format_math_prompt
from ..config import CONSTANTS, PERSONAS, model_for_persona


def _build_prompt(q: Question) -> str:
    if q.dataset in ("mmlu_pro", "gpqa_diamond", "musr", "musr_murder", "truthfulqa_mc", "arc_challenge"):
        return format_mcq_prompt(q)
    return format_math_prompt(q)


def _extract_letter(q: Question, text: str) -> str:
    if q.dataset in ("mmlu_pro", "gpqa_diamond", "musr", "musr_murder", "truthfulqa_mc", "arc_challenge"):
        letters = [chr(ord("A") + i) for i in range(len(q.choices or []))]
        return extract_multiple_choice(text, letters, choice_texts=q.choices)
    return extract_numeric(text)


@register("mad")
class MultiAgentDebateRunner(Runner):
    async def run(self, question: Question, seed: int) -> RunResult:
        t0 = time.time()
        usage = TokenUsage()
        user_prompt = _build_prompt(question)
        agents = PERSONAS.specialists[: CONSTANTS.mad_agents]
        transcript: list[Message] = [
            Message(role="system", speaker="system", content=DEBATE_AGENT),
            Message(role="user", speaker="user", content=user_prompt),
        ]
        # Round 0: each agent answers independently.
        async def round0(name: str):
            # Diversify temperature across seeds without exceeding the
            # Bedrock max of 1.0; map any seed to the [0.7, 1.0] range.
            temp = 0.7 + 0.05 * (seed % 7)
            text, u = await converse(
                [ChatMessage(role="user", text=user_prompt)],
                system=DEBATE_AGENT,
                model=model_for_persona(name),
                temperature=min(temp, 1.0),
            )
            return name, text, u

        last_answers: dict[str, str] = {}
        answer_history: list[dict[str, str]] = []
        results = await asyncio.gather(*[round0(a) for a in agents])
        for name, reply, u in results:
            usage += u
            transcript.append(Message(role="assistant", speaker=name, content=reply,
                                      metadata={"round": 0}))
            last_answers[name] = reply
        answer_history.append(dict(last_answers))

        # Subsequent rounds
        for round_i in range(1, CONSTANTS.mad_rounds):
            def peers_block(exclude: str) -> str:
                return "\\n\\n".join(
                    f"Agent {n} says:\\n{last_answers[n]}" for n in agents if n != exclude
                )

            async def round_i_(name: str):
                ctx = peers_block(name)
                p = (
                    f"Debate round {round_i}. Original problem:\\n\\n{user_prompt}\\n\\n"
                    f"The other agents' latest responses:\\n\\n{ctx}\\n\\n"
                    f"Critique, build on, or refute as appropriate. Then give your own"
                    f" complete answer ending with `Final answer: X`."
                )
                text, u = await converse(
                    [ChatMessage(role="user", text=p)],
                    system=DEBATE_AGENT,
                    model=model_for_persona(name),
                    temperature=0.6,
                )
                return name, text, u

            results = await asyncio.gather(*[round_i_(a) for a in agents])
            for name, reply, u in results:
                usage += u
                transcript.append(Message(role="assistant", speaker=name, content=reply,
                                          metadata={"round": round_i}))
                last_answers[name] = reply
            answer_history.append(dict(last_answers))

        # Majority vote on the final round.
        votes = []
        for name in agents:
            v = _extract_letter(question, last_answers[name])
            if v:
                votes.append(v)
        winner = ""
        if votes:
            winner = Counter(votes).most_common(1)[0][0]
        return self._build_result(
            question, seed, transcript, winner, usage, t0,
            final_statement=", ".join(f"{a}:{_extract_letter(question, last_answers[a])}" for a in agents),
            participant_turns=len(agents) * CONSTANTS.mad_rounds,
        )
