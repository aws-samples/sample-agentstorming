# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Paper-Grade Cross-Domain Synthesis Benchmark.

Problems authored by Nova Pro (us.amazon.nova-pro-v1:0) following paper-grade
authoring constraints:

- Cross-domain synthesis required (technique from domain B applied to problem
  in domain A; single-domain expertise insufficient).
- Novelty bar: no known textbook solution; requires recognising the
  cross-domain analogy.
- Verifiable correctness anchor: gold answer includes a specific calculation,
  named theorem, quantitative bound, or concrete construction whose properties
  can be independently checked.
- Hallucination trap: each problem includes an explicit wrong-but-plausible
  claim an LLM is likely to assert; the gold answer flags how to spot it.
- Bounded scope (500-1500 words by an expert); self-contained.

Loaded from packages/benchmark/data/paper_grade/problems.json so the problem
set can be extended without code changes. The generator that produced the
generator lives outside this repository, with the study's working material.

Graded by an LLM judge (Opus 4.7) against the rubric, with hallucination-trap
detection scored separately.
"""

from __future__ import annotations

import json
from pathlib import Path

from ..types import Question
from . import register


_DATA = (
    Path(__file__).resolve().parents[3]
    / "data"
    / "paper_grade"
    / "problems.json"
)


def format_open_ended_prompt(q: Question) -> str:
    body = q.question.strip()
    return f"""Question ({q.subject}):
{body}

Provide a detailed answer with technical depth. Identify any cross-domain
analogy explicitly. Include a verifiable calculation, named technique, or
quantitative bound that an expert could independently check. Think
step-by-step if helpful. Keep your response ≤800 words."""


def _problems() -> list[dict]:
    if not _DATA.exists():
        return []
    return json.loads(_DATA.read_text())


@register("paper_grade")
def load(n: int | None = None, seed: int = 42) -> list[Question]:
    """Load paper-grade cross-domain synthesis problems.

    Args:
        n: optional cap on number of problems (default: all available).
        seed: kept for interface symmetry with other loaders; problems are
            returned in their canonical order (no sampling) since the set is
            small and we want every condition to see every problem.
    """
    del seed  # unused; canonical order is intentional
    problems = _problems()
    if n is not None:
        problems = problems[:n]
    questions: list[Question] = []
    for p in problems:
        questions.append(Question(
            id=p["id"],
            dataset="paper_grade",
            subject=f"{p['domain_a']} + {p['domain_b']}",
            question=p["problem"],
            choices=None,
            answer_key=p["gold_answer"],
            metadata={
                "domain_a": p["domain_a"],
                "domain_b": p["domain_b"],
                "title": p.get("title", ""),
                "rubric": p["rubric"],
                "max_score": p.get("max_score", 5),
                "gold_answer": p["gold_answer"],
                "hallucination_trap": p.get("hallucination_trap", ""),
                "estimated_baseline_accuracy": p.get(
                    "estimated_baseline_accuracy", None
                ),
            },
        ))
    return questions
