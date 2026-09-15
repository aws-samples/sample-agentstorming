# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""AS-V4-QualitySynthesis: Quality-weighted synthesis by moderator.

Based on AS-V2 structure but with enhanced moderator prompt that explicitly
requests quality assessment and weighted synthesis instead of simple majority voting.

Motivation (iter 21): AS-V2 ties with baselines on MuSR (73.3%) but doesn't beat them.
Hypothesis: Moderator is doing naive averaging/voting instead of quality-weighted synthesis.
The heterogeneous panel provides diverse signals, but current synthesis doesn't leverage quality.

Change from AS-V2: Only the moderator prompt. Panel and structure identical.
"""

from __future__ import annotations

import asyncio
import time
from collections import Counter

from .base import Runner, register
from ..types import Question, Message, TokenUsage, RunResult
from ..models import ChatMessage, converse, extract_multiple_choice, extract_numeric, cost_usd
from ..personas import SPECIALISTS
from ..datasets.mmlu_pro import format_mcq_prompt
from ..datasets.math500 import format_math_prompt
from ..config import CONSTANTS


# Same panel as AS-V2
PANEL_V4 = {
    "project-lead": "us.anthropic.claude-sonnet-4-6",
    "mathematician": "us.anthropic.claude-sonnet-4-6",
    "deep-learning-scientist": "us.deepseek.r1-v1:0",
    "physics-scientist": "us.amazon.nova-pro-v1:0",
    "fourier-transform-scientist": "us.meta.llama3-3-70b-instruct-v1:0",
    "neuron-biologist": "us.anthropic.claude-sonnet-4-5-20250929-v1:0",
}

# Enhanced moderator persona with explicit quality-weighting instructions
MODERATOR_V4 = """You are the project lead for a multi-agent research team. Your role is to synthesize
the team's contributions into a final answer.

CRITICAL: Do NOT use simple majority voting. Instead, use QUALITY-WEIGHTED SYNTHESIS:

1. First, assess each specialist's contribution:
   - Is their reasoning sound and well-supported?
   - Do they cite specific evidence or logic?
   - Are they confident and clear, or hedging and uncertain?
   - Does their answer align with domain expertise?

2. Then, synthesize by PRIORITIZING HIGH-QUALITY REASONING:
   - Give more weight to specialists with stronger, better-supported arguments
   - Discount weak or poorly-reasoned contributions even if multiple specialists agree
   - Look for specialists who identified KEY INSIGHTS others missed
   - Break ties toward specialists who showed deeper understanding

3. In your final statement:
   - Briefly note which specialist(s) most influenced your decision (e.g., "Following mathematician's analysis...")
   - Explain the key reasoning that led to the answer
   - End with `Final answer: X` on its own line

