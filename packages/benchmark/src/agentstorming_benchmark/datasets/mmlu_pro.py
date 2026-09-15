# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""MMLU-Pro (Wang et al. 2024) loader.

`TIGER-Lab/MMLU-Pro` on HuggingFace. 12k questions across 14 subjects, each with
up to 10 options (not all questions use all options).

We build a *stratified* subsample: proportional across subjects, deterministic
per seed. This ensures STEM-heavy coverage and reproducibility.
"""

from __future__ import annotations

import random
from collections import defaultdict

from . import register
from ..types import Question


# Subjects in MMLU-Pro we include in the primary sample. We focus on
# disciplines where multi-expert reasoning plausibly helps; others can be
# added via CLI flag.
DEFAULT_SUBJECTS = [
    "biology",
    "business",
    "chemistry",
    "computer science",
    "economics",
    "engineering",
    "health",
    "history",
    "law",
    "math",
    "philosophy",
    "physics",
    "psychology",
    "other",
]


@register("mmlu_pro")
def load_mmlu_pro(
    n: int = 200,
    seed: int = 0,
    subjects: list[str] | None = None,
    split: str = "test",
) -> list[Question]:
    from datasets import load_dataset
    from ._revisions import resolve
    ds = load_dataset("TIGER-Lab/MMLU-Pro", split=split,
                         revision=resolve("TIGER-Lab/MMLU-Pro"))
    subjects = subjects or DEFAULT_SUBJECTS
    by_subject: dict[str, list[dict]] = defaultdict(list)
    for row in ds:
        subj = (row.get("category") or "other").lower()
        if subj not in subjects:
            continue
        by_subject[subj].append(row)

    rng = random.Random(seed)
    for rows in by_subject.values():
        rng.shuffle(rows)

    total = sum(len(r) for r in by_subject.values())
    if total == 0:
        raise RuntimeError("MMLU-Pro: no rows after subject filter")

    # Proportional allocation with round-robin tie-breaker so small subjects
    # still get represented.
    quotas = {s: max(1, int(round(n * len(by_subject[s]) / total))) for s in by_subject}
    while sum(quotas.values()) > n:
        biggest = max(quotas, key=lambda s: quotas[s])
        quotas[biggest] -= 1
    while sum(quotas.values()) < n:
        smallest = min(quotas, key=lambda s: quotas[s])
        smallest_has_more = len(by_subject[smallest]) > quotas[smallest]
        if smallest_has_more:
            quotas[smallest] += 1
        else:
            break

    picks: list[Question] = []
    for subj, q in quotas.items():
        for row in by_subject[subj][:q]:
            opts = [o for o in row["options"] if o not in (None, "N/A", "")]
            n_opts = len(opts)
            # ans_letter is the ground-truth letter (A, B, ...).
            if "answer" in row and isinstance(row["answer"], str):
                ans_letter = row["answer"].strip().upper()
            elif "answer_index" in row:
                ans_letter = chr(ord("A") + int(row["answer_index"]))
            else:
                continue
            picks.append(Question(
                id=f"mmlu_pro-{row.get('question_id', row.get('index', len(picks)))}",
                dataset="mmlu_pro",
                subject=subj,
                question=row["question"],
                choices=opts,
                answer_key=ans_letter,
                metadata={
                    "n_options": n_opts,
                    "src": row.get("src", ""),
                },
            ))
    return picks[:n]


def format_mcq_prompt(q: Question) -> str:
    """Standard multiple-choice prompt used by every runner."""
    letters = [chr(ord("A") + i) for i in range(len(q.choices or []))]
    body = q.question.strip()
    options = "\n".join(f"{l}. {c}" for l, c in zip(letters, q.choices or []))
    return f"""Question ({q.subject}):
{body}

Options:
{options}

Think carefully, then state your final answer on a line of the form
`Final answer: X`, where X is the single letter of the correct option."""
