# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Multi-persona single-model baseline.

One model is prompted with multiple specialist personas in sequence, simulating
an internal "discussion" by stitching all responses into a single context window.
Then the model synthesizes a final answer from all perspectives.

This tests whether persona prompting alone (without true multi-agent parallelism
or model diversity) captures some of Agent Storming's advantage.
"""

from __future__ import annotations

import time

from .base import Runner, register
from ..types import Question, Message, TokenUsage, RunResult
from ..models import ChatMessage, converse, extract_multiple_choice, extract_numeric
from ..personas import SPECIALISTS, ORCHESTRATOR


def build_user_prompt(q: Question) -> str:
    """Format question as MCQ or math prompt."""
    if q.dataset in ("mmlu_pro", "gpqa_diamond", "musr", "musr_murder", "truthfulqa_mc", "arc_challenge", "xdomain_v4", "musr_object_placements", "musr_team_allocation"):
        from ..datasets.mmlu_pro import format_mcq_prompt
        return format_mcq_prompt(q)
    if q.dataset in ("math500", "dabstep_hard"):
        from ..datasets.math500 import format_math_prompt
        return format_math_prompt(q)
    return f"Question: {q.question}\n\nProvide your answer."


def extract_answer(q: Question, text: str) -> str:
    """Extract answer from model response."""
    if q.dataset in ("mmlu_pro", "gpqa_diamond", "musr", "musr_murder", "truthfulqa_mc", "arc_challenge", "xdomain_v4", "musr_object_placements", "musr_team_allocation"):
        letters = [chr(ord("A") + i) for i in range(len(q.choices or []))]
        return extract_multiple_choice(text, letters, choice_texts=q.choices)
    return extract_numeric(text)


@register("multi_persona_single")
class MultiPersonaSingleModelRunner(Runner):
    """One model, multiple personas, internal discussion, then synthesis."""

    async def run(self, question: Question, seed: int) -> RunResult:
        t0 = time.time()
        usage = TokenUsage()
        user_prompt = build_user_prompt(question)
        transcript = [
            Message(role="system", speaker="system", content="Multi-persona internal discussion"),
            Message(role="user", speaker="user", content=user_prompt),
        ]

        # Use 5 personas in sequence (smaller subset for cost control).
        personas_to_use = [
            ("mathematician", SPECIALISTS["mathematician"]),
            ("physicist", SPECIALISTS["physics-scientist"]),
            ("chemist", SPECIALISTS["chemist"]),
            ("computer-scientist", SPECIALISTS["computer-scientist"]),
            ("deep-learning-scientist", SPECIALISTS["deep-learning-scientist"]),
        ]

        discussion_history = []

        # Round 1: Each persona generates initial perspective.
        for persona_name, persona_prompt in personas_to_use:
            # Build context: show question + prior contributions.
            context_lines = [f"Question: {user_prompt}", ""]
            if discussion_history:
                context_lines.append("Previous contributions from other perspectives:")
                for prev_name, prev_text in discussion_history:
                    context_lines.append(f"\n[{prev_name}]: {prev_text}")
                context_lines.append("")

            context_lines.append(f"Now, as {persona_name}, provide your perspective:")
            full_prompt = "\n".join(context_lines)

            text, u = await converse(
                [ChatMessage(role="user", text=full_prompt)],
                system=persona_prompt,
                temperature=0.7,
            )
            usage += u
            discussion_history.append((persona_name, text))
            transcript.append(Message(role="assistant", speaker=persona_name, content=text))

        # Synthesis phase: orchestrator synthesizes all perspectives.
        synthesis_prompt = f"Question: {user_prompt}\n\nPerspectives from specialists:\n"
        for persona_name, text in discussion_history:
            synthesis_prompt += f"\n[{persona_name}]: {text}\n"
        synthesis_prompt += "\nSynthesize these perspectives and provide the final answer."

        final_text, u = await converse(
            [ChatMessage(role="user", text=synthesis_prompt)],
            system=ORCHESTRATOR,
            temperature=0.3,
        )
        usage += u
        transcript.append(Message(role="assistant", speaker="orchestrator", content=final_text))

        final_answer = extract_answer(question, final_text)

        return self._build_result(
            question,
            seed,
            transcript,
            final_answer,
            usage,
            t0,
            final_statement=final_text,
            participant_turns=len(personas_to_use) + 1,
        )
