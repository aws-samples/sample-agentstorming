# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""AS-V17-Universal: Multi-strategy ensemble that routes questions to optimal sub-protocol.

Design (iter 318):
- Classify each question (type, difficulty, domain) with zero-shot LLM
- Route to optimal strategy: adaptive (AS-V5), ind-first (AS-V14), ind-only (AS-V9), full (AS-V3)
- Select aligned personas based on benchmark + classification
- Execute selected strategy

Hypothesis: By choosing the optimal protocol per question, AS-V17 beats all baselines on ≥4 benchmarks.

Expected performance:
- MMLU-Pro: ~90-91% (mostly factual → adaptive)
- TruthfulQA: ~95-96% (adversarial → ind-first)
- MuSR variants: ~75-80% (reasoning → ind-only)
- XDomain: ~65-70% (cross-domain → full deliberation)
"""

from __future__ import annotations

import asyncio
import json
import time
from collections import Counter
from typing import Literal

from .base import Runner, register
from ..types import Question, Message, TokenUsage, RunResult
from ..models import ChatMessage, converse, extract_multiple_choice, extract_numeric
from ..personas import get_aligned_personas, get_persona_prompt, MODERATOR
from ..datasets.mmlu_pro import format_mcq_prompt
from ..datasets.math500 import format_math_prompt


# Strong heterogeneous panel
STRONG_MODELS = [
    "us.anthropic.claude-sonnet-4-6",
    "us.deepseek.r1-v1:0",
    "us.amazon.nova-pro-v1:0",
    "us.meta.llama3-3-70b-instruct-v1:0",
    "us.anthropic.claude-sonnet-4-5-20250929-v1:0",
]

MODERATOR_MODEL = "us.anthropic.claude-sonnet-4-6"
CLASSIFIER_MODEL = "us.anthropic.claude-sonnet-4-6"


def _build_panel(persona_names: list[str]) -> dict[str, str]:
    """Assign models to personas round-robin for diversity."""
    panel = {}
    for i, name in enumerate(persona_names):
        panel[name] = STRONG_MODELS[i % len(STRONG_MODELS)]
    return panel


def _build_prompt(q: Question) -> str:
    if q.dataset in ("mmlu_pro", "gpqa_diamond", "musr", "truthfulqa_mc", "arc_challenge",
                     "xdomain_v4", "musr_murder", "musr_object_placements", "musr_team_allocation",
                     "xdomain", "xdomain_v2_smoke", "xdomain_v3", "xdomain_v5"):
        return format_mcq_prompt(q)
    return format_math_prompt(q)


def _extract(q: Question, text: str) -> str:
    if q.dataset in ("mmlu_pro", "gpqa_diamond", "musr", "truthfulqa_mc", "arc_challenge",
                     "xdomain_v4", "musr_murder", "musr_object_placements", "musr_team_allocation",
                     "xdomain", "xdomain_v2_smoke", "xdomain_v3", "xdomain_v5"):
        letters = [chr(ord("A") + i) for i in range(len(q.choices or []))]
        return extract_multiple_choice(text, letters, choice_texts=q.choices)
    return extract_numeric(text)


async def _classify_question(question: Question) -> tuple[dict, TokenUsage]:
    """Classify question into type, difficulty, domain using zero-shot LLM."""
    choices_text = ""
    if question.choices:
        choices_text = "\nChoices:\n" + "\n".join(
            f"{chr(ord('A')+i)}. {c}" for i, c in enumerate(question.choices)
        )

    prompt = f"""Classify this question into three dimensions:

1. **Type**: Choose ONE:
   - factual: Tests recall of facts, definitions, or well-established knowledge
   - adversarial: Designed to trick with common misconceptions or false premises
   - reasoning: Requires multi-step logical inference or problem-solving
   - cross_domain: Requires synthesizing concepts from multiple distinct domains

2. **Difficulty**: Choose ONE:
   - easy: Most experts would answer correctly immediately
   - medium: Requires careful thought but solvable by domain experts
   - hard: Even experts may disagree or need extensive reasoning

3. **Domain**: Choose ONE:
   - science: Physics, chemistry, biology, math, computer science
   - humanities: History, philosophy, literature, social sciences
   - mixed: Spans multiple traditional domains
   - novel: Requires cross-domain synthesis or unconventional thinking

