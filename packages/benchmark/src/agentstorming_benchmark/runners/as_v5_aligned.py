# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""AS-V5-Aligned: Adaptive compute with benchmark-aligned personas.

Same as AS-V5-Adaptive, but uses benchmark-specific persona panels instead
of generic scientific personas.

Key change: Instead of always using (mathematician, physicist, deep-learning-scientist, ...)
we use personas aligned to the benchmark domain:
  - musr_murder: detective, forensic-pathologist, criminal-psychologist, ...
  - musr_object_placements: spatial-reasoning-expert, theory-of-mind-expert, ...
  - truthfulqa_mc: epistemologist, sceptic, fact-checker, ...

Hypothesis: Domain-aligned personas should outperform generic panels on
domain-specific benchmarks.

Cost profile: Same as AS-V5-Adaptive (~$0.038/q expected)
Panel: Same model diversity (Sonnet-4-6 ×2, DeepSeek-R1, Nova-Pro, Llama3.3-70B)
"""

from __future__ import annotations

import asyncio
import time
from collections import Counter

from .base import Runner, register
from ..types import Question, Message, TokenUsage, RunResult
from ..models import ChatMessage, converse, extract_multiple_choice, extract_numeric
from ..personas import get_persona_prompt, MODERATOR
from ..aligned_personas import get_aligned_fast_track, get_aligned_full_panel
from ..datasets.mmlu_pro import format_mcq_prompt
from ..datasets.math500 import format_math_prompt


# Same strong heterogeneous models as V5-adaptive
# But personas will be chosen per-benchmark
MODEL_POOL = [
    "us.anthropic.claude-sonnet-4-6",
    "us.anthropic.claude-sonnet-4-6",
    "us.deepseek.r1-v1:0",
    "us.amazon.nova-pro-v1:0",
    "us.meta.llama3-3-70b-instruct-v1:0",
]

MODERATOR_MODEL = "us.anthropic.claude-sonnet-4-6"


def _build_panel(persona_names: list[str]) -> dict[str, str]:
    """Build panel mapping persona names to models from the pool."""
    return {
        name: MODEL_POOL[i % len(MODEL_POOL)]
        for i, name in enumerate(persona_names)
    }


def _build_prompt(q: Question) -> str:
    if q.dataset in ("mmlu_pro", "gpqa_diamond", "musr", "truthfulqa_mc", "arc_challenge", "xdomain_v4", "musr_murder", "musr_object_placements", "musr_team_allocation"):
        return format_mcq_prompt(q)
    return format_math_prompt(q)


def _extract(q: Question, text: str) -> str:
    if q.dataset in ("mmlu_pro", "gpqa_diamond", "musr", "truthfulqa_mc", "arc_challenge", "xdomain_v4", "musr_murder", "musr_object_placements", "musr_team_allocation"):
        letters = [chr(ord("A") + i) for i in range(len(q.choices or []))]
        return extract_multiple_choice(text, letters, choice_texts=q.choices)
    return extract_numeric(text)


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
    """Full synthesis for disagreement cases (3-way split)."""
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


@register("as_v5_aligned")
class ASV5AlignedRunner(Runner):
    """AS-V5-Aligned: Adaptive depth with benchmark-aligned personas."""

    config_id = "as_v5_aligned"
    model = "panel-as-v5-aligned"

    async def run(self, question: Question, seed: int = 42) -> RunResult:
        t0 = time.time()
        user_prompt = _build_prompt(question)
        usage = TokenUsage()
        transcript = [
            Message(role="user", speaker="user", content=user_prompt)
        ]

        # Get benchmark-aligned personas
        benchmark = question.dataset
        fast_track_personas = get_aligned_fast_track(benchmark)
        full_panel_personas = get_aligned_full_panel(benchmark)

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
                        "personas": fast_track_personas,
                        "benchmark": benchmark,
                    },
                )

        # Phase 2: Disagreement detected, escalate to full panel
        # Add 2 more specialists
        additional_personas = [
            name for name in full_panel_personas
            if name not in fast_track_personas
        ]
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
                "benchmark": benchmark,
            },
        )
