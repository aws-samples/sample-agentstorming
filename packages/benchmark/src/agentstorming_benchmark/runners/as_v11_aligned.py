# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""AS-V11-Aligned: Benchmark-aligned persona panels with adaptive depth.

Key innovation (iter209): Use personas ALIGNED to the benchmark domain instead of
hard-coded research personas.

Insight: The original AS panel (mathematician, physicist, chemist, etc.) was designed
for MMLU-Pro and academic synthesis tasks. But these personas are MISALIGNED for:
- musr_murder → needs detective, forensic-pathologist, prosecutor, etc.
- musr_object_placements → needs spatial-reasoning-expert, theory-of-mind, etc.
- truthfulqa_mc → needs epistemologist, fact-checker, skeptic, etc.

This runner:
1. Selects personas based on benchmark domain (via get_aligned_personas)
2. Uses same adaptive-depth logic as AS-V5-adaptive
3. Tests hypothesis: aligned personas > mismatched personas

Panel selection:
- musr_murder: detective, forensic-pathologist, criminal-psychologist, defence-lawyer, prosecutor
- musr_object_placements: spatial-reasoning-expert, theory-of-mind-expert, logician, librarian, household-organiser
- musr_team_allocation: operations-research-expert, manager, sociologist, economist, psychometrician
- truthfulqa_mc: epistemologist, sceptic, fact-checker, logician, historian-of-misconceptions
- mmlu_pro: mathematician, physicist, computer-scientist, chemist, biologist, economist, historian, deep-learning-scientist
- arc_challenge: biology-teacher, chemistry-teacher, physics-teacher, earth-science-teacher, science-historian

Model assignment:
- Uses HETEROGENEOUS strong models (same as AS-V5-adaptive)
- Sonnet, Opus, DeepSeek R1, Nova Pro, Llama3.3 mix
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
        temperature=0.7,
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
        temperature=0.3,
    )
    return text, u


async def _moderator_synthesis_full(
    user_prompt: str, responses: list[tuple[str, str]], seed: int
) -> tuple[str, TokenUsage]:
    """Full synthesis for disagreement cases (3-way split).

    Same as simple synthesis but with explicit instruction to weigh quality.
    """
    context = "\n\n".join([
        f"**{name}:** {resp}" for name, resp in responses
    ])

    mod_prompt = f"""The following specialists have answered this question independently:

{context}

Your task: Synthesize their answers into a final answer.
These specialists disagree, so carefully assess the quality of each argument:
- Which reasoning is most sound?
- Which cites the strongest evidence?
- Which aligns best with domain expertise?

Prioritize high-quality reasoning over simple voting.
End with `Final answer: X` on its own line.
"""

    text, u = await converse(
        [ChatMessage(role="user", text=f"{user_prompt}\n\n{mod_prompt}")],
        system=MODERATOR,
        model=MODERATOR_MODEL,
        temperature=0.3,
    )
    return text, u


@register("as_v11_aligned")
class ASV11AlignedRunner(Runner):
    """AS-V11: Benchmark-aligned personas with adaptive depth."""

    config_id = "as_v11_aligned"
    model = "panel-as-v11-aligned"

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