Question: {question.question}{choices_text}

Output ONLY valid JSON (no markdown, no explanation):
{{"type": "factual|adversarial|reasoning|cross_domain", "difficulty": "easy|medium|hard", "domain": "science|humanities|mixed|novel"}}
"""

    response, usage = await converse(
        [ChatMessage(role="user", text=prompt)],
        system="You are a question-classification expert. Output ONLY valid JSON.",
        model=CLASSIFIER_MODEL,
        temperature=0.0,
        max_tokens=100,
    )

    # Parse JSON, with fallback
    try:
        # Strip markdown if present
        clean = response.strip()
        if clean.startswith("```"):
            clean = clean.split("```")[1]
            if clean.startswith("json"):
                clean = clean[4:]
        clean = clean.strip()

        classification = json.loads(clean)
        # Validate fields
        assert classification["type"] in ["factual", "adversarial", "reasoning", "cross_domain"]
        assert classification["difficulty"] in ["easy", "medium", "hard"]
        assert classification["domain"] in ["science", "humanities", "mixed", "novel"]
    except (json.JSONDecodeError, KeyError, AssertionError):
        # Fallback: default classification
        classification = {"type": "reasoning", "difficulty": "medium", "domain": "mixed"}

    return classification, usage


def _select_strategy(
    classification: dict
) -> Literal["adaptive", "ind_first", "ind_only", "full_deliberation"]:
    """Route to optimal sub-protocol based on classification."""
    qtype = classification["type"]
    difficulty = classification["difficulty"]

    if qtype == "factual" and difficulty == "easy":
        return "adaptive"  # AS-V5 fast-track
    elif qtype == "adversarial":
        return "ind_first"  # AS-V14 avoid groupthink
    elif qtype == "reasoning" and difficulty == "hard":
        return "ind_only"  # AS-V9 no deliberation overhead
    elif qtype == "cross_domain":
        return "full_deliberation"  # AS-V3 multi-turn synthesis
    else:
        return "adaptive"  # default fallback


def _select_personas(question: Question, classification: dict) -> list[str]:
    """Select aligned personas based on benchmark + classification fallback."""
    # First try benchmark-specific alignment
    benchmark_personas = get_aligned_personas(question.dataset)
    if benchmark_personas:
        return benchmark_personas

    # Fallback to classification-based selection
    domain = classification["domain"]
    if domain == "science":
        return ["mathematician", "physics-scientist", "chemist",
                "computer-scientist", "deep-learning-scientist"]
    elif domain == "humanities":
        return ["economist", "detective", "epistemologist",
                "criminal-psychologist", "forensic-pathologist"]
    elif domain == "mixed":
        return ["mathematician", "economist", "computer-scientist",
                "epistemologist", "detective"]
    else:  # novel
        return ["mathematician", "deep-learning-scientist", "physics-scientist",
                "computer-scientist", "economist"]


async def _specialist_independent(
    name: str, user_prompt: str, panel: dict, seed: int
) -> tuple[str, str, TokenUsage]:
    """Specialist answers independently."""
    sys = get_persona_prompt(name)
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


async def _moderator_synthesis(
    user_prompt: str,
    responses: list[tuple[str, str]],
    quality_weighted: bool,
    seed: int
) -> tuple[str, TokenUsage]:
    """Moderator synthesizes specialist answers."""
    context = "\n\n".join([
        f"**{name}:** {resp}" for name, resp in responses
    ])

    if quality_weighted:
        instruction = """Your task: Synthesize their answers into a final answer.
These specialists may disagree, so carefully assess:
- Which reasoning is most sound?
- Which cites the strongest evidence?
- Which aligns best with domain expertise?

Prioritize high-quality reasoning over simple voting.
End with `Final answer: X` on its own line."""
    else:
        instruction = """Your task: Synthesize their answers and provide the final answer.
End with `Final answer: X` on its own line."""

    mod_prompt = f"""The following specialists have answered this question:

{context}

