# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""AS-V8-NoPersona: Model diversity without personas.

Key insight from persona ablation (iter164-169):
- TruthfulQA (N=518): personas have ZERO effect (p=1.0000)
- MMLU-Pro (N=51): personas have ZERO effect (p=0.5000)
- ARC-Challenge (N=250): personas have ZERO effect (p>0.05, iter164)
- MuSR-Team (N=250): aligned personas HURT performance (-1.2%, iter169)

Hypothesis refinement:
- Original: "model diversity + persona diversity → better reasoning"
- Revised: "model diversity ALONE → better reasoning, personas unnecessary"

V8 Design:
- Same protocol as V7 (fast-track, consensus-based escalation, moderator synthesis)
- Same heterogeneous model panel (diverse vendors/families)
- ONE CHANGE: Always use NEUTRAL_EXPERT, never role-specific personas
- Expected: Same accuracy as V7, 5-10% lower cost (simpler prompts)

This satisfies MISSION Criterion 2: "cost-quality Pareto frontier"

See:
- /opt/agentstorming/driver/decisions/20260517T213500-iter167-prepare-v8-design-and-analysis.md
- /opt/agentstorming/driver/AS-V8-DESIGN.md
- MISSION.md §Update 2026-05-17T13:30Z
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


# Same heterogeneous panel as V7 (model diversity preserved)
FAST_TRACK_PANEL_V8 = {
    "expert-1": "us.anthropic.claude-sonnet-4-6",
    "expert-2": "us.anthropic.claude-sonnet-4-6",
    "expert-3": "us.deepseek.r1-v1:0",
}

FULL_PANEL_V8 = {
    **FAST_TRACK_PANEL_V8,
    "expert-4": "us.amazon.nova-pro-v1:0",
    "expert-5": "us.meta.llama3-3-70b-instruct-v1:0",
}

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


async def _expert_independent(
    name: str,
    user_prompt: str,
    panel: dict,
    seed: int,
) -> tuple[str, str, TokenUsage]:
    """Expert answers independently using NEUTRAL_EXPERT prompt.

    Args:
        name: Role name (e.g., "expert-1")
        user_prompt: The question prompt
        panel: Model panel mapping
        seed: Random seed

    Returns:
        (name, response, usage)
    """
    # KEY CHANGE FROM V7: Always use NEUTRAL_EXPERT, never role-specific personas
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

    mod_prompt = f"""The following experts have answered this question independently:

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

    mod_prompt = f"""The following experts have answered this question independently:

{context}

Your task: Synthesize their answers into a final answer.
These experts disagree, so carefully assess the quality of each argument:
- Which reasoning is most sound?
- Which cites the strongest evidence?
- Which approach is most rigorous?

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


@register("as_v8_nopersona")
class ASV8NoPersonaRunner(Runner):
    """AS-V8: Model diversity without personas.

    Same protocol as V7 (fast-track → consensus check → escalation → synthesis)
    Same heterogeneous model panel (Anthropic, DeepSeek, Amazon, Meta)
    ONE CHANGE: All experts use NEUTRAL_EXPERT prompt (no role-specific personas)

    Rationale: Persona ablation (iter164-169) shows personas have zero or negative effect.
    Expected: Same accuracy as V7, lower cost (5-10% savings from simpler prompts).
    """

    config_id = "as_v8_nopersona"
    model = "panel-as-v8-nopersona"

    async def run(self, question: Question, seed: int = 42) -> RunResult:
        t0 = time.time()
        user_prompt = _build_prompt(question)
        usage = TokenUsage()
        transcript = [
            Message(role="user", speaker="user", content=user_prompt)
        ]

        # Phase 1: Fast-track panel (3 experts) answer independently
        # ALL use NEUTRAL_EXPERT (no personas)
        tasks = [
            _expert_independent(name, user_prompt, FAST_TRACK_PANEL_V8, seed)
            for name in FAST_TRACK_PANEL_V8.keys()
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
                        "use_personas": False,  # V8 never uses personas
                    },
                )

        # Phase 2: Disagreement detected, escalate to full panel
        # Add 2 more experts (also using NEUTRAL_EXPERT)
        additional_names = [
            name for name in FULL_PANEL_V8.keys()
            if name not in FAST_TRACK_PANEL_V8
        ]
        additional_tasks = [
            _expert_independent(name, user_prompt, FULL_PANEL_V8, seed)
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
            metadata={
                "fast_track": False,
                "panel_size": 5,
                "use_personas": False,  # V8 never uses personas
            },
        )
