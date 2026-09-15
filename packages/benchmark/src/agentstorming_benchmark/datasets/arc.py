# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""ARC-Challenge (AI2 Reasoning Challenge) loader.

Grade-school science questions requiring reasoning. Challenge subset
has 1,172 questions that are harder than the Easy subset.

Public on HuggingFace: ai2_arc, challenge split.
Paper: Clark et al. 2018, arXiv:1803.05457
"""

from __future__ import annotations

import logging
import random
from typing import Any

from . import register
from ..types import Question


log = logging.getLogger(__name__)


@register("arc_challenge")
def load_arc_challenge(n: int = 1172, seed: int = 0) -> list[Question]:
    """Load ARC-Challenge questions.

    Args:
        n: Number of questions to sample (default 1172 = full test set).
        seed: Random seed for reproducibility.

    Returns:
        List of Question objects formatted as MCQ.
    """
    from datasets import load_dataset
    from ._revisions import resolve

    try:
        ds = load_dataset("ai2_arc", "ARC-Challenge", split="test",
                             revision=resolve("ai2_arc"))
    except Exception as e:  # noqa: BLE001
        log.warning("ARC-Challenge: load_dataset failed: %s; returning empty list", e)
        return []

    rng = random.Random(seed)
    rows: list[dict[str, Any]] = list(ds)
    rng.shuffle(rows)

    out: list[Question] = []
    for i, row in enumerate(rows[:n]):
        question_text = row.get("question", "")
        answer_key = row.get("answerKey", "")

        # Choices come as {"text": [...], "label": ["A", "B", ...]}
        choices_obj = row.get("choices", {})
        choice_texts = choices_obj.get("text", [])
        choice_labels = choices_obj.get("label", [])

        if not question_text or not choice_texts or not answer_key:
            log.warning("ARC row %d missing data; skipping", i)
            continue

        # ARC answer keys are sometimes "A", "B", etc. or "1", "2", etc.
        # Normalize to letter format
        if answer_key.isdigit():
            answer_letter = chr(ord("A") + int(answer_key) - 1)
        else:
            answer_letter = answer_key.upper()

        # Build choice list in order, verify answer_key is valid
        if answer_letter not in choice_labels:
            # Try to find it by position
            try:
                answer_idx = choice_labels.index(answer_key)
                answer_letter = chr(ord("A") + answer_idx)
            except ValueError:
                log.warning("ARC row %d invalid answer_key: %s; skipping", i, answer_key)
                continue

        # Reorder choices to standard A, B, C, D format
        ordered_choices = []
        label_to_text = dict(zip(choice_labels, choice_texts))
        for label_char in ["A", "B", "C", "D", "E", "F"]:
            if label_char in label_to_text:
                ordered_choices.append(label_to_text[label_char])
            else:
                break

        out.append(Question(
            id=row.get("id", f"arc-challenge-{i}"),
            dataset="arc_challenge",
            subject="science",
            question=question_text,
            choices=ordered_choices,
            answer_key=answer_letter,
            metadata={
                "difficulty": "challenge",
                "num_choices": len(ordered_choices),
            },
        ))

    return out
