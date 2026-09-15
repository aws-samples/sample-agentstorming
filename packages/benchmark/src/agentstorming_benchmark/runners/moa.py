# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Mixture-of-Agents (MoA) baseline — Wang et al. arXiv:2406.04692.

Multiple proposer models generate initial answers, then an aggregator model
synthesizes them into a final answer. Layered approach: proposers → aggregator.
"""

from __future__ import annotations

import asyncio
import time

from .base import Runner, register
from ..types import Question, Message, TokenUsage, RunResult
from ..models import ChatMessage, converse, extract_multiple_choice, extract_numeric
from ..personas import NEUTRAL_EXPERT, get_persona_prompt
from ..config import PRIMARY_MODEL


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


# Heterogeneous proposer panel (same as AS-Het-Strong).
PROPOSER_MODELS = [
    ("mathematician", "us.anthropic.claude-sonnet-4-5-20250929-v1:0"),
    ("deep-learning-scientist", "us.deepseek.r1-v1:0"),
    ("physics-scientist", "us.anthropic.claude-sonnet-4-6"),
    ("fourier-transform-scientist", "us.meta.llama3-3-70b-instruct-v1:0"),
    ("neuron-biologist", "us.anthropic.claude-haiku-4-5-20251001-v1:0"),
    ("chemist", "us.mistral.pixtral-large-2502-v1:0"),
]

AGGREGATOR_MODEL = "us.anthropic.claude-sonnet-4-6"


@register("moa_het")
class MixtureOfAgentsHetRunner(Runner):
    """MoA with heterogeneous proposers + aggregator."""

    model = AGGREGATOR_MODEL  # Report aggregator as primary model.

    async def run(self, question: Question, seed: int) -> RunResult:
        t0 = time.time()
        usage = TokenUsage()
        user_prompt = build_user_prompt(question)
        transcript = [
            Message(role="system", speaker="system", content="MoA: proposers → aggregator"),
            Message(role="user", speaker="user", content=user_prompt),
        ]

        # Phase 1: Proposers generate initial answers.
        proposer_tasks = []
        for persona_name, model_id in PROPOSER_MODELS:
            persona_sys = get_persona_prompt(persona_name)
            proposer_tasks.append(
                converse(
                    [ChatMessage(role="user", text=user_prompt)],
                    system=persona_sys,
                    temperature=0.7,
                    model=model_id,
                )
            )

        proposer_results = await asyncio.gather(*proposer_tasks)
        proposer_answers = []
        for (persona_name, _), (text, u) in zip(PROPOSER_MODELS, proposer_results):
            usage += u
            transcript.append(Message(role="assistant", speaker=f"proposer-{persona_name}", content=text))
            ans = extract_answer(question, text)
            proposer_answers.append((persona_name, text, ans))

        # Phase 2: Aggregator synthesizes.
        aggregation_prompt = (
            f"{user_prompt}\n\n"
            "You have received the following responses from multiple expert agents:\n\n"
        )
        for persona, response_text, extracted in proposer_answers:
            aggregation_prompt += f"**{persona}:** {response_text[:500]}...\n\n"  # Truncate to avoid token overflow.
        aggregation_prompt += (
            "\nSynthesize these responses into a single, final answer. "
            "Weigh the arguments carefully and state your final answer clearly."
        )

        agg_text, agg_u = await converse(
            [ChatMessage(role="user", text=aggregation_prompt)],
            system=NEUTRAL_EXPERT,
            temperature=0.3,
            model=AGGREGATOR_MODEL,
        )
        usage += agg_u
        transcript.append(Message(role="assistant", speaker="aggregator", content=agg_text))

        final_answer = extract_answer(question, agg_text)

        return self._build_result(
            question,
            seed,
            transcript,
            final_answer,
            usage,
            t0,
            final_statement=agg_text,
            participant_turns=len(PROPOSER_MODELS) + 1,
        )
