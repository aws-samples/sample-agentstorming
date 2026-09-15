# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Emulated vs live Agent Storming on the same questions, same seed.

## What this settles

The NeurIPS draft asserted that a 40-question pilot found emulated and live runs
agreeing on 39 of 40, and used that to justify running every bulk condition
through the emulation. No such comparison existed — no runner, no recorded
condition, no log. The claim has been withdrawn. This script is how it gets
replaced with a measured one.

## Method

For each question, run `as_free_mod` (emulated, in-process transcript) and
`live_as_free_mod` (real server, Ed25519-signed envelopes, SSE delivery) at the
same seed, and compare the extracted answers. Prompts, models, persona system
prompts, round structure and answer extraction are shared code — the transport is
the only difference.

Agreement is reported three ways, because they answer different questions:

- **answer agreement** — did both arms extract the same answer? This is what the
  withdrawn claim was about.
- **correctness agreement** — did both arms get it right or wrong together? Two
  arms can disagree on the answer and still both be wrong.
- **per-arm accuracy** — because if the live arm is systematically worse, that is
  a finding about the protocol, not noise.

## Honest statistics

With N questions and a handful of disagreements, a raw ratio is nearly
meaningless: 39/40 and 36/40 are not distinguishable at that sample size. This
prints a **Wilson 95% interval** on the agreement rate and says plainly what N
would be needed to support a tighter claim. Do not quote the point estimate
without the interval — that is the mistake this whole exercise exists to correct.

## Cost

Each question costs two full discussions: 5 specialists x 2 rounds + 2 moderator
turns, per arm. Bedrock is billed per call. `--limit` defaults deliberately low.

## Usage

    export AGENTSTORMING_BASE_URL=http://127.0.0.1:8440
    export AGENTSTORMING_TEST_DSN=postgresql://agentstorming@127.0.0.1:5432/agentstorming_test
    python scripts/equivalence-run.py --dataset mmlu_pro --limit 10 --seed 0

Writes one JSON per run under `results/equivalence/` and a summary to stdout.
Nothing is inferred that is not in those files.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "packages" / "benchmark" / "src"))

