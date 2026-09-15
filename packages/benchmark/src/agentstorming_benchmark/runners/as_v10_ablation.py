# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""AS-V10-Ablation: 2×2 persona-vs-model factorial ablation matrix.

This is a proper factorial experiment to cleanly separate:
- Effect of model heterogeneity (homogeneous vs heterogeneous panel)
- Effect of persona diversity (diverse personas vs neutral expert)
- Interaction between the two

Four conditions:
1. V10-HetPers: Heterogeneous models + Diverse personas
2. V10-HetNeut: Heterogeneous models + Neutral expert
3. V10-HomPers: Homogeneous models + Diverse personas
4. V10-HomNeut: Homogeneous models + Neutral expert

All four share:
- Same adaptive depth logic (fast-track 3, escalate to 5 on disagreement)
- Same moderator model (Sonnet-4.6)
- Same synthesis strategy
- Same hyperparameters

Design rationale:
- If personas matter: HetPers > HetNeut AND HomPers > HomNeut
- If model diversity matters: HetPers > HomPers AND HetNeut > HomNeut
- If super-additive: HetPers > (HetNeut + HomPers - HomNeut)

See: /opt/agentstorming/driver/decisions/20260517T203230-iter154-prepare-v10-persona-model-matrix.md
"""

from __future__ import annotations

import asyncio
import time
from collections import Counter

from .base import Runner, register
from ..types import Question, Message, TokenUsage, RunResult
from ..models import ChatMessage, converse, extract_multiple_choice, extract_numeric
from ..personas import SPECIALISTS, NEUTRAL_EXPERT, MODERATOR
from ..datasets.mmlu_pro import format_mcq_prompt
from ..datasets.math500 import format_math_prompt


# Heterogeneous panel (strong diverse models)
FAST_TRACK_PANEL_HET = {
    "mathematician": "us.anthropic.claude-sonnet-4-6",
    "deep-learning-scientist": "us.anthropic.claude-sonnet-4-6",
    "physics-scientist": "us.deepseek.r1-v1:0",
}

FULL_PANEL_HET = {
    **FAST_TRACK_PANEL_HET,
    "computer-scientist": "us.amazon.nova-pro-v1:0",
    "chemist": "us.meta.llama3-3-70b-instruct-v1:0",
}

# Homogeneous panel (all same model)
FAST_TRACK_PANEL_HOM = {
    "mathematician": "us.anthropic.claude-sonnet-4-6",
    "deep-learning-scientist": "us.anthropic.claude-sonnet-4-6",
    "physics-scientist": "us.anthropic.claude-sonnet-4-6",
}

FULL_PANEL_HOM = {
    **FAST_TRACK_PANEL_HOM,
    "computer-scientist": "us.anthropic.claude-sonnet-4-6",
    "chemist": "us.anthropic.claude-sonnet-4-6",
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


async def _specialist_independent(
    name: str,
    user_prompt: str,
    panel: dict,
    seed: int,
    use_personas: bool = True
) -> tuple[str, str, TokenUsage]:
    """Specialist answers independently.

    Args:
        name: Role name (e.g., "mathematician")
        user_prompt: The question prompt
        panel: Model panel mapping
        seed: Random seed
        use_personas: If True, use SPECIALISTS[name]; if False, use NEUTRAL_EXPERT

    Returns:
        (name, response, usage)
    """
    if use_personas:
        sys = SPECIALISTS[name]
    else:
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


async def _run_ablation(
    runner_instance: Runner,
    question: Question,
    seed: int,
    fast_panel: dict,
    full_panel: dict,
    use_personas: bool,
    condition_name: str,
) -> RunResult:
    """Shared logic for all four ablation conditions.

    Args:
        runner_instance: The runner instance (for _build_result)
        question: The question to answer
        seed: Random seed
        fast_panel: Fast-track panel (3 specialists)
        full_panel: Full panel (5 specialists)
        use_personas: True for diverse personas, False for neutral expert
        condition_name: Condition identifier for metadata

    Returns:
        RunResult
    """
    t0 = time.time()
    user_prompt = _build_prompt(question)
    usage = TokenUsage()
    transcript = [
        Message(role="user", speaker="user", content=user_prompt)
    ]

    # Phase 1: Fast-track panel (3 specialists) answer independently
    tasks = [
        _specialist_independent(name, user_prompt, fast_panel, seed, use_personas)
        for name in fast_panel.keys()
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

            return runner_instance._build_result(
                question, seed, transcript, final_answer, usage, t0,
                final_statement=mod_resp,
                moderator_turns=1,
                participant_turns=3,
                metadata={
                    "fast_track": True,
                    "panel_size": 3,
                    "condition": condition_name,
                    "use_personas": use_personas,
                },
            )

    # Phase 2: Disagreement detected, escalate to full panel
    additional_names = [
        name for name in full_panel.keys()
        if name not in fast_panel
    ]
    additional_tasks = [
        _specialist_independent(name, user_prompt, full_panel, seed, use_personas)
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

    return runner_instance._build_result(
        question, seed, transcript, final_answer, usage, t0,
        final_statement=mod_resp,
        moderator_turns=1,
        participant_turns=5,
        metadata={
            "fast_track": False,
            "panel_size": 5,
            "condition": condition_name,
            "use_personas": use_personas,
        },
    )


@register("as_v10_het_pers")
class ASV10HetPersRunner(Runner):
    """V10 Condition 1: Heterogeneous models + Diverse personas."""

    config_id = "as_v10_het_pers"
    model = "panel-as-v10-het-pers"

    async def run(self, question: Question, seed: int = 42) -> RunResult:
        return await _run_ablation(
            self, question, seed,
            FAST_TRACK_PANEL_HET, FULL_PANEL_HET,
            use_personas=True,
            condition_name="het_pers"
        )


@register("as_v10_het_neut")
class ASV10HetNeutRunner(Runner):
    """V10 Condition 2: Heterogeneous models + Neutral expert."""

    config_id = "as_v10_het_neut"
    model = "panel-as-v10-het-neut"

    async def run(self, question: Question, seed: int = 42) -> RunResult:
        return await _run_ablation(
            self, question, seed,
            FAST_TRACK_PANEL_HET, FULL_PANEL_HET,
            use_personas=False,
            condition_name="het_neut"
        )


@register("as_v10_hom_pers")
class ASV10HomPersRunner(Runner):
    """V10 Condition 3: Homogeneous models + Diverse personas."""

    config_id = "as_v10_hom_pers"
    model = "panel-as-v10-hom-pers"

    async def run(self, question: Question, seed: int = 42) -> RunResult:
        return await _run_ablation(
            self, question, seed,
            FAST_TRACK_PANEL_HOM, FULL_PANEL_HOM,
            use_personas=True,
            condition_name="hom_pers"
        )


@register("as_v10_hom_neut")
class ASV10HomNeutRunner(Runner):
    """V10 Condition 4: Homogeneous models + Neutral expert."""

    config_id = "as_v10_hom_neut"
    model = "panel-as-v10-hom-neut"

    async def run(self, question: Question, seed: int = 42) -> RunResult:
        return await _run_ablation(
            self, question, seed,
            FAST_TRACK_PANEL_HOM, FULL_PANEL_HOM,
            use_personas=False,
            condition_name="hom_neut"
        )
