# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""AS-V6-Evidential: Moderator prioritizes evidence quality over vote count.

Design from iter 117: Addresses "majority tyranny" failure mode on musr_murder.

Key insight: AS-V5 loses when minority speaker has stronger forensic evidence
but moderator defers to majority vote. Solution: teach moderator to evaluate
argument QUALITY (evidence hierarchy) rather than counting votes.

Changes from AS-V5:
1. Moderator uses evidence hierarchy (physical > forensic > opportunity > motive)
2. Moderator must justify: "What is strongest evidence? Who presented it?"
3. Single speaker with forensic link can override majority with circumstantial

Expected impact:
- Flip 5 "minority correct" cases on musr_murder
- Flip 2-3 "unanimous wrong" cases with better reasoning
- Push musr_murder from p=0.098 to p<0.05

Panel: Same as AS-V5 (heterogeneous strong)
Cost: ~$0.038/q (same as AS-V5)
"""

from __future__ import annotations

import asyncio
import time
from collections import Counter

from .base import Runner, register
from ..types import Question, Message, TokenUsage, RunResult
from ..models import ChatMessage, converse, extract_multiple_choice, extract_numeric
from ..personas import SPECIALISTS, MODERATOR
from ..datasets.mmlu_pro import format_mcq_prompt
from ..datasets.math500 import format_math_prompt


# Strong heterogeneous panel (all Sonnet-class or better)
FAST_TRACK_PANEL_V6E = {
    "mathematician": "us.anthropic.claude-sonnet-4-6",
    "deep-learning-scientist": "us.anthropic.claude-sonnet-4-6",
    "physics-scientist": "us.deepseek.r1-v1:0",
}

FULL_PANEL_V6E = {
    **FAST_TRACK_PANEL_V6E,
    "computer-scientist": "us.amazon.nova-pro-v1:0",
    "chemist": "us.meta.llama3-3-70b-instruct-v1:0",
}

MODERATOR_MODEL = "us.anthropic.claude-sonnet-4-6"

# Evidence hierarchy for moderator
EVIDENCE_HIERARCHY = """
**Evidence strength hierarchy** (strongest first):
1. **Direct physical evidence**: Weapon ownership + presence at scene with physical trace
2. **Forensic links**: Tool/weapon expertise + tool is confirmed murder weapon
3. **Opportunity**: Confirmed presence at scene + no alibi + access to means
4. **Motive**: Personal grudge, financial gain, documented conflict
5. **Character traits**: Suspicious behavior, evasiveness, demeanor
"""


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
    name: str, user_prompt: str, panel: dict, seed: int
) -> tuple[str, str, TokenUsage]:
    """Specialist answers independently. Returns (name, response, usage)."""
    sys = SPECIALISTS[name]
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


async def _moderator_synthesis_evidential(
    user_prompt: str, responses: list[tuple[str, str]], seed: int, consensus: bool
) -> tuple[str, TokenUsage]:
    """Evidential synthesis: prioritize evidence quality over vote count.

    Key change from AS-V5: Add evidence hierarchy and require explicit justification.
    """
    context = "\n\n".join([
        f"**{name}:** {resp}" for name, resp in responses
    ])

    if consensus:
        # Consensus case (2+/3 agree) — still check quality
        mod_prompt = f"""The following specialists have answered this question independently:

{context}

Your task: Synthesize their answers and provide the final answer.

{EVIDENCE_HIERARCHY}

**Even though there appears to be consensus, verify the quality of reasoning:**
- Does the majority position cite strong evidence (top 3 in hierarchy)?
- Or does a dissenting voice cite forensic/physical evidence overlooked by the majority?

If the majority reasoning is sound, accept it. But if a minority speaker identifies
critical evidence the majority missed, prioritize that speaker's reasoning.

Before stating your final answer, briefly justify:
1. What is the strongest evidence presented?
2. Which speaker(s) presented it?
3. Does this evidence support the majority or minority position?

End with `Final answer: X` on its own line.
"""
    else:
        # Disagreement case (3-way split or no strong consensus)
        mod_prompt = f"""The following specialists have answered this question independently:

{context}

Your task: Synthesize their answers into a final answer.

{EVIDENCE_HIERARCHY}

**These specialists disagree. Evaluate evidence STRENGTH, not vote count:**
- Which speaker cites the highest-tier evidence in the hierarchy?
- Is there physical or forensic evidence that others overlooked?
- Are there logical flaws in any position?

**A single speaker with forensic evidence should override multiple speakers
with only circumstantial reasoning (motive/character).**

Before stating your final answer, explicitly justify:
1. What is the strongest evidence presented?
2. Which speaker(s) presented it?
3. Why does this evidence outweigh other positions?

End with `Final answer: X` on its own line.
"""

    text, u = await converse(
        [ChatMessage(role="user", text=f"{user_prompt}\n\n{mod_prompt}")],
        system=MODERATOR,
        model=MODERATOR_MODEL,
        temperature=0.3,
    )
    return text, u


@register("as_v6_evidential")
class ASV6EvidentialRunner(Runner):
    """AS-V6-Evidential: Moderator evaluates evidence quality over vote count."""

    config_id = "as_v6_evidential"
    model = "panel-as-v6-evidential"

    async def run(self, question: Question, seed: int = 42) -> RunResult:
        t0 = time.time()
        user_prompt = _build_prompt(question)
        usage = TokenUsage()
        transcript = [
            Message(role="user", speaker="user", content=user_prompt)
        ]

        # Phase 1: Fast-track panel (3 specialists) answer independently
        tasks = [
            _specialist_independent(name, user_prompt, FAST_TRACK_PANEL_V6E, seed)
            for name in FAST_TRACK_PANEL_V6E.keys()
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
        consensus = False
        if len(fast_answers) >= 2:
            counts = Counter(fast_answers)
            most_common = counts.most_common(1)[0]
            if most_common[1] >= 2:
                consensus = True
                # Use evidential synthesis (checks quality even with consensus)
                mod_resp, mod_u = await _moderator_synthesis_evidential(
                    user_prompt,
                    [(name, resp) for name, resp, _ in results],
                    seed,
                    consensus=True
                )
                usage += mod_u
                transcript.append(Message(role="assistant", speaker="moderator", content=mod_resp))

                final_answer = _extract(question, mod_resp)

                return self._build_result(
                    question, seed, transcript, final_answer, usage, t0,
                    final_statement=mod_resp,
                    moderator_turns=1,
                    participant_turns=3,
                    metadata={"fast_track": True, "panel_size": 3, "consensus": True},
                )

        # Phase 2: Disagreement detected, escalate to full panel
        # Add 2 more specialists
        additional_names = [
            name for name in FULL_PANEL_V6E.keys()
            if name not in FAST_TRACK_PANEL_V6E
        ]
        additional_tasks = [
            _specialist_independent(name, user_prompt, FULL_PANEL_V6E, seed)
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

        # Evidential synthesis with quality-weighting (disagreement case)
        mod_resp, mod_u = await _moderator_synthesis_evidential(
            user_prompt,
            all_responses,
            seed,
            consensus=False
        )
        usage += mod_u
        transcript.append(Message(role="assistant", speaker="moderator", content=mod_resp))

        final_answer = _extract(question, mod_resp)

        return self._build_result(
            question, seed, transcript, final_answer, usage, t0,
            final_statement=mod_resp,
            moderator_turns=1,
            participant_turns=5,
            metadata={"fast_track": False, "panel_size": 5, "consensus": False},
        )