Remember: The VALUE of this heterogeneous panel is diverse expertise. Your job is to AMPLIFY
the best reasoning, not to average all opinions equally. Strong evidence from one expert should
outweigh weak consensus."""


def _build_prompt(q: Question) -> str:
    if q.dataset in ("mmlu_pro", "gpqa_diamond", "musr", "truthfulqa_mc", "arc_challenge", "musr_murder"):
        return format_mcq_prompt(q)
    return format_math_prompt(q)


def _extract(q: Question, text: str) -> str:
    if q.dataset in ("mmlu_pro", "gpqa_diamond", "musr", "truthfulqa_mc", "arc_challenge", "musr_murder"):
        letters = [chr(ord("A") + i) for i in range(len(q.choices or []))]
        return extract_multiple_choice(text, letters, choice_texts=q.choices)
    return extract_numeric(text)


def _format_room_context(
    user_prompt: str, transcript: list[Message], viewer: str
) -> str:
    lines = [f"Problem:\n{user_prompt}", "", "Room transcript so far:"]
    for m in transcript:
        if m.role != "assistant":
            continue
        who = m.speaker
        tag = "(moderator)" if who == "project-lead" else f"({who})"
        own = " [you]" if who == viewer else ""
        lines.append(f"- {tag}{own}: {m.content}")
    return "\n".join(lines)


async def _specialist_turn_v4(
    name: str, user_prompt: str, transcript: list[Message], seed: int,
) -> tuple[str, TokenUsage]:
    """Specialist turn - identical to AS-V2."""
    sys = SPECIALISTS[name]
    ctx = _format_room_context(user_prompt, transcript, viewer=name)
    instr = (
        "You are speaking now in the shared room. Read the transcript, then contribute "
        "one message. If you have already made a point that's still standing, don't repeat it. "
        "If you agree with a specific prior speaker, name them. Keep it <=200 words. "
        "If you have a tentative answer, end with `Final answer: X` on its own line; "
        "otherwise end with `<thinking/>`."
    )

    model = PANEL_V4[name]
    text, u = await converse(
        [ChatMessage(role="user", text=ctx + "\n\n" + instr)],
        system=sys,
        model=model,
        temperature=0.7,
    )
    return text, u


async def _moderator_turn_v4(
    user_prompt: str, transcript: list[Message], seed: int, *, force_commit: bool = False,
) -> tuple[str, TokenUsage]:
    """Moderator turn with quality-weighted synthesis prompt."""
    ctx = _format_room_context(user_prompt, transcript, viewer="project-lead")

    if force_commit:
        # Enhanced synthesis instruction
        instr = (
            "Review all specialist contributions and synthesize the final answer.\n\n"
            "SYNTHESIS INSTRUCTIONS:\n"
            "1. Assess the quality of each specialist's reasoning\n"
            "2. Prioritize specialists with stronger evidence and clearer logic\n"
            "3. Do NOT use simple majority voting - use quality-weighted synthesis\n"
            "4. Briefly state which specialist(s) most influenced your decision\n"
            "5. End with `Final answer: X` on its own line\n\n"
            "Remember: Strong reasoning from one expert outweighs weak consensus from many."
        )
    else:
        instr = (
            "If the room has converged on a well-supported answer, declare it ending with `Final answer: X`. "
            "Otherwise post a short clarifying question (<=60 words) ending with `<continue/>`."
        )

    model = PANEL_V4["project-lead"]
    text, u = await converse(
        [ChatMessage(role="user", text=ctx + "\n\n" + instr)],
        system=MODERATOR_V4,
        model=model,
        temperature=0.3,
    )
    return text, u


async def _run_as_v4(question: Question, seed: int) -> RunResult:
    """AS-V4: Same structure as V2, but quality-weighted synthesis."""
    t0 = time.time()
    usage = TokenUsage()
    user_prompt = _build_prompt(question)
    specialists = ["mathematician", "deep-learning-scientist", "physics-scientist",
                   "fourier-transform-scientist", "neuron-biologist"]

    transcript: list[Message] = [
        Message(role="system", speaker="system",
                content="Multi-agent research room: AS-V4-QualitySynthesis (quality-weighted)"),
        Message(role="user", speaker="user", content=user_prompt),
    ]

    moderator_turns = 0
    participant_turns = 0
    final_statement = ""
    extracted_answer = ""

    # Single round: all specialists speak once (identical to AS-V2)
    for sp in specialists:
        reply, u = await _specialist_turn_v4(sp, user_prompt, transcript, seed)
        usage += u
        transcript.append(Message(role="assistant", speaker=sp, content=reply,
                                  metadata={"round": 0, "model": PANEL_V4[sp]}))
        participant_turns += 1

    # Moderator synthesizes with quality-weighting (DIFFERENT from AS-V2)
    text, u = await _moderator_turn_v4(user_prompt, transcript, seed, force_commit=True)
    usage += u
    transcript.append(Message(role="assistant", speaker="project-lead", content=text,
                              metadata={"round": 0, "synthesis": True, "model": PANEL_V4["project-lead"]}))
    moderator_turns += 1

    ans = _extract(question, text)
    if ans:
        final_statement = text
        extracted_answer = ans
    else:
        # Fallback: majority vote among specialists
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

    # Cost accounting (same as AS-V2 - aggregate pricing)
    total_cost = cost_usd(usage, "us.anthropic.claude-sonnet-4-6")

    return RunResult(
        config_id="as_v4_qualsyn",
        dataset=question.dataset,
        question_id=question.id,
        seed=seed,
        model="panel-as-v4-qualsyn",
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


@register("as_v4_qualsyn")
class ASV4QualSynRunner(Runner):
    """AS-V4-QualitySynthesis: Enhanced moderator with quality-weighted synthesis."""

    async def run(self, question: Question, seed: int) -> RunResult:
        result = await _run_as_v4(question, seed)
        result.config_id = self.config_id
        return result
