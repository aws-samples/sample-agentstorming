# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Base runner abstraction and registry."""

from __future__ import annotations

import abc
import time
from typing import Callable

from ..types import Question, RunResult, TokenUsage, Message
from ..models import cost_usd
from ..config import PRIMARY_MODEL


class Runner(abc.ABC):
    """Abstract: one configuration (e.g. `s1`, `mad`, `as_free_mod`)."""

    config_id: str = ""
    model: str = PRIMARY_MODEL

    @abc.abstractmethod
    async def run(self, question: Question, seed: int) -> RunResult:
        ...

    def _build_result(
        self,
        question: Question,
        seed: int,
        transcript: list[Message],
        extracted_answer: str,
        usage: TokenUsage,
        t_start: float,
        *,
        final_statement: str = "",
        moderator_turns: int = 0,
        participant_turns: int = 0,
        error: str = "",
        metadata: dict | None = None,
    ) -> RunResult:
        is_correct = answer_matches(extracted_answer, question.answer_key, question.dataset, question.metadata)
        return RunResult(
            config_id=self.config_id,
            dataset=question.dataset,
            question_id=question.id,
            seed=seed,
            model=self.model,
            transcript=transcript,
            extracted_answer=extracted_answer,
            is_correct=is_correct,
            usage=usage,
            cost_usd=cost_usd(usage, self.model),
            latency_sec=round(time.time() - t_start, 3),
            final_statement=final_statement,
            moderator_turns=moderator_turns,
            participant_turns=participant_turns,
            error=error,
            metadata=metadata or {},
        )


def answer_matches(pred: str, gold: str, dataset: str, metadata: dict | None = None) -> bool:
    """Check if predicted answer matches gold answer.

    Args:
        pred: Predicted answer (extracted from model output)
        gold: Gold answer (from question.answer_key)
        dataset: Dataset name for dataset-specific grading logic
        metadata: Question metadata (for open-response datasets like DABStep)

    Returns:
        True if answers match according to dataset-specific rules
    """
    if not pred:
        return False
    pred = pred.strip()
    gold = gold.strip()

    # MCQ datasets - letter matching
    if dataset in ("mmlu_pro", "gpqa_diamond", "musr"):
        return pred.upper() == gold.upper()

    # Math datasets - LaTeX normalization
    if dataset == "math500":
        norm = lambda s: "".join(s.split()).replace("\\left", "").replace("\\right", "")
        return norm(pred) == norm(gold)

    # Open-response datasets - use metadata gold answer
    if dataset == "dabstep_hard":
        if metadata and "answer_gold" in metadata:
            gold = str(metadata["answer_gold"]).strip()
        # Exact match on normalized strings (case-insensitive, whitespace-normalized)
        pred_norm = " ".join(pred.lower().split())
        gold_norm = " ".join(gold.lower().split())
        return pred_norm == gold_norm

    # Default: exact match
    return pred == gold


_REGISTRY: dict[str, type] = {}


def register(config_id: str):
    def deco(cls):
        cls.config_id = config_id
        _REGISTRY[config_id] = cls
        return cls
    return deco


def make(config_id: str) -> Runner:
    return _REGISTRY[config_id]()


def available() -> list[str]:
    return sorted(_REGISTRY)