{instruction}
"""

    text, u = await converse(
        [ChatMessage(role="user", text=f"{user_prompt}\n\n{mod_prompt}")],
        system=MODERATOR,
        model=MODERATOR_MODEL,
        temperature=0.3,
    )
    return text, u


async def _execute_adaptive(
    question: Question, personas: list[str], panel: dict, seed: int
) -> tuple[list[Message], str, TokenUsage]:
    """AS-V5 adaptive: fast-track 3, escalate to 5 if disagreement."""
    user_prompt = _build_prompt(question)
    usage = TokenUsage()
    transcript = [Message(role="user", speaker="user", content=user_prompt)]

    # Phase 1: Fast-track (3 specialists)
    fast_personas = personas[:3]
    tasks = [
        _specialist_independent(name, user_prompt, panel, seed)
        for name in fast_personas
    ]
    results = await asyncio.gather(*tasks)

    for name, resp, u in results:
        usage += u
        transcript.append(Message(role="assistant", speaker=name, content=resp))

    # Check consensus
    fast_answers = [_extract(question, resp) for _, resp, _ in results if _extract(question, resp)]

    if len(fast_answers) >= 2:
        counts = Counter(fast_answers)
        most_common = counts.most_common(1)[0]
        if most_common[1] >= 2:
            # Consensus! Simple synthesis
            mod_resp, mod_u = await _moderator_synthesis(
                user_prompt,
                [(name, resp) for name, resp, _ in results],
                quality_weighted=False,
                seed=seed
            )
            usage += mod_u
            transcript.append(Message(role="assistant", speaker="moderator", content=mod_resp))
            final_answer = _extract(question, mod_resp)
            return transcript, final_answer, usage

    # Phase 2: Escalate to full panel (5)
    additional_personas = [p for p in personas[:5] if p not in fast_personas]
    additional_tasks = [
        _specialist_independent(name, user_prompt, panel, seed)
        for name in additional_personas
    ]
    additional_results = await asyncio.gather(*additional_tasks)

    for name, resp, u in additional_results:
        usage += u
        transcript.append(Message(role="assistant", speaker=name, content=resp))

    all_responses = [(name, resp) for name, resp, _ in results + additional_results]
    mod_resp, mod_u = await _moderator_synthesis(
        user_prompt, all_responses, quality_weighted=True, seed=seed
    )
    usage += mod_u
    transcript.append(Message(role="assistant", speaker="moderator", content=mod_resp))
    final_answer = _extract(question, mod_resp)
    return transcript, final_answer, usage


async def _execute_ind_first(
    question: Question, personas: list[str], panel: dict, seed: int
) -> tuple[list[Message], str, TokenUsage]:
    """AS-V14 ind-first: all specialists answer independently, moderator synthesizes."""
    user_prompt = _build_prompt(question)
    usage = TokenUsage()
    transcript = [Message(role="user", speaker="user", content=user_prompt)]

    # All specialists answer independently
    specialists = personas[:5]
    tasks = [
        _specialist_independent(name, user_prompt, panel, seed)
        for name in specialists
    ]
    results = await asyncio.gather(*tasks)

    for name, resp, u in results:
        usage += u
        transcript.append(Message(role="assistant", speaker=name, content=resp))

    # Moderator synthesis (quality-weighted for ind-first)
    mod_resp, mod_u = await _moderator_synthesis(
        user_prompt,
        [(name, resp) for name, resp, _ in results],
        quality_weighted=True,
        seed=seed
    )
    usage += mod_u
    transcript.append(Message(role="assistant", speaker="moderator", content=mod_resp))
    final_answer = _extract(question, mod_resp)
    return transcript, final_answer, usage


async def _execute_ind_only(
    question: Question, personas: list[str], panel: dict, seed: int
) -> tuple[list[Message], str, TokenUsage]:
    """AS-V9 ind-only: specialists answer independently, simple voting (no deliberation)."""
    user_prompt = _build_prompt(question)
    usage = TokenUsage()
    transcript = [Message(role="user", speaker="user", content=user_prompt)]

    specialists = personas[:5]
    tasks = [
        _specialist_independent(name, user_prompt, panel, seed)
        for name in specialists
    ]
    results = await asyncio.gather(*tasks)

    for name, resp, u in results:
        usage += u
        transcript.append(Message(role="assistant", speaker=name, content=resp))

    # Simple voting
    answers = [_extract(question, resp) for _, resp, _ in results if _extract(question, resp)]
    if answers:
        counts = Counter(answers)
        final_answer = counts.most_common(1)[0][0]
    else:
        final_answer = ""

    # No moderator synthesis for ind-only (pure voting)
    return transcript, final_answer, usage


async def _execute_full_deliberation(
    question: Question, personas: list[str], panel: dict, seed: int
) -> tuple[list[Message], str, TokenUsage]:
    """AS-V3 full deliberation: independent → deliberation → synthesis.

    Simplified multi-turn: 2 rounds of independent, then moderator synthesis.
    """
    user_prompt = _build_prompt(question)
    usage = TokenUsage()
    transcript = [Message(role="user", speaker="user", content=user_prompt)]

    specialists = personas[:5]

    # Round 1: Independent answers
    tasks = [
        _specialist_independent(name, user_prompt, panel, seed)
        for name in specialists
    ]
    results_r1 = await asyncio.gather(*tasks)

    for name, resp, u in results_r1:
        usage += u
        transcript.append(Message(role="assistant", speaker=name, content=resp))

    # Round 2: React to others' answers (simplified deliberation)
    context_r1 = "\n\n".join([f"**{name}:** {resp}" for name, resp, _ in results_r1])

    async def _deliberation_turn(name: str) -> tuple[str, str, TokenUsage]:
        sys = get_persona_prompt(name)
        instr = f"""Other specialists have provided these answers:

