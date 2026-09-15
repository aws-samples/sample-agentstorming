# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""AS-V6-Safeguard: Adaptive compute with difficulty-aware fast-track gating.

Builds on AS-V5 but adds safety checks to prevent premature fast-tracking
on questions that show subtle difficulty signals.

Key changes from AS-V5:
1. Confidence threshold: ALL 3 agents ≥ 0.7 AND average ≥ 0.85
2. Difficulty detector: Only fast-track if difficulty_score ≤ 3.0
3. Trap word detection: Block fast-track on negation-heavy questions

Hypothesis: This prevents the "over-eager fast-track" failure mode where
unanimous low-confidence consensus leads to wrong answers (e.g., mmlu_pro-1016).

Cost profile (same as AS-V5):
- Easy questions: 4 calls (3 specialists + 1 moderator)
- Hard questions: 7 calls (5 specialists + 1 moderator)
- Expected: ~5.5 calls @ ~$0.038/q

Panel: Same strong heterogeneous panel as AS-V5.
"""

from __future__ import annotations

import asyncio
import re
import time
from collections import Counter

from .base import Runner, register
from ..types import Question, Message, TokenUsage, RunResult
from ..models import ChatMessage, converse, extract_multiple_choice, extract_numeric
from ..personas import SPECIALISTS, MODERATOR
from ..datasets.mmlu_pro import format_mcq_prompt
from ..datasets.math500 import format_math_prompt


# Strong heterogeneous panel (all Sonnet-class or better)
FAST_TRACK_PANEL_V6 = {
    "mathematician": "us.anthropic.claude-sonnet-4-6",
    "deep-learning-scientist": "us.anthropic.claude-sonnet-4-6",
    "physics-scientist": "us.deepseek.r1-v1:0",
}

FULL_PANEL_V6 = {
    **FAST_TRACK_PANEL_V6,
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


def compute_difficulty_score(q: Question) -> float:
    """Compute heuristic difficulty score for fast-track gating.

    Returns:
        float: Difficulty score in [1.0, 5.0]. Higher = harder.
        Fast-track only if score ≤ 3.0.
    """
    score = 1.0
    text = q.question.lower()

    # Negation markers (+1.5 each)
    negations = ['not', 'except', 'false', 'incorrect', 'never', 'least likely']
    score += 1.5 * sum(1 for neg in negations if neg in text)

    # Complex quantifiers (+1.0 each)
    quantifiers = ['most', 'least', 'all of the following', 'none of the following',
                   'which of the following', 'best describes']
    score += 1.0 * sum(1 for quant in quantifiers if quant in text)

    # Long question (>500 chars, +0.5)
    if len(q.question) > 500:
        score += 0.5

    # Many choices (>6 options, +0.5)
    if q.choices and len(q.choices) > 6:
        score += 0.5

    # Subject-specific difficulty (MMLU-Pro specific)
    if hasattr(q, 'category') and q.category:
        hard_subjects = ['abstract_algebra', 'formal_logic', 'college_physics',
                        'college_mathematics', 'machine_learning']
        if q.category in hard_subjects:
            score += 1.0

    return min(score, 5.0)


def extract_confidence(text: str) -> float | None:
    """Extract confidence from specialist response.

    Looks for patterns like:
    - "Confidence: 0.8"
    - "confidence: 85%"
    - "(confidence: 0.9)"

    Returns:
        float | None: Confidence in [0, 1], or None if not found.
    """
    # Pattern 1: "confidence: 0.8" or "confidence: 80%"
    pattern1 = r'confidence\s*[:\-]\s*(\d+(?:\.\d+)?)\s*%?'
    match = re.search(pattern1, text.lower())
    if match:
        val = float(match.group(1))
        # If val > 1, it's probably a percentage
        return val / 100.0 if val > 1 else val

    # If no explicit confidence, return None (assume we can't fast-track)
    return None


async def _specialist_independent_with_confidence(
    name: str, user_prompt: str, panel: dict, seed: int
) -> tuple[str, str, float | None, TokenUsage]:
    """Specialist answers independently with confidence score.

    Returns: (name, response, confidence, usage)
    """
    sys = SPECIALISTS[name]
    instr = (
        "You are an expert consulted on this problem. "
        "Provide your best answer independently. "
        "Think step-by-step if helpful. "
        "At the end, provide:\n"
        "1. Your confidence (0.0-1.0) in the answer\n"
        "2. Final answer: X\n\n"
        "Format:\n"
        "Confidence: 0.85\n"
        "Final answer: X\n\n"
        "Keep response ≤300 words."
    )

    model = panel[name]
    text, u = await converse(
        [ChatMessage(role="user", text=f"{user_prompt}\n\n{instr}")],
        system=sys,
        model=model,
        temperature=0.7,
    )

    confidence = extract_confidence(text)
    return name, text, confidence, u


async def _moderator_synthesis_simple(
    user_prompt: str, responses: list[tuple[str, str]], seed: int
) -> tuple[str, TokenUsage]:
    """Simple synthesis for consensus cases (high confidence unanimous)."""
    context = "\n\n".join([
        f"**{name}:** {resp}" for name, resp in responses
    ])

    mod_prompt = f"""The following specialists have answered this question independently:

{context}

