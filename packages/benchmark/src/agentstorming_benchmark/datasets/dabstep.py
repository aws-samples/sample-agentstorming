# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""DABStep (Data Analysis Benchmark) loader — arXiv:2506.23719.

Finance/data tasks with factoid auto-grading. 450 hard-tier items.
Public on HuggingFace: Watts-Lab/dabstep (assumed name; verify).
"""

from __future__ import annotations

import logging
import random
from typing import Any

from . import register
from ..types import Question


log = logging.getLogger(__name__)


@register("dabstep_hard")
def load_dabstep_hard(n: int = 100, seed: int = 0) -> list[Question]:
    """Load DABStep hard tier.

    Args:
        n: Number of questions to sample (default 100 for budget control).
        seed: Random seed for reproducibility.

    Returns:
        List of Question objects. DABStep is open-response but we'll extract
        the gold answer and format as a single-choice MCQ for consistency.
    """
    from datasets import load_dataset
    from ._revisions import resolve

    try:
        # Correct path: adyen/DABstep with 'default' split containing 450 items
        ds = load_dataset("adyen/DABstep", split="default",
                             revision=resolve("adyen/DABstep"))
        log.info("DABStep loaded from adyen/DABstep")
    except Exception as e:  # noqa: BLE001
        log.warning("DABStep: load_dataset failed: %s; returning empty list", e)
        return []

    # Filter for hard-level only
    rows: list[dict[str, Any]] = [r for r in ds if r.get("level") == "hard"]
    log.info("DABStep: filtered to %d hard-level items", len(rows))

    rng = random.Random(seed)
    rng.shuffle(rows)

    out: list[Question] = []
    for i, row in enumerate(rows[:n]):
        question_text = row.get("question", "")
        answer_gold = str(row.get("answer", "")).strip()
        guidelines = row.get("guidelines", "")
        task_id = row.get("task_id", i)

        if not question_text:
            log.warning("DABStep row %d missing question; skipping", i)
            continue

        # DABStep is open-response. For uniformity with MCQ benchmarks,
        # we'll format it as a single-choice question where the model must generate
        # the answer and we check exact-match or semantic-match.
        # Store the gold answer in metadata and use a placeholder choice list.
        # Guidelines specify answer format (e.g. "number rounded to 2 decimals")
        full_question = question_text
        if guidelines:
            full_question += f"\n\nGuidelines: {guidelines}"

        out.append(Question(
            id=f"dabstep-hard-{task_id}",
            dataset="dabstep_hard",
            subject="finance-data",
            question=full_question,
            choices=[answer_gold] if answer_gold else ["<open-response>"],
            answer_key="A",  # Placeholder; actual grading is by text-match.
            metadata={
                "dabstep_tier": "hard",
                "answer_gold": answer_gold,
                "is_open_response": True,
                "guidelines": guidelines,
                "task_id": task_id,
            },
        ))

    return out
