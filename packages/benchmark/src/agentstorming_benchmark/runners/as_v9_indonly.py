# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""AS-V9-IndOnly: Independent proposals → vote (no deliberation).

MISSION: Test hypothesis H_musr (iter177):
- musr_team_allocation is the ONLY benchmark where AS loses to a baseline (sc_k11)
- Hypothesis: Deliberation on deterministic logic puzzles reduces effective diversity
  through groupthink/convergence, while high-k independent sampling preserves exploration
- sc_k11: 82.8%, AS-V5-adaptive: 81.6% (-1.2% gap)

Design:
- Same heterogeneous model panel as V8 (diverse vendors/families)
- Same NEUTRAL_EXPERT prompts (no personas)
- KEY CHANGE: Skip moderator synthesis entirely
- Mechanism: All agents answer independently → majority vote on extracted answers
- No deliberation, no discussion, no moderation
- Maximum independence (like sc_k11 but with model diversity)

Expected outcome:
- If AS-V9-IndOnly beats sc_k11 on musr_team_allocation:
  → Confirms deliberation was the problem (H_musr validated)
  → Suggests task-adaptive protocol (deliberation for some tasks, independent for others)
- If AS-V9-IndOnly still loses:
  → Rules out "independence" as sufficient
  → Try hybrid design (Option C from iter177)

Cost: ~60% cheaper than V8 (no moderator calls, fixed 5 expert calls)

See:
- /opt/agentstorming/driver/decisions/20260517T221136-iter177-musr-team-allocation-investigation.md
- MISSION.md §Update 2026-05-17T13:30Z
"""

from __future__ import annotations

import asyncio
import time
from collections import Counter

from .base import Runner, register
from ..types import Question, Message, TokenUsage, RunResult
from ..models import ChatMessage, converse, extract_multiple_choice, extract_numeric
from ..personas import NEUTRAL_EXPERT
from ..datasets.mmlu_pro import format_mcq_prompt
from ..datasets.math500 import format_math_prompt


# Same heterogeneous panel as V8 (model diversity preserved)
PANEL_V9_INDONLY = {
    "expert-1": "us.anthropic.claude-sonnet-4-6",
    "expert-2": "us.anthropic.claude-sonnet-4-6",
    "expert-3": "us.deepseek.r1-v1:0",
    "expert-4": "us.amazon.nova-pro-v1:0",
    "expert-5": "us.meta.llama3-3-70b-instruct-v1:0",
}


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
    # Same as V8: Always use NEUTRAL_EXPERT, never role-specific personas
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


@register("as_v9_indonly")
class ASV9IndOnlyRunner(Runner):
    """AS-V9-IndOnly: Independent proposals → vote (no deliberation).

    Test of hypothesis H_musr: Deliberation hurts on deterministic logic puzzles.

    Mechanism:
    1. All 5 experts answer independently (parallel, no discussion)
    2. Extract answer from each response
    3. Majority vote
    4. No moderator, no synthesis, no deliberation

    Differences from V8:
    - V8: Fast-track (3) → consensus check → escalate (5) → moderator synthesis
    - V9-IndOnly: All 5 experts → extract → vote (no moderator)

    Differences from sc_k11:
    - sc_k11: Same model, 11 independent samples, majority vote
    - V9-IndOnly: 5 different models, 5 independent samples, majority vote
    - V9-IndOnly preserves model diversity (AS core value) while removing deliberation

    Expected: If H_musr is correct, V9-IndOnly should beat sc_k11 on musr_team_allocation.
    """

    config_id = "as_v9_indonly"
    model = "panel-as-v9-indonly"

    async def run(self, question: Question, seed: int = 42) -> RunResult:
        t0 = time.time()
        user_prompt = _build_prompt(question)
        usage = TokenUsage()
        transcript = [
            Message(role="user", speaker="user", content=user_prompt)
        ]

        # All 5 experts answer independently in parallel
        # NO fast-track, NO escalation, NO moderator
        tasks = [
            _expert_independent(name, user_prompt, PANEL_V9_INDONLY, seed)
            for name in PANEL_V9_INDONLY.keys()
        ]
        results = await asyncio.gather(*tasks)

        # Record all expert responses
        for name, resp, u in results:
            usage += u
            transcript.append(Message(role="assistant", speaker=name, content=resp))

        # Extract answers from all responses
        extracted_answers = []
        for name, resp, _ in results:
            extracted = _extract(question, resp)
            if extracted:
                extracted_answers.append((name, extracted))

        # Majority vote on extracted answers
        if extracted_answers:
            answer_counts = Counter([ans for _, ans in extracted_answers])
            final_answer = answer_counts.most_common(1)[0][0]
            vote_count = answer_counts[final_answer]
        else:
            # No valid extractions (rare)
            final_answer = None
            vote_count = 0

        # Build synthetic final statement for transcript
        # (This is just for record-keeping; no actual LLM call for synthesis)
        vote_summary = ", ".join([
            f"{ans}: {count}" for ans, count in Counter([a for _, a in extracted_answers]).most_common()
        ])
        synthetic_statement = (
            f"Independent vote completed. "
            f"Votes: {vote_summary}. "
            f"Majority answer: {final_answer} ({vote_count}/{len(extracted_answers)} votes)."
        )

        return self._build_result(
            question, seed, transcript, final_answer, usage, t0,
            final_statement=synthetic_statement,
            moderator_turns=0,  # NO moderator in V9-IndOnly
            participant_turns=5,
            metadata={
                "fast_track": False,
                "panel_size": 5,
                "use_personas": False,
                "use_moderator": False,  # KEY: No moderator
                "use_deliberation": False,  # KEY: No deliberation
                "vote_distribution": dict(Counter([a for _, a in extracted_answers])),
                "vote_count": vote_count,
                "total_votes": len(extracted_answers),
            },
        )
