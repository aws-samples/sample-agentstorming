# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""AS-V12-Stronger-Moderator: Enhanced moderator quality for AS-V11.

Key innovation (iter218): Improve moderator synthesis quality to push margins higher.

Changes from AS-V11:
1. **Enhanced moderator prompt** - Explicit 3-step process (evaluate → identify → synthesize)
   with emphasis on CORRECTNESS over consensus
2. **Lower specialist temperature** - 0.7 → 0.5 (reduce noise, preserve model diversity)
3. **Lower moderator temperature** - 0.3 → 0.2 (more deterministic synthesis)

Hypothesis: AS-V11's ~7.6pp margin (below target 8-10pp) is due to moderator quality
bottleneck. The simple "synthesize their answers" prompt doesn't emphasize correctness
enough. Enhanced prompt gives moderator explicit permission to override weak specialists
and follow a structured evaluation process.

Same as AS-V11:
- Benchmark-aligned personas (epistemologist, skeptic, fact-checker, etc. for TruthfulQA)
- Heterogeneous strong models (Sonnet, Opus, DeepSeek R1, Nova Pro, Llama3.3)
- Adaptive depth (fast-track 3, escalate to 5 if disagreement)

Expected improvement: 94.8% → 96%+ accuracy on TruthfulQA (8.8pp+ margin → statistical significance)
"""

from __future__ import annotations

import asyncio
import time
from collections import Counter

from .base import Runner, register
from ..types import Question, Message, TokenUsage, RunResult
from ..models import ChatMessage, converse, extract_multiple_choice, extract_numeric
from ..personas import get_aligned_personas, get_persona_prompt, MODERATOR
from ..datasets.mmlu_pro import format_mcq_prompt
from ..datasets.math500 import format_math_prompt


# Model pool (strong heterogeneous models)
STRONG_MODELS = [
    "us.anthropic.claude-sonnet-4-6",
    "us.anthropic.claude-sonnet-4-6",
    "us.deepseek.r1-v1:0",
    "us.amazon.nova-pro-v1:0",
    "us.meta.llama3-3-70b-instruct-v1:0",
]

MODERATOR_MODEL = "us.anthropic.claude-sonnet-4-6"


def _build_prompt(q: Question) -> str:
    if q.dataset in ("mmlu_pro", "gpqa_diamond", "musr", "truthfulqa_mc", "arc_challenge"):
        return format_mcq_prompt(q)
    return format_math_prompt(q)


def _extract(q: Question, text: str) -> str:
    if q.dataset in ("mmlu_pro", "gpqa_diamond", "musr", "truthfulqa_mc", "arc_challenge"):
        letters = [chr(ord("A") + i) for i in range(len(q.choices or []))]
        return extract_multiple_choice(text, letters, choice_texts=q.choices)
    return extract_numeric(text)


def _assign_models_to_personas(personas: list[str]) -> dict[str, str]:
    """Assign models to personas in round-robin fashion for diversity."""
    panel = {}
    for i, persona in enumerate(personas):
        panel[persona] = STRONG_MODELS[i % len(STRONG_MODELS)]
    return panel


async def _specialist_independent(
    name: str, user_prompt: str, panel: dict, seed: int
) -> tuple[str, str, TokenUsage]:
    """Specialist answers independently. Returns (name, response, usage)."""
    sys = get_persona_prompt(name)
    instr = (
        "You are an expert consulted on this problem. "
        "Provide your best answer independently. "
        "Think step-by-step if helpful. "
        "End with `Final answer: X` on its own line. "
        "Keep response ≤300 words."
    )

    model = panel[name]
    text, u = await converse(
        [ChatMessage(role="user", text=f"{user_prompt}\n\n{instr}")],
        system=sys,
        model=model,
        temperature=0.5,  # Changed from 0.7 to reduce variance
    )
    return name, text, u


async def _moderator_synthesis_simple(
    user_prompt: str, responses: list[tuple[str, str]], seed: int
) -> tuple[str, TokenUsage]:
    """Simple synthesis for consensus cases (2+/3 agree)."""
    context = "\n\n".join([
        f"**{name}:** {resp}" for name, resp in responses
    ])

    mod_prompt = f"""The following specialists have answered this question independently:

{context}

Your task: Synthesize their answers and provide the final answer.
Since there appears to be consensus, keep your synthesis brief.
End with `Final answer: X` on its own line.
"""

    text, u = await converse(
        [ChatMessage(role="user", text=f"{user_prompt}\n\n{mod_prompt}")],
        system=MODERATOR,
        model=MODERATOR_MODEL,
        temperature=0.2,  # Changed from 0.3 for more deterministic synthesis
    )
    return text, u


async def _moderator_synthesis_full(
    user_prompt: str, responses: list[tuple[str, str]], seed: int
) -> tuple[str, TokenUsage]:
    """Full synthesis for disagreement cases (3-way split).

    Enhanced with explicit 3-step evaluation process emphasizing correctness.
    """
    context = "\n\n".join([
        f"**{name}:** {resp}" for name, resp in responses
    ])

    mod_prompt = f"""The following specialists have answered this question independently:

{context}

Your task: Synthesize their answers into the BEST possible final answer.

CRITICAL: Your goal is CORRECTNESS, not consensus. Follow this process:

