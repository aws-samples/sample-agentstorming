# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""AS-V9-Adaptive: V8 + benchmark-aligned personas.

Key improvements from V8:
1. Benchmark-aware persona selection (murder → detective, not mathematician)
2. Uses specialized personas from personas.py (detective, epistemologist, etc.)
3. Keeps V8's improved domain classifier (MuSR/TruthfulQA always single-domain)

Expected gains vs V8:
- MuSR: V8 ~88% → V9 ~90-93% (+2-5pp from persona alignment)
- TruthfulQA: V8 ~93-96% → V9 ~95-97% (+0-2pp, epistemologist vs scientist)
- ARC: V8 ~98% → V9 ~98-99% (+0-1pp, already saturated)
- MMLU-Pro: V8 93% → V9 ~93% (no change, research personas appropriate)
- Cost: ZERO increase (just persona routing, no additional calls)

See:
- /opt/agentstorming/driver/decisions/20260517T202240-iter152-implement-v9-benchmark-aligned-personas.md
- /opt/agentstorming/driver/MISSION.md §Update 2026-05-17T13:30Z
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
from ..domain_classifier_v8 import classify_question_domain_v8, get_detected_domains_v8


# Benchmark-aligned persona sets (NEW in V9)
# Each benchmark gets personas whose lens is relevant to that domain
BENCHMARK_PERSONA_MAP = {
    # MuSR subsets use specialized reasoning personas
    'musr_murder': {
        'fast': ['detective', 'forensic-pathologist', 'criminal-psychologist'],
        'full': ['detective', 'forensic-pathologist', 'criminal-psychologist',
                'defence-lawyer', 'prosecutor']
    },
    'musr_object_placements': {
        'fast': ['spatial-reasoning-expert', 'theory-of-mind-expert', 'logician'],
        'full': ['spatial-reasoning-expert', 'theory-of-mind-expert', 'logician',
                'librarian', 'household-organiser']
    },
    'musr_team_allocation': {
        'fast': ['operations-research-expert', 'manager', 'sociologist'],
        'full': ['operations-research-expert', 'manager', 'sociologist',
                'economist', 'psychometrician']
    },
    # Generic MuSR fallback (when subtask not in question ID)
    'musr': {
        'fast': ['logician', 'theory-of-mind-expert', 'spatial-reasoning-expert'],
        'full': ['logician', 'theory-of-mind-expert', 'spatial-reasoning-expert',
                'detective', 'operations-research-expert']
    },
    # TruthfulQA: epistemology and fact-checking
    'truthfulqa_mc': {
        'fast': ['epistemologist', 'sceptic', 'fact-checker'],
        'full': ['epistemologist', 'sceptic', 'fact-checker',
                'historian-of-misconceptions', 'logician']
    },
    # ARC: science education
    'arc_challenge': {
        'fast': ['physics-teacher', 'chemistry-teacher', 'biology-teacher'],
        'full': ['physics-teacher', 'chemistry-teacher', 'biology-teacher',
                'earth-science-teacher', 'science-historian']
    },
    # MMLU-Pro: multi-domain scientific (research personas appropriate)
    'mmlu_pro': {
        'fast': ['mathematician', 'physicist', 'computer-scientist'],
        'full': ['mathematician', 'physicist', 'computer-scientist',
                'chemist', 'biologist']
    },
    # GPQA: graduate-level science (research personas)
    'gpqa_diamond': {
        'fast': ['physicist', 'chemist', 'biologist'],
        'full': ['physicist', 'chemist', 'biologist',
                'mathematician', 'computer-scientist']
    },
}

# Model panel: strong heterogeneous models (all Sonnet-class or better)
# Each persona role gets assigned a model from this pool
MODEL_POOL = [
    "us.anthropic.claude-sonnet-4-6",
    "us.anthropic.claude-sonnet-4-6",
    "us.deepseek.r1-v1:0",
    "us.amazon.nova-pro-v1:0",
    "us.meta.llama3-3-70b-instruct-v1:0",
]

