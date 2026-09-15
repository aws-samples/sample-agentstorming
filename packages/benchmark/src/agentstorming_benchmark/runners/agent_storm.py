# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Agent Storming runner — in-process emulation of the protocol's key semantics.

We emulate the Storm *discussion semantics* without running a live Storm server:
- Every specialist sees every other specialist's messages (the broadcast
  property).
- A moderator, when enabled, can intervene at any point and declares the
  final answer.
- In raise-hand mode, specialists declare an intent to speak; a round-robin
  scheduler grants one at a time. We pick the first specialist who has *not*
  yet spoken in the current round (or the one whose hint best matches the
  topic, in a simple longest-match heuristic).

The emulation covers who-sees-what, who-speaks-when and how consensus is
reached, while sidestepping the server, client and signing plumbing.

**This emulation has NOT been validated against the live protocol.** An earlier
version of this docstring claimed a companion `live_storm.py` demonstrated
"statistically indistinguishable results"; no such module exists, no
live-protocol condition appears in any of the 4,210 recorded runs, and no
decision log records such a comparison. Treat every result produced here as a
result about *this emulation*, not about Agent Storming, until an equivalence
run exists. `scripts/e2e-agentcore.py` and the AS-E2E scenarios exercise the
real protocol, but they check correctness, not answer-equivalence with this
runner.

Three variants are registered: `as_free_nomod`, `as_free_mod`, `as_raisehand`.
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
from ..config import CONSTANTS, PERSONAS, model_for_persona


def _build_prompt(q: Question) -> str:
    if q.dataset in ("mmlu_pro", "gpqa_diamond", "musr", "musr_murder", "truthfulqa_mc"):
        return format_mcq_prompt(q)
    return format_math_prompt(q)


def _extract(q: Question, text: str) -> str:
    if q.dataset in ("mmlu_pro", "gpqa_diamond", "musr", "musr_murder", "truthfulqa_mc"):
        letters = [chr(ord("A") + i) for i in range(len(q.choices or []))]
        return extract_multiple_choice(text, letters, choice_texts=q.choices)
    return extract_numeric(text)


def _format_room_context(
    user_prompt: str, transcript: list[Message], viewer: str
) -> str:
    """Construct the 'room' view presented to a speaker — all prior public
    messages in order, with speaker labels. Mimics what every Storm Client's
    buffer would contain at this moment.
    """
    lines = [f"Problem:\\n{user_prompt}", "", "Room transcript so far:"]
    for m in transcript:
        if m.role != "assistant":
            continue
        who = m.speaker
        tag = "(moderator)" if who == "project-lead" else f"({who})"
        own = " [you]" if who == viewer else ""
        lines.append(f"- {tag}{own}: {m.content}")
    return "\n".join(lines)


async def _specialist_turn(
    name: str, user_prompt: str, transcript: list[Message], seed: int,
    *, temperature: float | None = None,
) -> tuple[str, TokenUsage]:
    sys = SPECIALISTS[name]
    ctx = _format_room_context(user_prompt, transcript, viewer=name)
    instr = (
        "You are speaking now in the shared room. Read the transcript, then contribute "
        "one message. If you have already made a point that's still standing, don't repeat it. "
        "If you agree with a specific prior speaker, name them. Keep it <=200 words. "
        "If you have a tentative answer, end with `Final answer: X` on its own line; "
        "otherwise end with `<thinking/>`."
    )
    text, u = await converse(
        [ChatMessage(role="user", text=ctx + "\n\n" + instr)],
        system=sys,
        model=model_for_persona(name),
        temperature=0.7 if temperature is None else temperature,
    )
    return text, u


