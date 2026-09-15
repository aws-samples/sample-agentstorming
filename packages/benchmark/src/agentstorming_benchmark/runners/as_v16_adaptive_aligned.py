# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""AS-V16-Adaptive-Aligned: Combines AS-V5 adaptive protocol + AS-V14 aligned personas.

Design rationale (iter 292):
- AS-V5 (adaptive + generic personas) wins MMLU-Pro (90.0%), MuSR-Murder (76.4%)
- AS-V14 (ind-first + aligned personas) wins TruthfulQA (95.3%)
- AS-V16 tests hybrid: adaptive protocol + aligned personas

Hypothesis: This combines strengths of both parents to win across ≥4 benchmarks.

Protocol (from AS-V5):
1. Fast-track (3 specialists): answer independently
2. Consensus check: if 2+/3 agree, synthesize immediately (skip full panel)
3. Escalate: if disagreement, add 2 more specialists + full synthesis

Personas (from AS-V14):
- Benchmark-aligned specialist panels (detective for murders, epistemologist for TruthfulQA, etc)
- Heterogeneous strong models from diverse vendors

Expected performance:
- TruthfulQA: ~95% (aligned personas help with adversarial questions)
- MMLU-Pro: ~90% (adaptive fast-tracks easy factual questions)
- MuSR variants: ~75% (aligned personas + adaptive efficiency)
- XDomain: ~65% (cross-domain synthesis with adaptive protocol)

If AS-V16 wins on ≥4 benchmarks → Mission Criterion #1 met.
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


# Strong heterogeneous panel (active models only, no legacy)
STRONG_MODELS = [
    "us.anthropic.claude-sonnet-4-6",
    "us.deepseek.r1-v1:0",
    "us.amazon.nova-pro-v1:0",  # Changed from nova-premier (legacy)
    "us.meta.llama3-3-70b-instruct-v1:0",
    "us.anthropic.claude-sonnet-4-5-20250929-v1:0",
]

MODERATOR_MODEL = "us.anthropic.claude-sonnet-4-6"


def _build_panel(persona_names: list[str]) -> dict[str, str]:
    """Assign models to personas round-robin for diversity."""
    panel = {}
    for i, name in enumerate(persona_names):
        panel[name] = STRONG_MODELS[i % len(STRONG_MODELS)]
    return panel


def _build_prompt(q: Question) -> str:
    if q.dataset in ("mmlu_pro", "gpqa_diamond", "musr", "truthfulqa_mc", "arc_challenge",
                     "xdomain_v4", "musr_murder", "musr_object_placements", "musr_team_allocation",
                     "xdomain", "xdomain_v2_smoke", "xdomain_v3", "xdomain_v5"):
        return format_mcq_prompt(q)
    return format_math_prompt(q)


def _extract(q: Question, text: str) -> str:
    if q.dataset in ("mmlu_pro", "gpqa_diamond", "musr", "truthfulqa_mc", "arc_challenge",
                     "xdomain_v4", "musr_murder", "musr_object_placements", "musr_team_allocation",
                     "xdomain", "xdomain_v2_smoke", "xdomain_v3", "xdomain_v5"):
        letters = [chr(ord("A") + i) for i in range(len(q.choices or []))]
        return extract_multiple_choice(text, letters, choice_texts=q.choices)
    return extract_numeric(text)


async def _specialist_independent(
    name: str, user_prompt: str, panel: dict, seed: int
) -> tuple[str, str, TokenUsage]:
    """Specialist answers independently with aligned persona prompt."""
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
    """Full synthesis for disagreement cases (3-way split or worse).

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


@register("as_v16_adaptive_aligned")
class ASV16AdaptiveAlignedRunner(Runner):
    """AS-V16: Adaptive protocol + aligned personas (hybrid of AS-V5 + AS-V14)."""

    config_id = "as_v16_adaptive_aligned"
    model = "panel-as-v16-adaptive-aligned"

    async def run(self, question: Question, seed: int = 42) -> RunResult:
        t0 = time.time()
        user_prompt = _build_prompt(question)
        usage = TokenUsage()
        transcript = [
            Message(role="user", speaker="user", content=user_prompt)
        ]

        # Get aligned personas for this benchmark
        all_personas = get_aligned_personas(question.dataset)
        fast_track_personas = all_personas[:3]
        full_panel_personas = all_personas[:5]

        # Build panels
        fast_track_panel = _build_panel(fast_track_personas)
        full_panel = _build_panel(full_panel_personas)

        # Phase 1: Fast-track panel (3 specialists) answer independently
        tasks = [
            _specialist_independent(name, user_prompt, fast_track_panel, seed)
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

        # Check consensus: if 2+/3 agree, fast-track
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
                        "personas": fast_track_personas,
                        "benchmark": question.dataset,
                    },
                )

        # Phase 2: Disagreement detected, escalate to full panel (5 specialists)
        additional_personas = [p for p in full_panel_personas if p not in fast_track_personas]
        additional_tasks = [
            _specialist_independent(name, user_prompt, full_panel, seed)
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

        # Full synthesis with quality-weighting
        mod_resp, mod_u = await _moderator_synthesis_full(
            user_prompt,
            all_responses,
            seed
        )
        usage += mod_u
        transcript.append(Message(role="assistant", speaker="moderator", content=mod_resp))

        final_answer = _extract(question, mod_resp)

        return self._build_result(
            question, seed, transcript, final_answer, usage, t0,
            final_statement=mod_resp,
            moderator_turns=1,
            participant_turns=5,
            metadata={
                "fast_track": False,
                "panel_size": 5,
                "personas": full_panel_personas,
                "benchmark": question.dataset,
            },
        )
