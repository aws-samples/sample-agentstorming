# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""MATH-500 (Lightman et al. 2023, subset of Hendrycks MATH) loader.

Uses `HuggingFaceH4/MATH-500` which is a publicly available 500-question
subset. Answers are numeric / algebraic strings (e.g. "42", "3/4", "x^2+1").
"""

from __future__ import annotations

import random

from . import register
from ..types import Question


@register("math500")
def load_math500(n: int = 500, seed: int = 0) -> list[Question]:
    from datasets import load_dataset
    from ._revisions import resolve
    try:
        ds = load_dataset("HuggingFaceH4/MATH-500", split="test",
                             revision=resolve("HuggingFaceH4/MATH-500"))
    except Exception:  # noqa: BLE001
        return []
    rows = list(ds)
    rng = random.Random(seed)
    rng.shuffle(rows)
    out: list[Question] = []
    for row in rows[:n]:
        out.append(Question(
            id=f"math500-{row.get('unique_id', row.get('idx', len(out)))}",
            dataset="math500",
            subject=(row.get("subject") or "math").lower(),
            question=row["problem"],
            choices=None,
            answer_key=str(row["answer"]).strip(),
            metadata={"level": row.get("level")},
        ))
    return out


def format_math_prompt(q: Question) -> str:
    return f"""Problem:
{q.question}

Solve step by step. Present the final answer on a line of the form
`Final answer: X`, using \\boxed{{...}} where helpful."""