1. EVALUATE each specialist's reasoning:
   - Identify logical errors, inconsistencies, unsupported claims
   - Flag any specialist whose reasoning is weak or confused
   - Note which specialists provide strong evidence or clear logic

2. IDENTIFY the highest-quality answer:
   - The answer with the strongest reasoning and evidence
   - NOT necessarily the majority answer
   - If all answers are weak, state your own best reasoning

3. SYNTHESIZE:
   - If one specialist's answer is clearly superior, adopt it
   - If multiple strong answers exist, explain the tradeoff and choose
   - If all answers are weak, provide your own independent analysis

FORMAT:
- Brief evaluation of each specialist (1-2 sentences each)
- Your reasoning for the final answer (2-3 sentences)
- End with `Final answer: X` on its own line

REMEMBER: You are the FINAL DECIDER. Your job is to find truth, not mediate.
"""

    text, u = await converse(
        [ChatMessage(role="user", text=f"{user_prompt}\n\n{mod_prompt}")],
        system=MODERATOR,
        model=MODERATOR_MODEL,
        temperature=0.2,  # Changed from 0.3 for more deterministic synthesis
    )
    return text, u


@register("as_v12_stronger_moderator")
class ASV12StrongerModeratorRunner(Runner):
    """AS-V12: Enhanced moderator quality with lower temperatures."""

    config_id = "as_v12_stronger_moderator"
    model = "panel-as-v12-stronger-moderator"

    async def run(self, question: Question, seed: int = 42) -> RunResult:
        t0 = time.time()
        user_prompt = _build_prompt(question)
        usage = TokenUsage()
        transcript = [
            Message(role="user", speaker="user", content=user_prompt)
        ]

        # Get aligned personas for this benchmark
        # For MuSR, parse the subcategory from the question ID
        benchmark_key = question.dataset
        if question.dataset == "musr" and question.id:
            if "murder_mysteries" in question.id:
                benchmark_key = "musr_murder"
            elif "object_placements" in question.id:
                benchmark_key = "musr_object_placements"
            elif "team_allocation" in question.id:
                benchmark_key = "musr_team_allocation"

        aligned_personas = get_aligned_personas(benchmark_key)

        # Assign models to personas (heterogeneous panel)
        panel = _assign_models_to_personas(aligned_personas)

        # Phase 1: Fast-track panel (first 3 personas) answer independently
        fast_track_personas = aligned_personas[:3]
        tasks = [
            _specialist_independent(name, user_prompt, panel, seed)
            for name in fast_track_personas
        ]
        results = await asyncio.gather(*tasks)

        for name, resp, u in results:
            usage += u
            transcript.append(Message(role="assistant", speaker=name, content=resp))

        # Extract answers from fast-track responses
        fast_answers = []
        for name, resp, _ in results:
            extracted = _extract(question, resp)
            if extracted:
                fast_answers.append(extracted)

        # Check consensus
        if len(fast_answers) >= 2:
            counts = Counter(fast_answers)
            most_common = counts.most_common(1)[0]
            if most_common[1] >= 2:
                # Consensus! Use simple synthesis
                mod_resp, mod_u = await _moderator_synthesis_simple(
                    user_prompt,
                    [(name, resp) for name, resp, _ in results],
                    seed
                )
                usage += mod_u
                transcript.append(Message(role="assistant", speaker="moderator", content=mod_resp))

                final_answer = _extract(question, mod_resp)

                return self._build_result(
                    question, seed, transcript, final_answer, usage, t0,
                    final_statement=mod_resp,
                    moderator_turns=1,
                    participant_turns=3,
                    metadata={
                        "fast_track": True,
                        "panel_size": 3,
                        "aligned_personas": fast_track_personas,
                        "benchmark": question.dataset,
                    },
                )

        # Phase 2: Disagreement detected, escalate to full panel
        # Add 2 more personas (or all remaining if <5 total)
        additional_personas = [
            name for name in aligned_personas[3:5]  # limit to 5 total
        ]

        if additional_personas:
            additional_tasks = [
                _specialist_independent(name, user_prompt, panel, seed)
                for name in additional_personas
            ]
            additional_results = await asyncio.gather(*additional_tasks)

            for name, resp, u in additional_results:
                usage += u
                transcript.append(Message(role="assistant", speaker=name, content=resp))

            # Combine all responses for moderator
            all_responses = [
                (name, resp) for name, resp, _ in results + additional_results
            ]
        else:
            all_responses = [
                (name, resp) for name, resp, _ in results
            ]

        # Full synthesis with quality-weighting
        mod_resp, mod_u = await _moderator_synthesis_full(
            user_prompt,
            all_responses,
            seed
        )
        usage += mod_u
        transcript.append(Message(role="assistant", speaker="moderator", content=mod_resp))

        final_answer = _extract(question, mod_resp)

        full_panel_size = len(fast_track_personas) + len(additional_personas)

        return self._build_result(
            question, seed, transcript, final_answer, usage, t0,
            final_statement=mod_resp,
            moderator_turns=1,
            participant_turns=full_panel_size,
            metadata={
                "fast_track": False,
                "panel_size": full_panel_size,
                "aligned_personas": fast_track_personas + additional_personas,
                "benchmark": question.dataset,
            },
        )
