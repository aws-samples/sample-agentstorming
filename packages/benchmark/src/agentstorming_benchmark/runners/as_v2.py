# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""AS-V2: Agent Storming with heterogeneous panel + reduced rounds.

Addresses failure modes found in MMLU-Pro pilot analysis (iter 9):
- Over-deliberation: reduce from 2 rounds to 1 round
- Lack of diversity: use heterogeneous strong panel (diverse model backends)
- Groupthink: 1 round reduces sequential contamination

This is the first test of the CORE hypothesis: model diversity helps.
"""

from __future__ import annotations

import asyncio
import time
from collections import Counter

from .base import Runner, register
from ..types import Question, Message, TokenUsage, RunResult
from ..models import ChatMessage, converse, extract_multiple_choice, extract_numeric, cost_usd
from ..personas import SPECIALISTS, MODERATOR
from ..datasets.mmlu_pro import format_mcq_prompt
from ..datasets.math500 import format_math_prompt
from ..config import CONSTANTS


# Heterogeneous strong panel: diverse vendors, all accessible models.
# Tested 2026-05-16: all models confirmed working in us-east-1.
PANEL_V2 = {
    "project-lead": "us.anthropic.claude-sonnet-4-6",           # moderator: strong & reliable
    "mathematician": "us.anthropic.claude-sonnet-4-6",         # strongest available
    "deep-learning-scientist": "us.deepseek.r1-v1:0",           # reasoning specialist
    "physics-scientist": "us.amazon.nova-pro-v1:0",             # different vendor (Amazon)
    "fourier-transform-scientist": "us.meta.llama3-3-70b-instruct-v1:0",  # Meta
    "neuron-biologist": "us.anthropic.claude-sonnet-4-5-20250929-v1:0",   # Anthropic diversity
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


def _format_room_context(
    user_prompt: str, transcript: list[Message], viewer: str
) -> str:
    lines = [f"Problem:\\n{user_prompt}", "", "Room transcript so far:"]
    for m in transcript:
        if m.role != "assistant":
            continue
        who = m.speaker
        tag = "(moderator)" if who == "project-lead" else f"({who})"
        own = " [you]" if who == viewer else ""
        lines.append(f"- {tag}{own}: {m.content}")
    return "\n".join(lines)


async def _specialist_turn_v2(
    name: str, user_prompt: str, transcript: list[Message], seed: int,
) -> tuple[str, TokenUsage]:
    """Specialist turn using persona's assigned model from PANEL_V2."""
    sys = SPECIALISTS[name]
    ctx = _format_room_context(user_prompt, transcript, viewer=name)
    instr = (
        "You are speaking now in the shared room. Read the transcript, then contribute "
        "one message. If you have already made a point that's still standing, don't repeat it. "
        "If you agree with a specific prior speaker, name them. Keep it <=200 words. "
        "If you have a tentative answer, end with `Final answer: X` on its own line; "
        "otherwise end with `<thinking/>`."
    )

    model = PANEL_V2[name]
    text, u = await converse(
        [ChatMessage(role="user", text=ctx + "\n\n" + instr)],
        system=sys,
        model=model,
        temperature=0.7,
    )
    return text, u


async def _moderator_turn_v2(
    user_prompt: str, transcript: list[Message], seed: int, *, force_commit: bool = False,
) -> tuple[str, TokenUsage]:
    ctx = _format_room_context(user_prompt, transcript, viewer="project-lead")
    if force_commit:
        instr = (
            "The discussion has gone on long enough. Commit to the best-supported answer "
            "given the room's contributions. End with `Final answer: X` on its own line."
        )
    else:
        instr = (
            "If the room has converged, declare a final answer ending with `Final answer: X`. "
            "Otherwise post a short clarifying question (<=60 words) ending with `<continue/>`."
        )

    model = PANEL_V2["project-lead"]
    text, u = await converse(
        [ChatMessage(role="user", text=ctx + "\n\n" + instr)],
        system=MODERATOR,
        model=model,
        temperature=0.3,
    )
    return text, u


async def _run_as_v2(question: Question, seed: int) -> RunResult:
    """AS-V2: 1 round (5 specialists) + moderator synthesis."""
    t0 = time.time()
    usage = TokenUsage()
    user_prompt = _build_prompt(question)
    specialists = ["mathematician", "deep-learning-scientist", "physics-scientist",
                   "fourier-transform-scientist", "neuron-biologist"]

    transcript: list[Message] = [
        Message(role="system", speaker="system",
                content="Multi-agent research room: AS-V2 (heterogeneous panel, 1 round)"),
        Message(role="user", speaker="user", content=user_prompt),
    ]

    moderator_turns = 0
    participant_turns = 0
    final_statement = ""
    extracted_answer = ""

    # Single round: all specialists speak once.
    for sp in specialists:
        reply, u = await _specialist_turn_v2(sp, user_prompt, transcript, seed)
        usage += u
        transcript.append(Message(role="assistant", speaker=sp, content=reply,
                                  metadata={"round": 0, "model": PANEL_V2[sp]}))
        participant_turns += 1

    # Moderator synthesizes (forced commit since this is the only round).
    text, u = await _moderator_turn_v2(user_prompt, transcript, seed, force_commit=True)
    usage += u
    transcript.append(Message(role="assistant", speaker="project-lead", content=text,
                              metadata={"round": 0, "synthesis": True, "model": PANEL_V2["project-lead"]}))
    moderator_turns += 1

    ans = _extract(question, text)
    if ans:
        final_statement = text
        extracted_answer = ans
    else:
        # Fallback: majority vote among specialists.
        votes = []
        for m in transcript:
            if m.role == "assistant" and m.speaker in specialists:
                a = _extract(question, m.content)
                if a:
                    votes.append(a)
        extracted_answer = Counter(votes).most_common(1)[0][0] if votes else ""
        final_statement = f"(fallback majority vote: {extracted_answer})"

    from .base import answer_matches

    is_correct = answer_matches(extracted_answer, question.answer_key, question.dataset)

    # Cost accounting for heterogeneous panel: sum cost per message.
    total_cost = 0.0
    for m in transcript:
        if m.role == "assistant" and "model" in (m.metadata or {}):
            model_id = m.metadata["model"]
            # Extract usage from metadata if stored, else approximate from content length.
            # For now, use aggregate usage / num_turns as rough estimate.
            # TODO: track per-turn usage in metadata.
            pass
    # Simple fallback: use aggregate usage with PRIMARY_MODEL pricing.
    # This is an approximation; v3.1 should track per-call usage.
    total_cost = sum(cost_usd(usage, PANEL_V2.get(m.speaker, "us.anthropic.claude-sonnet-4-6"))
                     for m in transcript if m.role == "assistant" and m.speaker != "system")
    # Better: distribute the aggregate usage proportionally. For now, use aggregate.
    total_cost = cost_usd(usage, "us.anthropic.claude-sonnet-4-6")

    return RunResult(
        config_id="as_v2",
        dataset=question.dataset,
        question_id=question.id,
        seed=seed,
        model="panel-as-v2-heterogeneous",
        transcript=transcript,
        extracted_answer=extracted_answer,
        is_correct=is_correct,
        usage=usage,
        cost_usd=total_cost,
        latency_sec=round(time.time() - t0, 3),
        final_statement=final_statement,
        moderator_turns=moderator_turns,
        participant_turns=participant_turns,
    )


@register("as_v2")
class ASV2Runner(Runner):
    """AS-V2: heterogeneous panel + 1 round + moderator."""

    async def run(self, question: Question, seed: int) -> RunResult:
        result = await _run_as_v2(question, seed)
        result.config_id = self.config_id
        return result
