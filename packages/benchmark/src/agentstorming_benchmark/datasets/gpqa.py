# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""GPQA Diamond (Rein et al. 2023) loader. Requires HF token (gated).

If `HF_TOKEN` env var isn't set, returns an empty list and logs a warning, so
that benchmark runs gracefully skip GPQA. Everything is a drop-in MCQ.
"""

from __future__ import annotations

import logging
import os
import random
from typing import Any

from . import register
from ..types import Question


log = logging.getLogger(__name__)


@register("gpqa_diamond")
def load_gpqa_diamond(n: int = 198, seed: int = 0) -> list[Question]:
    token = os.getenv("HF_TOKEN")
    if not token:
        log.warning("GPQA Diamond: HF_TOKEN not set; skipping. Set HF_TOKEN to enable.")
        return []

    from datasets import load_dataset

    from ._revisions import resolve
    try:
        ds = load_dataset("Idavidrein/gpqa", "gpqa_diamond", split="train", token=token,
                             revision=resolve("Idavidrein/gpqa"))
    except Exception as e:  # noqa: BLE001
        log.warning("GPQA Diamond: load_dataset failed: %s; returning empty list", e)
        return []

    rng = random.Random(seed)
    rows: list[dict[str, Any]] = list(ds)
    rng.shuffle(rows)

    out: list[Question] = []
    for i, row in enumerate(rows[:n]):
        correct = row["Correct Answer"]
        incorrect = [row[f"Incorrect Answer {k}"] for k in (1, 2, 3)]
        answers = [correct] + incorrect
        # Deterministic shuffle of choices based on seed and row index.
        local_rng = random.Random(seed * 1000 + i)
        order = [0, 1, 2, 3]
        local_rng.shuffle(order)
        shuffled = [answers[j] for j in order]
        correct_letter = chr(ord("A") + order.index(0))
        out.append(Question(
            id=f"gpqa-{row.get('Record ID', i)}",
            dataset="gpqa_diamond",
            subject=(row.get("Subdomain") or row.get("High-level domain") or "science").lower(),
            question=row["Question"],
            choices=shuffled,
            answer_key=correct_letter,
            metadata={
                "high_level_domain": row.get("High-level domain"),
                "subdomain": row.get("Subdomain"),
                "explanation": row.get("Explanation", "")[:2000],
            },
        ))
    return out