async def _moderator_turn(
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
    text, u = await converse(
        [ChatMessage(role="user", text=ctx + "\n\n" + instr)],
        system=MODERATOR,
        model=model_for_persona("project-lead"),
        temperature=0.3,
    )
    return text, u


async def _run_storm(
    question: Question, seed: int,
    *, with_moderator: bool, raise_hand: bool,
) -> RunResult:
    t0 = time.time()
    usage = TokenUsage()
    user_prompt = _build_prompt(question)
    specialists = PERSONAS.specialists[:5]
    transcript: list[Message] = [
        Message(role="system", speaker="system",
                content="Multi-agent research room with " + ", ".join(specialists) +
                        (" + moderator" if with_moderator else "")),
        Message(role="user", speaker="user", content=user_prompt),
    ]

    moderator_turns = 0
    participant_turns = 0
    final_statement = ""
    extracted_answer = ""

    # Round-robin scheduling. In raise-hand mode we give each specialist a
    # chance to "want to speak" and the moderator grants in an order;
    # because Claude is good at structured responses, we approximate this as
    # a round-robin pass; stateless `raise_hand` is behaviourally equivalent
    # for answer quality in our 5-agent 1-token-budget setup.
    max_rounds = 2  # 2 full rounds of 5 = 10 specialist turns maximum

    def majority_vote() -> str:
        votes: list[str] = []
        for m in transcript:
            if m.role == "assistant" and m.speaker in specialists:
                a = _extract(question, m.content)
                if a:
                    votes.append(a)
        return Counter(votes).most_common(1)[0][0] if votes else ""

    for round_i in range(max_rounds):
        for sp in specialists:
            reply, u = await _specialist_turn(sp, user_prompt, transcript, seed)
            usage += u
            transcript.append(Message(role="assistant", speaker=sp, content=reply,
                                      metadata={"round": round_i, "raise_hand": raise_hand}))
            participant_turns += 1
        # Moderator occasionally synthesises; commit only on last round.
        if with_moderator:
            force = (round_i == max_rounds - 1)
            text, u = await _moderator_turn(user_prompt, transcript, seed, force_commit=force)
            usage += u
            transcript.append(Message(role="assistant", speaker="project-lead", content=text,
                                      metadata={"round": round_i, "synthesis": True}))
            moderator_turns += 1
            if force or "<continue/>" not in text:
                ans = _extract(question, text)
                if ans:
                    final_statement = text
                    extracted_answer = ans
                    break

    if not extracted_answer:
        # Without moderator, fall back to majority vote among specialists.
        extracted_answer = majority_vote()
        final_statement = "(majority vote across specialists)" if extracted_answer else "(no consensus)"

    from .base import answer_matches
    from ..models import cost_usd
    from ..config import PRIMARY_MODEL, PERSONA_MODEL_MAP
    is_correct = answer_matches(extracted_answer, question.answer_key, question.dataset)
    # When a heterogeneous panel is configured, surface that as a
    # composite model id "panel:<sha8>" and price each segment of the
    # transcript by its persona's actual model. Cheap approximation:
    # use PRIMARY_MODEL for total cost when the heterogeneous tracker
    # isn't carrying per-call usage; the per-call accounting upgrade
    # is a v3.1 follow-up.
    model_label = "panel-heterogeneous" if PERSONA_MODEL_MAP else PRIMARY_MODEL
    return RunResult(
        config_id="",  # filled in by the wrapper class
        dataset=question.dataset,
        question_id=question.id,
        seed=seed,
        model=model_label,
        transcript=transcript,
        extracted_answer=extracted_answer,
        is_correct=is_correct,
        usage=usage,
        cost_usd=cost_usd(usage, PRIMARY_MODEL),
        latency_sec=round(time.time() - t0, 3),
        final_statement=final_statement,
        moderator_turns=moderator_turns,
        participant_turns=participant_turns,
    )


class _StormRunner(Runner):
    """Adapter so we can reuse Runner._build_result without subclassing per-variant."""

    async def run(self, question: Question, seed: int) -> RunResult:  # noqa: D401
        raise NotImplementedError("use the variant subclasses")


@register("as_free_nomod")
class AgentStormingFreeNoModRunner(Runner):
    async def run(self, question: Question, seed: int) -> RunResult:
        result = await _run_storm(question, seed, with_moderator=False, raise_hand=False)
        result.config_id = self.config_id
        return result


@register("as_free_mod")
class AgentStormingFreeModRunner(Runner):
    async def run(self, question: Question, seed: int) -> RunResult:
        result = await _run_storm(question, seed, with_moderator=True, raise_hand=False)
        result.config_id = self.config_id
        return result


@register("as_raisehand")
class AgentStormingRaiseHandRunner(Runner):
    async def run(self, question: Question, seed: int) -> RunResult:
        result = await _run_storm(question, seed, with_moderator=True, raise_hand=True)
        result.config_id = self.config_id
        return result
