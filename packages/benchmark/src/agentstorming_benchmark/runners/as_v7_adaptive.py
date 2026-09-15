# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""AS-V7-Adaptive: Question-level adaptive persona routing.

Key innovation from iter140: Persona effect is benchmark-dependent.
- Multi-domain tasks NEED personas (-72.6pp without on MMLU-Pro)
- Single-domain tasks DON'T need personas (+5-6pp without on MuSR/TruthfulQA)

Solution: Classify each question and route to optimal strategy:
- Multi-domain → diverse personas (AS-V5-adaptive logic)
- Single-domain → neutral expert (AS-V5-neutral logic)

Expected gains:
- MMLU-Pro: maintain 90% (mostly multi-domain)
- MuSR: improve from 66% to 72% (mostly single-domain)
- TruthfulQA: improve from 89.5% to 94.9% (mostly single-domain)
- Cost: reduce by 30-40% (single-domain uses fast-track more often)

See: /opt/agentstorming/driver/decisions/20260517T190630-iter140-design-as-v7-adaptive.md
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
from ..domain_classifier import classify_question_domain, get_detected_domains


# Strong heterogeneous panel (all Sonnet-class or better)
FAST_TRACK_PANEL_V7 = {
    "mathematician": "us.anthropic.claude-sonnet-4-6",
    "deep-learning-scientist": "us.anthropic.claude-sonnet-4-6",
    "physics-scientist": "us.deepseek.r1-v1:0",
}

FULL_PANEL_V7 = {
    **FAST_TRACK_PANEL_V7,
    "computer-scientist": "us.amazon.nova-pro-v1:0",
    "chemist": "us.meta.llama3-3-70b-instruct-v1:0",
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
        use_personas: If True, use role-specific persona; if False, use NEUTRAL_EXPERT

    Returns:
        (name, response, usage)
    """
    # KEY: Route to persona or neutral based on domain classification
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


@register("as_v7_adaptive")
class ASV7AdaptiveRunner(Runner):
    """AS-V7: Question-level adaptive persona routing.

    Classifies each question as single-domain or multi-domain, then routes:
    - Multi-domain → diverse personas (enable cross-domain synthesis)
    - Single-domain → neutral expert (avoid over-specialization)
    """

    config_id = "as_v7_adaptive"
    model = "panel-as-v7-adaptive"

    async def run(self, question: Question, seed: int = 42) -> RunResult:
        t0 = time.time()
        user_prompt = _build_prompt(question)
        usage = TokenUsage()
        transcript = [
            Message(role="user", speaker="user", content=user_prompt)
        ]

        # STEP 1: Classify question domain
        # Build full question text for classification
        question_full_text = f"{question.question}\n" + "\n".join(
            [f"{chr(ord('A') + i)}. {choice}" for i, choice in enumerate(question.choices or [])]
        )
        domain_classification = classify_question_domain(question_full_text)
        detected_domains = get_detected_domains(question_full_text)

        # STEP 2: Select persona strategy
        use_personas = (domain_classification == 'multi_domain')

        # Phase 1: Fast-track panel (3 specialists) answer independently
        tasks = [
            _specialist_independent(name, user_prompt, FAST_TRACK_PANEL_V7, seed, use_personas)
            for name in FAST_TRACK_PANEL_V7.keys()
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
                        "domain_classification": domain_classification,
                        "detected_domains": sorted(list(detected_domains)),
                        "use_personas": use_personas,
                    },
                )

        # Phase 2: Disagreement detected, escalate to full panel
        # Add 2 more specialists
        additional_names = [
            name for name in FULL_PANEL_V7.keys()
            if name not in FAST_TRACK_PANEL_V7
        ]
        additional_tasks = [
            _specialist_independent(name, user_prompt, FULL_PANEL_V7, seed, use_personas)
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
                "domain_classification": domain_classification,
                "detected_domains": sorted(list(detected_domains)),
                "use_personas": use_personas,
            },
        )