Your task: Synthesize their answers and provide the final answer.
These specialists show strong consensus, so keep your synthesis brief.
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
    """Full synthesis for disagreement or low-confidence cases.

    Same as AS-V5 full synthesis.
    """
    context = "\n\n".join([
        f"**{name}:** {resp}" for name, resp in responses
    ])

    mod_prompt = f"""The following specialists have answered this question independently:

{context}

Your task: Synthesize their answers into a final answer.
Carefully assess the quality of each argument:
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


@register("as_v6_safeguard")
class ASV6SafeguardRunner(Runner):
    """AS-V6: Adaptive depth with difficulty-aware fast-track gating."""

    config_id = "as_v6_safeguard"
    model = "panel-as-v6-safeguard"

    async def run(self, q: Question, seed: int = 42) -> RunResult:
        t0 = time.time()
        user_prompt = _build_prompt(q)
        usage = TokenUsage()
        transcript = [
            Message(role="user", speaker="user", content=user_prompt)
        ]

        # Compute difficulty score upfront
        difficulty = compute_difficulty_score(q)

        # Phase 1: Fast-track panel (3 specialists) answer independently with confidence
        tasks = [
            _specialist_independent_with_confidence(name, user_prompt, FAST_TRACK_PANEL_V6, seed)
            for name in FAST_TRACK_PANEL_V6.keys()
        ]
        results = await asyncio.gather(*tasks)

        for name, resp, conf, u in results:
            usage += u
            transcript.append(Message(role="assistant", speaker=name, content=resp))

        # Extract answers and confidences
        fast_answers = []
        confidences = []
        for name, resp, conf, _ in results:
            extracted = _extract(q, resp)
            if extracted:
                fast_answers.append(extracted)
            if conf is not None:
                confidences.append(conf)

        # Fast-track decision logic (STRICTER than AS-V5)
        can_fast_track = False
        fast_track_reason = None

        if len(fast_answers) == 3:
            # Check unanimous agreement
            if len(set(fast_answers)) == 1:
                # Unanimous! Now check safety conditions

                # Condition 1: All confidences ≥ 0.7
                if len(confidences) == 3 and all(c >= 0.7 for c in confidences):
                    # Condition 2: Average confidence ≥ 0.85
                    avg_conf = sum(confidences) / len(confidences)
                    if avg_conf >= 0.85:
                        # Condition 3: Difficulty score ≤ 3.0
                        if difficulty <= 3.0:
                            can_fast_track = True
                            fast_track_reason = f"unanimous_high_conf_easy (avg_conf={avg_conf:.2f}, diff={difficulty:.1f})"
                        else:
                            fast_track_reason = f"blocked_by_difficulty (diff={difficulty:.1f} > 3.0)"
                    else:
                        fast_track_reason = f"blocked_by_avg_confidence (avg={avg_conf:.2f} < 0.85)"
                else:
                    fast_track_reason = f"blocked_by_low_confidence (confs={confidences})"
            else:
                fast_track_reason = f"no_unanimous_agreement (answers={fast_answers})"
        else:
            fast_track_reason = f"insufficient_extractions (got {len(fast_answers)}/3)"

        # If we can fast-track, use simple synthesis
        if can_fast_track:
            mod_resp, mod_u = await _moderator_synthesis_simple(
                user_prompt,
                [(name, resp) for name, resp, _, _ in results],
                seed
            )
            usage += mod_u
            transcript.append(Message(role="assistant", speaker="moderator", content=mod_resp))

            final_answer = _extract(q, mod_resp)

            return self._build_result(
                q, seed, transcript, final_answer, usage, t0,
                final_statement=mod_resp,
                moderator_turns=1,
                participant_turns=3,
                metadata={
                    "fast_track": True,
                    "fast_track_reason": fast_track_reason,
                    "panel_size": 3,
                    "difficulty_score": difficulty,
                    "avg_confidence": sum(confidences) / len(confidences) if confidences else None,
                },
            )

        # Phase 2: Cannot fast-track, escalate to full panel
        # Add 2 more specialists
        additional_names = [
            name for name in FULL_PANEL_V6.keys()
            if name not in FAST_TRACK_PANEL_V6
        ]
        additional_tasks = [
            _specialist_independent_with_confidence(name, user_prompt, FULL_PANEL_V6, seed)
            for name in additional_names
        ]
        additional_results = await asyncio.gather(*additional_tasks)

        for name, resp, conf, u in additional_results:
            usage += u
            transcript.append(Message(role="assistant", speaker="name", content=resp))

        # Combine all responses for moderator
        all_responses = [
            (name, resp) for name, resp, _, _ in results + additional_results
        ]

        # Full synthesis with quality-weighting
        mod_resp, mod_u = await _moderator_synthesis_full(
            user_prompt,
            all_responses,
            seed
        )
        usage += mod_u
        transcript.append(Message(role="assistant", speaker="moderator", content=mod_resp))

        final_answer = _extract(q, mod_resp)

        return self._build_result(
            q, seed, transcript, final_answer, usage, t0,
            final_statement=mod_resp,
            moderator_turns=1,
            participant_turns=5,
            metadata={
                "fast_track": False,
                "fast_track_reason": fast_track_reason,
                "panel_size": 5,
                "difficulty_score": difficulty,
            },
        )