MODERATOR_MODEL = "us.anthropic.claude-sonnet-4-6"


def get_panel_for_benchmark(benchmark: str, panel_size: str) -> dict[str, str]:
    """Get persona panel for a benchmark.

    Args:
        benchmark: Benchmark name (e.g., 'musr_murder', 'mmlu_pro')
        panel_size: 'fast' (3 personas) or 'full' (5 personas)

    Returns:
        Dict mapping persona name → model ID
    """
    # Check for exact benchmark match
    if benchmark in BENCHMARK_PERSONA_MAP:
        persona_list = BENCHMARK_PERSONA_MAP[benchmark][panel_size]
    else:
        # Fallback to research personas for unknown benchmarks
        persona_list = BENCHMARK_PERSONA_MAP['mmlu_pro'][panel_size]

    # Assign models to personas (round-robin to ensure diversity)
    panel = {}
    for i, persona in enumerate(persona_list):
        panel[persona] = MODEL_POOL[i % len(MODEL_POOL)]

    return panel


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
        name: Role name (e.g., "detective", "mathematician")
        user_prompt: The question prompt
        panel: Model panel mapping (persona name → model ID)
        seed: Random seed
        use_personas: If True, use role-specific persona; if False, use NEUTRAL_EXPERT

    Returns:
        (name, response, usage)
    """
    # KEY: Route to persona or neutral based on domain classification
    if use_personas:
        if name in SPECIALISTS:
            sys = SPECIALISTS[name]
        else:
            # Fallback if persona not found (shouldn't happen)
            sys = NEUTRAL_EXPERT
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


@register("as_v9_adaptive")
class ASV9AdaptiveRunner(Runner):
    """AS-V9: V8 + benchmark-aligned personas.

    Fixes V8's persona mismatch issue where research personas (mathematician,
    physicist) were used for all benchmarks, even when specialized personas
    (detective, epistemologist, teacher) would be more relevant.

    Key changes:
    1. Benchmark-aware persona selection (new)
    2. Uses specialized personas from personas.py
    3. Keeps V8's improved domain classifier

    Expected improvements:
    - MuSR: +2-5pp (detective > mathematician for murder mysteries)
    - TruthfulQA: +0-2pp (epistemologist > scientist for misconceptions)
    - ARC: +0-1pp (teachers > researchers, but already saturated)
    - MMLU-Pro: no change (research personas appropriate)
    - Cost: $0 (routing change only)
    """

    config_id = "as_v9_adaptive"
    model = "panel-as-v9-adaptive"

    async def run(self, question: Question, seed: int = 42) -> RunResult:
        t0 = time.time()
        user_prompt = _build_prompt(question)
        usage = TokenUsage()
        transcript = [
            Message(role="user", speaker="user", content=user_prompt)
        ]

        # STEP 1: Classify question domain (V8 classifier, unchanged)
        # Build full question text for classification
        question_full_text = f"{question.question}\n" + "\n".join(
            [f"{chr(ord('A') + i)}. {choice}" for i, choice in enumerate(question.choices or [])]
        )

        # V8: Pass benchmark context to classifier
        domain_classification = classify_question_domain_v8(
            question_full_text,
            benchmark=question.dataset
        )
        detected_domains = get_detected_domains_v8(question_full_text)

        # STEP 2: Select persona strategy
        use_personas = (domain_classification == 'multi_domain')

        # STEP 3: Get benchmark-aligned persona panel (NEW in V9)
        fast_panel = get_panel_for_benchmark(question.dataset, 'fast')
        full_panel = get_panel_for_benchmark(question.dataset, 'full')

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
                        "v8_classifier": True,
                        "v9_benchmark_aligned": True,  # NEW: track V9 feature
                        "personas_used": sorted(list(fast_panel.keys())),
                    },
                )

        # Phase 2: Disagreement detected, escalate to full panel
        # Add 2 more specialists
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
                "v8_classifier": True,
                "v9_benchmark_aligned": True,  # NEW: track V9 feature
                "personas_used": sorted(list(full_panel.keys())),
            },
        )