{context_r1}

React to their reasoning. Do you agree or disagree? Why?
Provide your final answer after considering their input.
End with `Final answer: X` on its own line.
Keep response ≤200 words.
"""
        model = panel[name]
        text, u = await converse(
            [ChatMessage(role="user", text=f"{user_prompt}\n\n{instr}")],
            system=sys,
            model=model,
            temperature=0.7,
        )
        return name, text, u

    tasks_r2 = [_deliberation_turn(name) for name in specialists]
    results_r2 = await asyncio.gather(*tasks_r2)

    for name, resp, u in results_r2:
        usage += u
        transcript.append(Message(role="assistant", speaker=name, content=resp))

    # Moderator synthesis (quality-weighted for deliberation)
    all_responses = [
        (name, resp) for name, resp, _ in results_r1 + results_r2
    ]
    mod_resp, mod_u = await _moderator_synthesis(
        user_prompt, all_responses, quality_weighted=True, seed=seed
    )
    usage += mod_u
    transcript.append(Message(role="assistant", speaker="moderator", content=mod_resp))
    final_answer = _extract(question, mod_resp)
    return transcript, final_answer, usage


@register("as_v17_universal")
class ASV17UniversalRunner(Runner):
    """AS-V17: Universal multi-strategy ensemble with question-driven routing."""

    config_id = "as_v17_universal"
    model = "panel-as-v17-universal"

    async def run(self, question: Question, seed: int = 42) -> RunResult:
        t0 = time.time()
        usage = TokenUsage()

        # Step 1: Classify question
        classification, classify_usage = await _classify_question(question)
        usage += classify_usage

        # Step 2: Select strategy
        strategy = _select_strategy(classification)

        # Step 3: Select personas
        personas = _select_personas(question, classification)
        panel = _build_panel(personas)

        # Step 4: Execute selected strategy
        if strategy == "adaptive":
            transcript, final_answer, exec_usage = await _execute_adaptive(
                question, personas, panel, seed
            )
        elif strategy == "ind_first":
            transcript, final_answer, exec_usage = await _execute_ind_first(
                question, personas, panel, seed
            )
        elif strategy == "ind_only":
            transcript, final_answer, exec_usage = await _execute_ind_only(
                question, personas, panel, seed
            )
        else:  # full_deliberation
            transcript, final_answer, exec_usage = await _execute_full_deliberation(
                question, personas, panel, seed
            )

        usage += exec_usage

        return self._build_result(
            question, seed, transcript, final_answer, usage, t0,
            final_statement=transcript[-1].content if transcript else "",
            moderator_turns=1 if strategy in ["adaptive", "ind_first", "full_deliberation"] else 0,
            participant_turns=len([m for m in transcript if m.speaker != "user" and m.speaker != "moderator"]),
            metadata={
                "classification": classification,
                "strategy": strategy,
                "personas": personas,
                "panel_size": len(personas),
                "benchmark": question.dataset,
            },
        )
