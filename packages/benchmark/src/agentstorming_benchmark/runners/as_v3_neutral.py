# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""AS-V3-Neutral: Persona ablation variant of AS-V3-IndependentFirst.

This is a controlled ablation experiment to test whether persona diversity
contributes to AS performance beyond model diversity alone.

SAME as AS-V3-IndependentFirst:
- Model panel composition (heterogeneous models)
- Independent-first logic (parallel answers → synthesis → conditional discussion)
- All hyperparameters and decision thresholds

DIFFERENT:
- ALL specialists use NEUTRAL_EXPERT system prompt instead of role-specific
  personas (mathematician, physicist, etc.)

Purpose: If AS-V3-Neutral ≈ AS-V3-IndependentFirst, then personas don't matter
and we can simplify. If AS-V3-Neutral < AS-V3-IndependentFirst, then personas
provide measurable gain through diverse reasoning lenses.

Result from iter 124: Personas provide ZERO accuracy gain (p=0.50, Δ=0.7%).
This neutral variant becomes the new default.

See: /opt/agentstorming/driver/decisions/20260517-1342-iter123-persona-ablation.md
"""

from __future__ import annotations

import asyncio
import time
from collections import Counter
import re

from .base import Runner, register
from ..types import Question, Message, TokenUsage, RunResult
from ..models import ChatMessage, converse, extract_multiple_choice, extract_numeric, cost_usd
from ..personas import NEUTRAL_EXPERT, MODERATOR
from ..datasets.mmlu_pro import format_mcq_prompt
from ..datasets.math500 import format_math_prompt

# Same heterogeneous panel as AS-V3-IndependentFirst.
PANEL_V3_NEUTRAL = {
    "project-lead": "us.anthropic.claude-sonnet-4-6",
    "mathematician": "us.anthropic.claude-sonnet-4-6",
    "deep-learning-scientist": "us.deepseek.r1-v1:0",
    "physics-scientist": "us.amazon.nova-pro-v1:0",
    "fourier-transform-scientist": "us.meta.llama3-3-70b-instruct-v1:0",
    "neuron-biologist": "us.anthropic.claude-sonnet-4-5-20250929-v1:0",
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


async def _specialist_independent(
    name: str, user_prompt: str, seed: int
) -> tuple[str, TokenUsage]:
    """Specialist answers INDEPENDENTLY without seeing others' messages.

    KEY DIFFERENCE: Uses NEUTRAL_EXPERT instead of SPECIALISTS[name].
    """
    sys = NEUTRAL_EXPERT  # ← Changed from SPECIALISTS[name]
    instr = (
        "You are an expert being consulted on the following problem. "
        "Provide your best answer independently. "
        "Think step-by-step if helpful. "
        "End your response with `Final answer: X` on its own line, where X is your answer. "
        "Keep your response ≤300 words."
    )

    model = PANEL_V3_NEUTRAL[name]
    text, u = await converse(
        [ChatMessage(role="user", text=f"{user_prompt}\n\n{instr}")],
        system=sys,
        model=model,
        temperature=0.7,
    )
    return text, u


async def _moderator_synthesis(
    user_prompt: str, independent_answers: list[tuple[str, str]], seed: int
) -> tuple[str, TokenUsage, bool]:
    """Moderator synthesizes independent answers and decides if discussion is needed.

    Returns:
        (moderator_text, usage, needs_discussion)
    """
    # Format independent answers for moderator.
    lines = [f"Problem:\n{user_prompt}", "", "Expert opinions (provided independently):"]
    for sp_name, answer_text in independent_answers:
        lines.append(f"\n**{sp_name}:**\n{answer_text}")

    context = "\n".join(lines)

    instr = (
        "You are the moderator synthesizing independent expert opinions. "
        "Review all opinions carefully. "
        "\n\n"
        "If there is strong consensus (≥4/5 experts agree on the same answer), "
        "write a brief synthesis and end with `Final answer: X` where X is the consensus answer. "
        "\n\n"
        "If there is significant disagreement or uncertainty across experts, "
        "write a brief summary of the disagreement and end with `<needs-discussion/>` "
        "to trigger a collaborative discussion round."
        "\n\n"
        "Keep your response ≤200 words."
    )

    model = PANEL_V3_NEUTRAL["project-lead"]
    text, u = await converse(
        [ChatMessage(role="user", text=f"{context}\n\n{instr}")],
        system=MODERATOR,
        model=model,
        temperature=0.3,
    )

    needs_discussion = "<needs-discussion/>" in text.lower() or "<needs-discussion>" in text.lower()

    return text, u, needs_discussion


def _format_room_context(
    user_prompt: str, independent_phase: list[tuple[str, str]],
    discussion_transcript: list[Message], viewer: str
) -> str:
    """Format context for discussion phase."""
    lines = [f"Problem:\n{user_prompt}", "", "Phase 1 (independent opinions):"]
    for sp_name, answer_text in independent_phase:
        own = " [you]" if sp_name == viewer else ""
        lines.append(f"\n**{sp_name}**{own}:\n{answer_text[:200]}...")

    lines.append("\n\nPhase 2 (discussion):")
    for m in discussion_transcript:
        if m.role == "assistant":
            who = m.speaker
            tag = "(moderator)" if who == "project-lead" else ""
            own = " [you]" if who == viewer else ""
            lines.append(f"- {who}{tag}{own}: {m.content}")

    return "\n".join(lines)


async def _specialist_discussion(
    name: str, user_prompt: str, independent_phase: list[tuple[str, str]],
    discussion_transcript: list[Message], seed: int
) -> tuple[str, TokenUsage]:
    """Specialist participates in discussion phase (after seeing disagreement).

    KEY DIFFERENCE: Uses NEUTRAL_EXPERT instead of SPECIALISTS[name].
    """
    sys = NEUTRAL_EXPERT  # ← Changed from SPECIALISTS[name]
    ctx = _format_room_context(user_prompt, independent_phase, discussion_transcript, viewer=name)
    instr = (
        "You initially provided an independent opinion. Now there is disagreement, "
        "so a discussion has begun. Review the other experts' opinions and the discussion so far. "
        "Either defend your position with additional reasoning, or update your view if convinced. "
        "End with `Final answer: X` if you have a definite answer, or `<thinking/>` if still uncertain. "
        "Keep it ≤200 words."
    )

    model = PANEL_V3_NEUTRAL[name]
    text, u = await converse(
        [ChatMessage(role="user", text=f"{ctx}\n\n{instr}")],
        system=sys,
        model=model,
        temperature=0.7,
    )
    return text, u


async def _moderator_final_synthesis(
    user_prompt: str, independent_phase: list[tuple[str, str]],
    discussion_transcript: list[Message], seed: int
) -> tuple[str, TokenUsage]:
    """Moderator makes final synthesis after discussion."""
    ctx = _format_room_context(user_prompt, independent_phase, discussion_transcript, viewer="project-lead")
    instr = (
        "The discussion has concluded. Synthesize the best-supported answer "
        "considering both the independent phase and the discussion. "
        "End with `Final answer: X` on its own line. "
        "Keep it ≤150 words."
    )

    model = PANEL_V3_NEUTRAL["project-lead"]
    text, u = await converse(
        [ChatMessage(role="user", text=f"{ctx}\n\n{instr}")],
        system=MODERATOR,
        model=model,
        temperature=0.3,
    )
    return text, u


async def _run_as_v3_neutral(question: Question, seed: int) -> RunResult:
    """AS-V3 Neutral: independent parallel -> synthesis -> conditional discussion with neutral personas."""
    t0 = time.time()
    usage = TokenUsage()
    user_prompt = _build_prompt(question)
    specialists = ["mathematician", "deep-learning-scientist", "physics-scientist",
                   "fourier-transform-scientist", "neuron-biologist"]

    transcript: list[Message] = [
        Message(role="system", speaker="system",
                content="AS-V3-Neutral: independent parallel answers with neutral personas, conditional discussion"),
        Message(role="user", speaker="user", content=user_prompt),
    ]

    moderator_turns = 0
    participant_turns = 0

    # Phase 1: Independent parallel answers.
    tasks = [_specialist_independent(sp, user_prompt, seed) for sp in specialists]
    results = await asyncio.gather(*tasks)

    independent_answers = []
    for sp, (text, u) in zip(specialists, results):
        usage += u
        independent_answers.append((sp, text))
        transcript.append(Message(
            role="assistant", speaker=sp, content=text,
            metadata={"phase": "independent", "model": PANEL_V3_NEUTRAL[sp], "persona": "neutral"}
        ))
        participant_turns += 1

    # Phase 2: Moderator synthesis (checks for consensus or triggers discussion).
    mod_text, u, needs_discussion = await _moderator_synthesis(user_prompt, independent_answers, seed)
    usage += u
    transcript.append(Message(
        role="assistant", speaker="project-lead", content=mod_text,
        metadata={"phase": "synthesis", "needs_discussion": needs_discussion, "model": PANEL_V3_NEUTRAL["project-lead"]}
    ))
    moderator_turns += 1

    final_statement = mod_text
    extracted_answer = _extract(question, mod_text)

    # Phase 3: Conditional discussion if needed.
    if needs_discussion and not extracted_answer:
        discussion_transcript: list[Message] = []

        # All specialists discuss.
        for sp in specialists:
            text, u = await _specialist_discussion(sp, user_prompt, independent_answers, discussion_transcript, seed)
            usage += u
            discussion_transcript.append(Message(
                role="assistant", speaker=sp, content=text,
                metadata={"phase": "discussion", "model": PANEL_V3_NEUTRAL[sp], "persona": "neutral"}
            ))
            transcript.append(discussion_transcript[-1])
            participant_turns += 1

        # Moderator final synthesis.
        text, u = await _moderator_final_synthesis(user_prompt, independent_answers, discussion_transcript, seed)
        usage += u
        transcript.append(Message(
            role="assistant", speaker="project-lead", content=text,
            metadata={"phase": "final_synthesis", "model": PANEL_V3_NEUTRAL["project-lead"]}
        ))
        moderator_turns += 1

        final_statement = text
        extracted_answer = _extract(question, text)

    # Fallback: majority vote from independent answers if no extracted answer.
    if not extracted_answer:
        votes = []
        for sp, text in independent_answers:
            a = _extract(question, text)
            if a:
                votes.append(a)
        extracted_answer = Counter(votes).most_common(1)[0][0] if votes else ""
        final_statement = f"(fallback majority vote: {extracted_answer})"

    from .base import answer_matches
    is_correct = answer_matches(extracted_answer, question.answer_key, question.dataset)

    # Cost approximation (use aggregate).
    total_cost = cost_usd(usage, "us.anthropic.claude-sonnet-4-6")

    return RunResult(
        config_id="as_v3_neutral",
        dataset=question.dataset,
        question_id=question.id,
        seed=seed,
        model="panel-as-v3-neutral",
        transcript=transcript,
        extracted_answer=extracted_answer,
        is_correct=is_correct,
        usage=usage,
        cost_usd=total_cost,
        latency_sec=round(time.time() - t0, 3),
        final_statement=final_statement,
        moderator_turns=moderator_turns,
        participant_turns=participant_turns,
        metadata={"persona_mode": "neutral"},
    )


@register("as_v3_neutral")
class ASV3NeutralRunner(Runner):
    """AS-V3 Neutral: independent parallel -> conditional discussion with neutral personas."""

    config_id = "as_v3_neutral"
    model = "panel-as-v3-neutral"

    async def run(self, question: Question, seed: int) -> RunResult:
        result = await _run_as_v3_neutral(question, seed)
        result.config_id = self.config_id
        return result
