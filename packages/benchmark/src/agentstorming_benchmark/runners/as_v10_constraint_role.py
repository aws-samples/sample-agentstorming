# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""AS-V10-ConstraintRole: Constraint-based multi-agent reasoning with functional roles.

Design motivation (iter 186 diagnosis):
  Failure Mode #1: AS misses multi-constraint optimization problems (e.g., MuSR team
  allocation) because current protocol is conversation-based, not constraint-based.
  Agents make holistic judgments without explicit constraint tracking.

  Failure Mode #2: No evidence-forcing mechanism. Agents assert without citing text.

  Failure Mode #3: Voting tyranny / groupthink. Early assertions dominate.

  Failure Mode #4: Domain personas (physicist, chemist) contribute zero (iters 172-175).

Solution: 3-phase protocol + functional roles.

## Phase 1: Constraint Extraction (independent)
All agents independently list constraints/requirements from problem.
Decomposer synthesizes into master constraint list.

## Phase 2: Systematic Evaluation
For each answer option, Evaluator checks against each constraint.
Devil's Advocate challenges assertions and looks for violations.
Track which constraints are satisfied/violated per option.

## Phase 3: Vote & Synthesis
Agents vote based on constraint satisfaction scores.
Synthesizer weighs votes + reasoning quality, provides final answer.

## Functional Roles (not domain personas)
- **decomposer**: Identifies all constraints, requirements, edge cases
- **evaluator**: Systematically checks each option against constraints
- **devils-advocate**: Challenges consensus, finds constraint violations
- **synthesizer**: Aggregates votes + reasoning, commits to final answer

Hypothesis: Explicit constraint tracking eliminates "missed constraint" failures
like Question 149 (MuSR team allocation where AS missed interpersonal constraints).

Expected pilot (iter 188): N=50 on musr_team_allocation, compare to sc_k11 (82.8%)
and as_v5_adaptive (81.6%). Target: AS-V10 margin over sc_k11 > 5 pp.

Cost profile:
- Phase 1: 4 agents extract constraints = 4 calls
- Phase 2: Evaluator + Devil's Advocate check options = 2 calls
- Phase 3: Vote + synthesis = 5 calls
- Total: ~11 calls/question, ~$0.077/q (similar to as_v5_adaptive full panel)
"""

from __future__ import annotations

import asyncio
import time
from collections import Counter

from .base import Runner, register
from ..types import Question, Message, TokenUsage, RunResult
from ..models import ChatMessage, converse, extract_multiple_choice, extract_numeric
from ..datasets.mmlu_pro import format_mcq_prompt
from ..datasets.math500 import format_math_prompt


# Functional role system prompts
DECOMPOSER_ROLE = """\
You are the **Decomposer**. Your job is to break down complex problems into
explicit constraints, requirements, and edge cases.

When given a problem:
1. List ALL constraints mentioned or implied (skills, preferences, incompatibilities)
2. Identify what must be satisfied vs what is preferred
3. Flag edge cases or ambiguities
4. Be exhaustive, not selective

Keep response ≤300 words. Format as numbered list of constraints.
"""

EVALUATOR_ROLE = """\
You are the **Evaluator**. Your job is to systematically check each answer option
against each constraint.

When given a problem and constraints:
1. For EACH option, check EACH constraint
2. Mark satisfied (✓) or violated (✗) with brief evidence
3. Be systematic, not holistic
4. Cite specific text from the problem when marking violations

Keep response ≤400 words. Use table or structured format.
End with `Preliminary answer: X` based on constraint counts.
"""

DEVILS_ADVOCATE_ROLE = """\
You are the **Devil's Advocate**. Your job is to challenge the emerging consensus
and find missed constraint violations.

When given evaluations:
1. Look for constraints that were overlooked
2. Question whether "satisfied" constraints are truly satisfied
3. Find edge cases that break the leading option
4. Be adversarial but fair

