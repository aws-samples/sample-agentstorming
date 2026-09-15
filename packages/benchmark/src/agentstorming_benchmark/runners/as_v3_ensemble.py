# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""AS-V3-Ensemble: Heterogeneous self-consistency (no discussion).

Radical simplification for if AS-V2 fails:
- 5 specialists, heterogeneous models, ZERO interaction
- Pure parallel independent samples
- Majority vote
- This is "sc_k5 but with model diversity"

Purpose:
- Tests whether model diversity helps WITHOUT discussion overhead
- If this beats homogeneous sc_k5, hypothesis is: diversity > discussion
- If this LOSES to homogeneous sc_k5, hypothesis is falsified
- Minimal cost: same as sc_k5 (5 calls)

This is the cleanest test of "does model diversity matter?"
"""

from __future__ import annotations

import asyncio
import time
from collections import Counter

from .base import Runner, register
from ..types import Question, Message, TokenUsage, RunResult
from ..models import ChatMessage, converse, extract_multiple_choice, extract_numeric, cost_usd
from ..personas import SPECIALISTS
from ..datasets.mmlu_pro import format_mcq_prompt
from ..datasets.math500 import format_math_prompt

# Same heterogeneous panel, but no moderator (we don't use project-lead).
PANEL_V3_ENSEMBLE = {
    "mathematician": "us.anthropic.claude-sonnet-4-6",
    "deep-learning-scientist": "us.deepseek.r1-v1:0",
    "physics-scientist": "us.amazon.nova-pro-v1:0",
    "fourier-transform-scientist": "us.meta.llama3-3-70b-instruct-v1:0",
    "neuron-biologist": "us.anthropic.claude-sonnet-4-5-20250929-v1:0",
}


def _build_prompt(q: Question) -> str:
    if q.dataset in ("mmlu_pro", "gpqa_diamond", "musr_murder", "truthfulqa_mc", "arc_challenge"):
        return format_mcq_prompt(q)
    return format_math_prompt(q)


def _extract(q: Question, text: str) -> str:
    if q.dataset in ("mmlu_pro", "gpqa_diamond", "musr_murder", "truthfulqa_mc", "arc_challenge"):
        letters = [chr(ord("A") + i) for i in range(len(q.choices or []))]
        return extract_multiple_choice(text, letters, choice_texts=q.choices)
    return extract_numeric(text)


async def _specialist_independent(
    name: str, user_prompt: str, seed: int, model: str
) -> tuple[str, TokenUsage]:
    """Specialist answers INDEPENDENTLY."""
    sys = SPECIALISTS[name]
    instr = (
        "You are an expert being consulted on the following problem. "
        "Provide your best answer independently. "
        "Think step-by-step if helpful. "
        "End your response with `Final answer: X` on its own line, where X is your answer. "
        "Keep your response ≤300 words."
    )

    text, u = await converse(
        [ChatMessage(role="user", text=f"{user_prompt}\n\n{instr}")],
        system=sys,
        model=model,
        temperature=0.7,
    )
    return text, u


async def _run_as_v3_ensemble(question: Question, seed: int) -> RunResult:
    """AS-V3-Ensemble: 5 heterogeneous independent samples -> majority vote."""
    t0 = time.time()
    usage = TokenUsage()
    user_prompt = _build_prompt(question)

    transcript: list[Message] = [
        Message(role="system", speaker="system",
                content="AS-V3-Ensemble: heterogeneous independent samples, majority vote"),
        Message(role="user", speaker="user", content=user_prompt),
    ]

    # All specialists answer in parallel, independently.
    tasks = []
    specialists = list(PANEL_V3_ENSEMBLE.keys())
    for sp in specialists:
        model = PANEL_V3_ENSEMBLE[sp]
        tasks.append(_specialist_independent(sp, user_prompt, seed, model))

    results = await asyncio.gather(*tasks)

    # Collect answers.
    votes = []
    for sp, (text, u) in zip(specialists, results):
        usage += u
        transcript.append(Message(
            role="assistant", speaker=sp, content=text,
            metadata={"model": PANEL_V3_ENSEMBLE[sp]}
        ))

        ans = _extract(question, text)
        if ans:
            votes.append(ans)

    # Majority vote.
    if votes:
        vote_counts = Counter(votes)
        extracted_answer = vote_counts.most_common(1)[0][0]
        final_statement = f"Majority vote: {extracted_answer} (votes: {dict(vote_counts)})"
    else:
        extracted_answer = ""
        final_statement = "(no valid answers extracted)"

    from .base import answer_matches
    is_correct = answer_matches(extracted_answer, question.answer_key, question.dataset)

    # Cost approximation.
    total_cost = cost_usd(usage, "us.anthropic.claude-sonnet-4-6")

    return RunResult(
        config_id="as_v3_ensemble",
        dataset=question.dataset,
        question_id=question.id,
        seed=seed,
        model="panel-as-v3-ensemble",
        transcript=transcript,
        extracted_answer=extracted_answer,
        is_correct=is_correct,
        usage=usage,
        cost_usd=total_cost,
        latency_sec=round(time.time() - t0, 3),
        final_statement=final_statement,
        moderator_turns=0,
        participant_turns=len(specialists),
    )


@register("as_v3_ensemble")
class ASV3EnsembleRunner(Runner):
    """AS-V3-Ensemble: heterogeneous self-consistency with majority vote."""

    async def run(self, question: Question, seed: int) -> RunResult:
        result = await _run_as_v3_ensemble(question, seed)
        result.config_id = self.config_id
        return result
