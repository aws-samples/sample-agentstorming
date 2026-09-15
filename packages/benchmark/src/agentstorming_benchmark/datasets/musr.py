# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""MuSR (Multistep Soft Reasoning) loader — arXiv:2310.16049, ICLR 2024.

Murder mysteries, team allocation, object tracking — long narrative + MCQ.
MIT license, public on HuggingFace: TAUR-Lab/MuSR.
"""

from __future__ import annotations

import logging
import random
from typing import Any

from . import register
from ..types import Question


log = logging.getLogger(__name__)


@register("musr")
def load_musr(n: int = 250, seed: int = 0, task: str = "murder_mysteries") -> list[Question]:
    """Load MuSR dataset.

    Args:
        n: Number of questions to sample (default 250 for stratified sampling).
        seed: Random seed for reproducibility.
        task: Which MuSR task ('murder_mysteries', 'team_allocation', 'object_placements').
               Default: 'murder_mysteries' (most contestable).

    Returns:
        List of Question objects with narrative context + MCQ.
    """
    from datasets import load_dataset
    from ._revisions import resolve

    try:
        # MuSR returns a dict with three task splits. Access the requested task.
        ds_dict = load_dataset("TAUR-Lab/MuSR", revision=resolve("TAUR-Lab/MuSR"))
        if task not in ds_dict:
            log.warning("MuSR: task '%s' not found; available: %s", task, list(ds_dict.keys()))
            return []
        ds = ds_dict[task]
    except Exception as e:  # noqa: BLE001
        log.warning("MuSR: load_dataset failed: %s; returning empty list", e)
        return []
    
    rng = random.Random(seed)
    rows: list[dict[str, Any]] = list(ds)
    rng.shuffle(rows)
    
    out: list[Question] = []
    for i, row in enumerate(rows[:n]):
        narrative = row.get("narrative", "")
        question_text = row.get("question", "")
        choices_raw = row.get("choices", [])
        answer_idx = row.get("answer_index", row.get("answer", 0))

        # MuSR format: choices is a STRING representation of a list (e.g. "['A', 'B']")
        # Parse it into an actual list.
        if isinstance(choices_raw, str):
            import ast
            try:
                choices_raw = ast.literal_eval(choices_raw)
            except (ValueError, SyntaxError):
                log.warning("MuSR row %d: failed to parse choices string; skipping", i)
                continue

        if not choices_raw:
            log.warning("MuSR row %d missing choices; skipping", i)
            continue
        
        answer_key = chr(ord("A") + answer_idx) if answer_idx < len(choices_raw) else "A"
        
        out.append(Question(
            id=f"musr-{task}-{i}",
            dataset="musr",
            subject=task.replace("_", "-"),
            question=f"{narrative}\n\n{question_text}",
            choices=choices_raw,
            answer_key=answer_key,
            metadata={
                "narrative_length": len(narrative),
                "musr_task": task,
            },
        ))

    return out


@register("musr_murder")
def load_musr_murder(n: int = 250, seed: int = 0) -> list[Question]:
    """Convenience alias for MuSR murder mysteries task."""
    return load_musr(n=n, seed=seed, task="murder_mysteries")


@register("musr_object_placements")
def load_musr_object_placements(n: int = 256, seed: int = 0) -> list[Question]:
    """Convenience alias for MuSR object_placements task."""
    return load_musr(n=n, seed=seed, task="object_placements")


@register("musr_team_allocation")
def load_musr_team_allocation(n: int = 250, seed: int = 0) -> list[Question]:
    """Convenience alias for MuSR team_allocation task."""
    return load_musr(n=n, seed=seed, task="team_allocation")