Keep response ≤250 words. If you find a critical flaw, state it clearly.
End with `My answer: X` if you disagree with the Evaluator.
"""

SYNTHESIZER_ROLE = """\
You are the **Synthesizer**. Your job is to aggregate all analyses and commit
to a final answer.

When given constraint analyses and votes:
1. Weight by constraint satisfaction count
2. Prioritize options with fewest critical violations
3. Break ties by reasoning quality, not vote count
4. Acknowledge uncertainty if constraints conflict

Keep response ≤200 words. Justify briefly.
End with `Final answer: X` on its own line.
"""

INDEPENDENT_ANALYST_ROLE = """\
You are an independent analyst contributing to a constraint-based evaluation.

Given a problem:
1. Identify the key constraints and requirements
2. Evaluate which answer option best satisfies them
3. Cite specific text when making claims
4. Be systematic

Keep response ≤250 words.
End with `Final answer: X` on its own line.
"""


# Strong heterogeneous panel (all Sonnet-class or better)
PANEL_V10 = {
    "decomposer": "us.anthropic.claude-sonnet-4-6",
    "evaluator": "us.anthropic.claude-sonnet-4-6",
    "devils-advocate": "us.deepseek.r1-v1:0",
    "synthesizer": "us.anthropic.claude-sonnet-4-6",
    # Additional independent analysts for vote diversity
    "analyst-1": "us.amazon.nova-pro-v1:0",
    "analyst-2": "us.meta.llama3-3-70b-instruct-v1:0",
}

ROLE_PROMPTS = {
    "decomposer": DECOMPOSER_ROLE,
    "evaluator": EVALUATOR_ROLE,
    "devils-advocate": DEVILS_ADVOCATE_ROLE,
    "synthesizer": SYNTHESIZER_ROLE,
    "analyst-1": INDEPENDENT_ANALYST_ROLE,
    "analyst-2": INDEPENDENT_ANALYST_ROLE,
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


async def _agent_response(
    role: str,
    user_prompt: str,
    context: str,
    seed: int
) -> tuple[str, TokenUsage]:
    """Get response from an agent with given role.

    Args:
        role: Role name (decomposer, evaluator, etc.)
        user_prompt: Original problem
        context: Prior conversation context
        seed: Random seed

    Returns:
        (response_text, token_usage)
    """
    system = ROLE_PROMPTS[role]
    model = PANEL_V10[role]

    if context:
        full_prompt = f"{user_prompt}\n\n--- Prior context ---\n{context}"
    else:
        full_prompt = user_prompt

    text, usage = await converse(
        [ChatMessage(role="user", text=full_prompt)],
        system=system,
        model=model,
        temperature=0.7 if role in ("analyst-1", "analyst-2") else 0.5,
    )
    return text, usage


@register("as_v10_constraint_role")
class ASV10ConstraintRoleRunner(Runner):
    """AS-V10: Constraint-based reasoning with functional roles."""

    config_id = "as_v10_constraint_role"
    model = "panel-as-v10-constraint-role"

    async def run(self, question: Question, seed: int = 42) -> RunResult:
        t0 = time.time()
        user_prompt = _build_prompt(question)
        usage = TokenUsage()
        transcript = [
            Message(role="user", speaker="user", content=user_prompt)
        ]

        # ===== PHASE 1: Constraint Extraction (independent) =====
        decomposer_instr = (
            "You are the Decomposer. List ALL constraints, requirements, and "
            "considerations from this problem as a numbered list. Be exhaustive."
        )
        decomposer_resp, decomp_u = await _agent_response(
            "decomposer",
            f"{user_prompt}\n\n{decomposer_instr}",
            "",
            seed
        )
        usage += decomp_u
        transcript.append(Message(
            role="assistant",
            speaker="decomposer",
            content=decomposer_resp
        ))

        # Also get independent constraint lists from analysts
        analyst_tasks = []
        for analyst_name in ["analyst-1", "analyst-2"]:
            analyst_instr = (
                "List the key constraints from this problem, then identify "
                "which answer option best satisfies them. Be systematic."
            )
            analyst_tasks.append(_agent_response(
                analyst_name,
                f"{user_prompt}\n\n{analyst_instr}",
                "",
                seed
            ))

        analyst_results = await asyncio.gather(*analyst_tasks)
        analyst_responses = []
        for (analyst_name, (resp, u)) in zip(["analyst-1", "analyst-2"], analyst_results):
            usage += u
            transcript.append(Message(
                role="assistant",
                speaker=analyst_name,
                content=resp
            ))
            analyst_responses.append((analyst_name, resp))

        # ===== PHASE 2: Systematic Evaluation =====
        context_phase2 = f"**Decomposer's constraint list:**\n{decomposer_resp}\n\n"
        for analyst_name, resp in analyst_responses:
            context_phase2 += f"**{analyst_name}'s analysis:**\n{resp}\n\n"

        evaluator_instr = (
            "You are the Evaluator. For EACH answer option, check it against EACH "
            "constraint from the Decomposer's list. Mark satisfied (✓) or violated (✗). "
            "Be systematic. End with `Preliminary answer: X`."
        )
        evaluator_resp, eval_u = await _agent_response(
            "evaluator",
            f"{user_prompt}\n\n{evaluator_instr}",
            context_phase2,
            seed
        )
        usage += eval_u
        transcript.append(Message(
            role="assistant",
            speaker="evaluator",
            content=evaluator_resp
        ))

        # Devil's Advocate challenges
        context_phase2b = context_phase2 + f"**Evaluator's assessment:**\n{evaluator_resp}\n\n"
        devils_instr = (
            "You are the Devil's Advocate. Challenge the Evaluator's assessment. "
            "Look for missed constraints or incorrectly marked satisfied constraints. "
            "If you find critical flaws, state them. End with `My answer: X` if you "
            "disagree."
        )
        devils_resp, devils_u = await _agent_response(
            "devils-advocate",
            f"{user_prompt}\n\n{devils_instr}",
            context_phase2b,
            seed
        )
        usage += devils_u
        transcript.append(Message(
            role="assistant",
            speaker="devils-advocate",
            content=devils_resp
        ))

        # ===== PHASE 3: Vote & Synthesis =====
        # Collect votes from all agents
        votes = []
        for agent_name, resp in [("decomposer", decomposer_resp)] + analyst_responses + [
            ("evaluator", evaluator_resp),
            ("devils-advocate", devils_resp)
        ]:
            extracted = _extract(question, resp)
            if extracted:
                votes.append((agent_name, extracted))

        # Synthesizer aggregates
        context_phase3 = context_phase2b + f"**Devil's Advocate:**\n{devils_resp}\n\n"
        vote_summary = "\n".join([f"- {name}: {ans}" for name, ans in votes])
        context_phase3 += f"**Votes:**\n{vote_summary}\n\n"

        synth_instr = (
            "You are the Synthesizer. Based on the constraint analyses and votes, "
            "provide the final answer. Prioritize options with fewest constraint "
            "violations. Break ties by reasoning quality. End with `Final answer: X`."
        )
        synth_resp, synth_u = await _agent_response(
            "synthesizer",
            f"{user_prompt}\n\n{synth_instr}",
            context_phase3,
            seed
        )
        usage += synth_u
        transcript.append(Message(
            role="assistant",
            speaker="synthesizer",
            content=synth_resp
        ))

        final_answer = _extract(question, synth_resp)

        return self._build_result(
            question, seed, transcript, final_answer, usage, t0,
            final_statement=synth_resp,
            moderator_turns=1,  # synthesizer acts as moderator
            participant_turns=5,  # decomposer, 2 analysts, evaluator, devil's advocate
            metadata={
                "protocol": "3-phase-constraint-based",
                "roles": list(PANEL_V10.keys()),
                "votes": dict(votes),
                "vote_counts": dict(Counter([v for _, v in votes])),
            },
        )
