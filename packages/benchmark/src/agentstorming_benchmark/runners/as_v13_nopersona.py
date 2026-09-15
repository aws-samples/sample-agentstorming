# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""AS-V13-NoPersona: Test whether model diversity alone drives AS gains.

Key hypothesis from iter 224: Personas hurt more often than they help.
- Generic scientific personas: -6.0pp on MuSR, +2.0pp on MMLU-Pro
- Aligned task-specific personas: -2.0pp on MuSR-Murder, -1.4pp on TruthfulQA
- Neutral (no personas): consistent across benchmarks

AS-V13 removes personas entirely to test whether model heterogeneity alone
is sufficient for multi-agent deliberation gains. This simplifies the protocol
and tests the core hypothesis: structured deliberation with diverse model
backends beats baselines, without needing specialized persona prompts.

Architecture:
- Same as AS-V5-adaptive (individual-then-deliberation, adaptive synthesis)
- All agents use NEUTRAL_EXPERT prompt (no persona specialization)
- Keep heterogeneous model panel (diverse backends)
- Moderator remains moderator (structural role, not domain expertise)

Expected results (based on AS-V5-neutral partial data):
- MMLU-Pro: ~88% (AS-V5-neutral: 88.0%, AS-V5-adaptive: 90.0%) → -2pp acceptable
- TruthfulQA: ~91% (match AS-V3-indfirst: 91.8%)
- MuSR-Murder: ~76% (match AS-V5-adaptive: 76.4%)

If AS-V13 wins on 2/3 benchmarks: personas are not necessary for AS protocol
If AS-V13 loses on 2/3: diagnose new failure mode

Cost profile: Same as AS-V5-adaptive (~$0.038/q with 50/50 easy/hard split)

Implemented: iter 225, 2026-05-18T02:20Z
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


# Strong heterogeneous panel (all Sonnet-class or better)
# Same models as AS-V5, but ALL use NEUTRAL_EXPERT prompt
FAST_TRACK_PANEL_V13 = {
    "expert-1": "us.anthropic.claude-sonnet-4-6",
    "expert-2": "us.anthropic.claude-sonnet-4-6",
    "expert-3": "us.deepseek.r1-v1:0",
}

FULL_PANEL_V13 = {
    **FAST_TRACK_PANEL_V13,
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
    name: str, user_prompt: str, panel: dict, seed: int
) -> tuple[str, str, TokenUsage]:
    """Expert answers independently with NEUTRAL_EXPERT prompt. Returns (name, response, usage)."""
    # All agents use NEUTRAL_EXPERT - no persona specialization
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
    user_prompt: str, responses: list[tuple[str, str, TokenUsage]], seed: int
) -> tuple[str, TokenUsage]:
    """Simple synthesis for consensus cases (2+/3 agree)."""
    context = "\n\n".join([
        f"**{name}:** {resp}" for name, resp, _ in responses
    ])

    prompt = f"""The original question:

{user_prompt}

Three experts answered independently:

{context}

You are the moderator. Review their answers. If 2 or more agree on the same answer,
return that consensus. Otherwise, synthesize the best answer from their reasoning.

Respond with `Final answer: X` on its own line."""

    text, u = await converse(
        [ChatMessage(role="user", text=prompt)],
        system=MODERATOR,
        model=MODERATOR_MODEL,
        temperature=0.0,
    )
    return text, u


async def _moderator_synthesis_full(
    user_prompt: str,
    fast_responses: list[tuple[str, str, TokenUsage]],
    full_responses: list[tuple[str, str, TokenUsage]],
    seed: int,
) -> tuple[str, TokenUsage]:
    """Full deliberation synthesis when fast-track shows disagreement."""
    fast_context = "\n\n".join([
        f"**{name}:** {resp}" for name, resp, _ in fast_responses
    ])
    full_context = "\n\n".join([
        f"**{name}:** {resp}" for name, resp, _ in full_responses
    ])

    prompt = f"""The original question:

{user_prompt}

Initial three experts disagreed:

{fast_context}

Two additional experts provided input:

{full_context}

You are the moderator. Synthesize the best answer from all five experts' reasoning.
Weight by argument quality, not by consensus count. Look for the most rigorous reasoning.

Respond with `Final answer: X` on its own line."""

    text, u = await converse(
        [ChatMessage(role="user", text=prompt)],
        system=MODERATOR,
        model=MODERATOR_MODEL,
        temperature=0.0,
    )
    return text, u


async def _run_question(runner: "ASV13NoPersona", q: Question, seed: int) -> RunResult:
    """Run AS-V13 on a single question with adaptive synthesis."""
    t0 = time.time()
    transcript: list[Message] = []
    usage = TokenUsage(input_tokens=0, output_tokens=0)

    user_prompt = _build_prompt(q)

    # Phase 1: Fast-track panel (3 experts) answer independently
    tasks = [
        _expert_independent(name, user_prompt, FAST_TRACK_PANEL_V13, seed)
        for name in FAST_TRACK_PANEL_V13.keys()
    ]
    fast_results = await asyncio.gather(*tasks)

    for name, text, u in fast_results:
        transcript.append(Message(role="assistant", speaker=name, content=text))
        usage.input_tokens += u.input_tokens
        usage.output_tokens += u.output_tokens

    # Extract answers from fast-track
    fast_answers = []
    for name, text, _ in fast_results:
        ans = _extract(q, text)
        if ans and ans != "INVALID":
            fast_answers.append(ans)

    # Check consensus: if 2+/3 agree, early exit
    if fast_answers:
        counts = Counter(fast_answers)
        most_common_ans, most_common_count = counts.most_common(1)[0]
        if most_common_count >= 2:
            # Consensus found - simple synthesis
            mod_text, mod_u = await _moderator_synthesis_simple(
                user_prompt, fast_results, seed
            )
            transcript.append(Message(role="assistant", speaker="moderator", content=mod_text))
            usage.input_tokens += mod_u.input_tokens
            usage.output_tokens += mod_u.output_tokens

            final_answer = _extract(q, mod_text)
            return runner._build_result(
                q, seed, transcript, final_answer, usage, t0,
                final_statement=mod_text,
                moderator_turns=1,
                participant_turns=3,
                metadata={"path": "fast_consensus", "fast_track_consensus": most_common_ans},
            )

    # Phase 2: Disagreement detected - add 2 more experts
    additional_names = [n for n in FULL_PANEL_V13.keys() if n not in FAST_TRACK_PANEL_V13]
    tasks = [
        _expert_independent(name, user_prompt, FULL_PANEL_V13, seed)
        for name in additional_names
    ]
    additional_results = await asyncio.gather(*tasks)

    for name, text, u in additional_results:
        transcript.append(Message(role="assistant", speaker=name, content=text))
        usage.input_tokens += u.input_tokens
        usage.output_tokens += u.output_tokens

    # Full deliberation synthesis
    mod_text, mod_u = await _moderator_synthesis_full(
        user_prompt, fast_results, additional_results, seed
    )
    messages.append(Message(speaker="moderator", text=mod_text))
    total_usage.input_tokens += mod_u.input_tokens
    total_usage.output_tokens += mod_u.output_tokens

    final_answer = _extract(q, mod_text)
    return runner._build_result(
        q, seed, transcript, final_answer, usage, t0,
        final_statement=mod_text,
        moderator_turns=1,
        participant_turns=5,
        metadata={"path": "full_deliberation"},
    )


@register("as_v13_nopersona")
class ASV13NoPersona(Runner):
    """AS-V13: No personas, heterogeneous models only."""

    async def run(self, question: Question, seed: int = 42) -> RunResult:
        return await _run_question(self, question, seed)
