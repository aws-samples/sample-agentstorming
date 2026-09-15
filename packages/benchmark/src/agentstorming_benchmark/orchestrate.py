# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Runner orchestration: enumerate work, dispatch runners, checkpoint.

Work unit = (config_id, dataset, seed, question_id). Idempotent: if the
corresponding result file already exists on disk we skip the work.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path

from .config import RESULTS_DIR, TIERS
from .datasets import load as load_dataset
from .runners import make as make_runner, available as runners_available
from .types import Question, RunResult, TokenUsage, save_result


log = logging.getLogger(__name__)


@dataclass
class Task:
    config_id: str
    dataset: str
    seed: int
    question: Question

    def result_path(self, results_dir: Path = RESULTS_DIR) -> Path:
        return (results_dir / "runs" / self.config_id / self.dataset /
                f"seed{self.seed}" / f"{self.question.id}.json")


def plan_tier(tier_name: str, *, n_override: int | None = None) -> list[Task]:
    tier = TIERS[tier_name]
    tasks: list[Task] = []
    for dataset in tier.datasets:
        q_count = n_override or tier.n_questions
        questions = load_dataset(dataset, n=q_count, seed=0)
        for config in tier.configs:
            for seed in tier.seeds:
                for q in questions:
                    tasks.append(Task(config, dataset, seed, q))
    return tasks


async def _run_task(task: Task) -> RunResult | None:
    path = task.result_path()
    if path.exists():
        return None
    runner = make_runner(task.config_id)
    try:
        r = await runner.run(task.question, task.seed)
    except Exception as e:  # noqa: BLE001
        log.exception("runner failed: config=%s q=%s seed=%s", task.config_id, task.question.id, task.seed)
        r = RunResult(
            config_id=task.config_id, dataset=task.dataset, question_id=task.question.id,
            seed=task.seed, model=runner.model, transcript=[], extracted_answer="",
            is_correct=False, usage=TokenUsage(),
            cost_usd=0.0, latency_sec=0.0, error=f"{type(e).__name__}: {e}",
        )
    save_result(r, str(path))
    return r


async def run_all(tasks: list[Task], *, concurrency: int = 4) -> None:
    if not tasks:
        log.info("no tasks to run (all results already on disk).")
        return
    sem = asyncio.Semaphore(concurrency)

    async def worker(task: Task):
        async with sem:
            return await _run_task(task)

    from tqdm import tqdm
    pbar = tqdm(total=len(tasks), desc="runs")
    coros = [worker(t) for t in tasks]
    try:
        for coro in asyncio.as_completed(coros):
            try:
                await coro
            except Exception:  # noqa: BLE001
                log.exception("task failed; continuing")
            pbar.update(1)
    finally:
        pbar.close()


def pending(tasks: list[Task]) -> list[Task]:
    return [t for t in tasks if not t.result_path().exists()]


def summary(tasks: list[Task]) -> dict:
    done = sum(1 for t in tasks if t.result_path().exists())
    return {"total": len(tasks), "done": done, "remaining": len(tasks) - done}
