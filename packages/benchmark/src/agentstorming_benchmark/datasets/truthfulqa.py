# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""TruthfulQA (Lin et al. 2021) loader - multiple choice variant.

Tests whether models generate truthful answers. 817 questions spanning
38 categories. Public on HuggingFace: truthful_qa, multiple_choice subset.

arXiv:2109.07958
"""

from __future__ import annotations

import hashlib
import logging
import random
from typing import Any

from . import register
from ..types import Question


log = logging.getLogger(__name__)


@register("truthfulqa_mc")
def load_truthfulqa_mc(n: int = 817, seed: int = 0) -> list[Question]:
    """Load TruthfulQA multiple choice variant.

    Args:
        n: Number of questions to sample (default 817 = full dataset).
        seed: Random seed for reproducibility.

    Returns:
        List of Question objects formatted as MCQ.
    """
    from datasets import load_dataset
    from ._revisions import resolve

    try:
        ds = load_dataset("truthful_qa", "multiple_choice", split="validation",
                             revision=resolve("truthful_qa"))
    except Exception as e:  # noqa: BLE001
        log.warning("TruthfulQA: load_dataset failed: %s; returning empty list", e)
        return []

    rng = random.Random(seed)
    rows: list[dict[str, Any]] = list(ds)
    rng.shuffle(rows)

    out: list[Question] = []
    for i, row in enumerate(rows[:n]):
        question_text = row.get("question", "")

        # TruthfulQA MC format: mc1_targets has correct answer(s), mc2_targets has all choices
        # We'll use mc2 format which gives {choices: [...], labels: [0/1 for each]}
        mc2_targets = row.get("mc2_targets", {})
        choices_raw = mc2_targets.get("choices", [])
        labels = mc2_targets.get("labels", [])

        if not question_text or not choices_raw or not labels:
            log.warning("TruthfulQA row %d missing data; skipping", i)
            continue

        # Find the first correct answer (label=1)
        try:
            correct_idx = labels.index(1)
        except ValueError:
            log.warning("TruthfulQA row %d has no correct answer; skipping", i)
            continue

        # Build MCQ with up to 4 choices (1 correct + 3 incorrect)
        # Shuffle to avoid position bias
        correct_choice = choices_raw[correct_idx]
        incorrect_choices = [c for idx, c in enumerate(choices_raw) if labels[idx] == 0]

        # CRITICAL FIX: Use stable seed based on question text hash, not enumeration position.
        # Previous bug: seed * 1000 + i used the position in the SHUFFLED list, causing
        # different gold answers when n changed. Now using hash of question text for stability.
        # IMPORTANT: Must use hashlib (md5/sha256) NOT Python's hash() which is randomized
        # per-process via PYTHONHASHSEED. hash() caused iter68-85 data corruption.
        question_hash = int(hashlib.md5(question_text.encode(), usedforsecurity=False).hexdigest(), 16) % (2**31)
        local_rng = random.Random(seed ^ question_hash)
        sampled_incorrect = local_rng.sample(
            incorrect_choices,
            min(3, len(incorrect_choices))
        )

        # Combine and shuffle
        all_choices = [correct_choice] + sampled_incorrect
        order = list(range(len(all_choices)))
        local_rng.shuffle(order)
        shuffled_choices = [all_choices[j] for j in order]
        correct_letter = chr(ord("A") + order.index(0))

        # Category from row
        category = row.get("category", "general")

        out.append(Question(
            id=f"truthfulqa-{i}",
            dataset="truthfulqa_mc",
            subject=category.lower().replace(" ", "_"),
            question=question_text,
            choices=shuffled_choices,
            answer_key=correct_letter,
            metadata={
                "category": category,
                "source": row.get("source", ""),
                "best_answer": row.get("best_answer", ""),  # Single best answer per mc1
                "num_choices_original": len(choices_raw),
            },
        ))

    return out
