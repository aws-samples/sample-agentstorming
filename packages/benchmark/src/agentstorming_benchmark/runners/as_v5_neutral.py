# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""AS-V5-Neutral: Persona ablation variant of AS-V5-Adaptive.

This is a controlled ablation experiment to test whether persona diversity
contributes to AS performance beyond model diversity alone.

SAME as AS-V5-Adaptive:
- Model panel composition (heterogeneous strong models)
- Adaptive depth logic (fast-track 3, escalate to 5)
- Moderator synthesis strategy
- All hyperparameters

DIFFERENT:
- ALL specialists use NEUTRAL_EXPERT system prompt instead of role-specific
  personas (mathematician, physicist, etc.)

Purpose: If AS-V5-Neutral ≈ AS-V5-Adaptive, then personas don't matter and
we can simplify. If AS-V5-Neutral < AS-V5-Adaptive, then personas provide
measurable gain through diverse reasoning lenses.

See: /opt/agentstorming/driver/decisions/20260517-1342-iter123-persona-ablation.md
"""

from __future__ import annotations

import asyncio
import time
from collections import Counter

from .base import Runner, register
from ..types import Question, Message, TokenUsage, RunResult
from ..models import ChatMessage, converse, extract_multiple_choice, extract_numeric
from ..personas import NEUTRAL_EXPERT, MODERATOR
from ..datasets.mmlu_pro import format_mcq_prompt
from ..datasets.math500 import format_math_prompt


# Use the SAME model panel as AS-V5-Adaptive (critical for ablation validity)
# We keep the persona names as keys for panel structure, but use NEUTRAL_EXPERT prompt
FAST_TRACK_PANEL_V5 = {
    "mathematician": "us.anthropic.claude-sonnet-4-6",
    "deep-learning-scientist": "us.anthropic.claude-sonnet-4-6",
    "physics-scientist": "us.deepseek.r1-v1:0",
}

FULL_PANEL_V5 = {
    **FAST_TRACK_PANEL_V5,
    "computer-scientist": "us.amazon.nova-pro-v1:0",
    "chemist": "us.meta.llama3-3-70b-instruct-v1:0",
}

MODERATOR_MODEL = "us.anthropic.claude-sonnet-4-6"


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
    """Specialist answers independently using NEUTRAL_EXPERT prompt."""
    # KEY DIFFERENCE: Use NEUTRAL_EXPERT instead of SPECIALISTS[name]
    sys = NEUTRAL_EXPERT
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


@register("as_v5_neutral")
class ASV5NeutralRunner(Runner):
    """AS-V5-Neutral: Persona ablation variant (all specialists use NEUTRAL_EXPERT)."""

    config_id = "as_v5_neutral"
    model = "panel-as-v5-neutral"

    async def run(self, question: Question, seed: int = 42) -> RunResult:
        t0 = time.time()
        user_prompt = _build_prompt(question)
        usage = TokenUsage()
        transcript = [
            Message(role="user", speaker="user", content=user_prompt)
        ]

        # Phase 1: Fast-track panel (3 specialists) answer independently
        tasks = [
            _specialist_independent(name, user_prompt, FAST_TRACK_PANEL_V5, seed)
            for name in FAST_TRACK_PANEL_V5.keys()
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
                    metadata={"fast_track": True, "panel_size": 3, "persona_mode": "neutral"},
                )

        # Phase 2: Disagreement detected, escalate to full panel
        # Add 2 more specialists
        additional_names = [
            name for name in FULL_PANEL_V5.keys()
            if name not in FAST_TRACK_PANEL_V5
        ]
        additional_tasks = [
            _specialist_independent(name, user_prompt, FULL_PANEL_V5, seed)
            for name in additional_names
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
            metadata={"fast_track": False, "panel_size": 5, "persona_mode": "neutral"},
        )