EMULATED = "as_free_mod"
LIVE = "live_as_free_mod"


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score interval. Correct at small n, unlike the normal approximation.

    At n=10, k=10 the normal approximation gives [1.0, 1.0] — a claim of
    certainty from ten observations. Wilson gives roughly [0.72, 1.0], which is
    the honest width and the reason this is here.
    """
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / d
    half = (z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))) / d
    return (max(0.0, centre - half), min(1.0, centre + half))


def n_for_margin(margin: float, p: float = 0.95, z: float = 1.96) -> int:
    """Sample size needed for a +/- margin at the given rate. For the caveat line."""
    return int(math.ceil(z * z * p * (1 - p) / (margin * margin)))


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", default="mmlu_pro")
    ap.add_argument("--limit", type=int, default=10,
                    help="questions to run; each costs two full discussions")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--out", type=Path,
                    default=REPO / "packages" / "benchmark" / "results" / "equivalence")
    args = ap.parse_args()

    from agentstorming_benchmark.runners import base as runner_base
    from agentstorming_benchmark.runners import agent_storm  # noqa: F401  registers
    from agentstorming_benchmark.runners import live_storm   # noqa: F401  registers

    base_url = os.environ.get("AGENTSTORMING_BASE_URL", "http://127.0.0.1:8440")
    print(f"# live server: {base_url}")
    print(f"# dataset={args.dataset} limit={args.limit} seed={args.seed}")

    # The registry lives in datasets/__init__; importing the module registers it.
    import importlib
    from agentstorming_benchmark import datasets as ds
    importlib.import_module(f"agentstorming_benchmark.datasets.{args.dataset}")
    questions = ds.load(args.dataset)[: args.limit]
    if not questions:
        print("no questions loaded", file=sys.stderr)
        return 2

    args.out.mkdir(parents=True, exist_ok=True)
    rows = []

    for i, q in enumerate(questions, 1):
        print(f"\n[{i}/{len(questions)}] {q.id}", flush=True)
        rec: dict = {"question_id": q.id, "dataset": args.dataset, "seed": args.seed}
        for label, config_id in (("emulated", EMULATED), ("live", LIVE)):
            t = time.time()
            try:
                r = await runner_base.make(config_id).run(q, args.seed)
                rec[label] = {
                    "answer": r.extracted_answer,
                    "correct": bool(r.is_correct),
                    "error": r.error,
                    "cost_usd": r.cost_usd,
                    "latency_sec": r.latency_sec,
                    "participant_turns": r.participant_turns,
                    "moderator_turns": r.moderator_turns,
                    "metadata": r.metadata,
                }
                flag = "" if not r.error else f"  ERROR {r.error[:60]}"
                print(f"  {label:9} answer={r.extracted_answer!r:8} "
                      f"correct={r.is_correct} {time.time()-t:.1f}s{flag}", flush=True)
                (args.out / f"{config_id}__{q.id}__seed{args.seed}.json").write_text(
                    json.dumps(rec[label], indent=2, default=str))
            except Exception as e:
                rec[label] = {"answer": "", "correct": False,
                              "error": f"{type(e).__name__}: {e}"}
                print(f"  {label:9} FAILED {type(e).__name__}: {e}", flush=True)
        rows.append(rec)

    # --- summary ----------------------------------------------------------
    usable = [r for r in rows
              if not r.get("emulated", {}).get("error")
              and not r.get("live", {}).get("error")]
    n = len(usable)
    agree_ans = sum(1 for r in usable
                    if r["emulated"]["answer"] == r["live"]["answer"])
    agree_cor = sum(1 for r in usable
                    if r["emulated"]["correct"] == r["live"]["correct"])
    acc_emu = sum(1 for r in usable if r["emulated"]["correct"])
    acc_live = sum(1 for r in usable if r["live"]["correct"])

    print("\n" + "=" * 66)
    print(f"usable pairs: {n} of {len(rows)}")
    if n:
        lo, hi = wilson(agree_ans, n)
        print(f"answer agreement:      {agree_ans}/{n} = {agree_ans/n:.0%}   "
              f"Wilson 95% [{lo:.0%}, {hi:.0%}]")
        lo2, hi2 = wilson(agree_cor, n)
        print(f"correctness agreement: {agree_cor}/{n} = {agree_cor/n:.0%}   "
              f"Wilson 95% [{lo2:.0%}, {hi2:.0%}]")
        print(f"accuracy emulated:     {acc_emu}/{n}")
        print(f"accuracy live:         {acc_live}/{n}")
        print()
        print("Disagreements:")
        any_dis = False
        for r in usable:
            if r["emulated"]["answer"] != r["live"]["answer"]:
                any_dis = True
                print(f"  {r['question_id']}: emulated={r['emulated']['answer']!r} "
                      f"live={r['live']['answer']!r}")
        if not any_dis:
            print("  none")
        print()
        need = n_for_margin(0.05)
        print(f"CAVEAT: at n={n} the interval above is wide. Supporting "
              f"'equivalent within +/-5%' needs roughly n={need}.")
        print("Quote the interval, never the point estimate alone. Absence of a "
              "detected difference at this n is not evidence of equivalence.")
    print("=" * 66)

    summary = args.out / f"summary__{args.dataset}__seed{args.seed}__n{len(rows)}.json"
    summary.write_text(json.dumps({
        "dataset": args.dataset, "seed": args.seed,
        "requested": len(rows), "usable_pairs": n,
        "answer_agreement": {"k": agree_ans, "n": n,
                             "wilson95": wilson(agree_ans, n) if n else None},
        "correctness_agreement": {"k": agree_cor, "n": n,
                                  "wilson95": wilson(agree_cor, n) if n else None},
        "accuracy": {"emulated": acc_emu, "live": acc_live},
        "rows": rows,
    }, indent=2, default=str))
    print(f"summary: {summary}")
    return 0 if n else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
