# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Result aggregation, statistical tests, and paper-ready outputs.

Reads `results/runs/<config>/<dataset>/<seed>/*.json`, produces:
  - results/aggregated/<config>.csv — per-question outcomes.
  - results/aggregated/summary.csv — accuracy / cost / latency per config.
  - results/paper-ready/tables/*.tex
  - results/paper-ready/plots/*.pdf
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections import defaultdict
from dataclasses import asdict
from pathlib import Path

import numpy as np
import pandas as pd

from .config import RESULTS_DIR, CONFIG_IDS
from .types import RunResult, load_result
from .metrics import contribution_diversity, cross_reference_rate, convergence_to_concrete, citation_accuracy_rate


log = logging.getLogger(__name__)


# --------- aggregation ---------

def collect_results(results_dir: Path = RESULTS_DIR) -> list[RunResult]:
    runs_dir = results_dir / "runs"
    if not runs_dir.exists():
        return []
    out: list[RunResult] = []
    for config_dir in runs_dir.iterdir():
        if not config_dir.is_dir():
            continue
        for dataset_dir in config_dir.iterdir():
            if not dataset_dir.is_dir():
                continue
            for seed_dir in dataset_dir.iterdir():
                if not seed_dir.is_dir():
                    continue
                for fp in seed_dir.glob("*.json"):
                    try:
                        out.append(load_result(fp))
                    except Exception as e:  # noqa: BLE001
                        log.warning("failed to load %s: %s", fp, e)
    return out


def to_dataframe(results: list[RunResult], include_metrics: bool = False) -> pd.DataFrame:
    rows = []
    for r in results:
        row = {
            "config_id": r.config_id,
            "dataset": r.dataset,
            "question_id": r.question_id,
            "seed": r.seed,
            "is_correct": r.is_correct,
            "cost_usd": r.cost_usd,
            "latency_sec": r.latency_sec,
            "input_tokens": r.usage.input_tokens,
            "output_tokens": r.usage.output_tokens,
            "moderator_turns": r.moderator_turns,
            "participant_turns": r.participant_turns,
            "extracted": r.extracted_answer,
            "error": r.error,
        }
        if include_metrics:
            cd = contribution_diversity(r.transcript)
            crr = cross_reference_rate(r.transcript)
            car_val, car_detail = citation_accuracy_rate(r.transcript)
            row.update({
                "cdiv_distinct2": cd.distinct_2,
                "cdiv_distinct3": cd.distinct_3,
                "cdiv_semantic": cd.semantic,
                "cdiv_topics": cd.topics,
                "cdiv_mean": cd.mean,
                "crr": crr,
                "car": car_val if car_val is not None else np.nan,
                "car_total": car_detail["total"],
                "car_verified": car_detail["verified"],
            })
        rows.append(row)
    return pd.DataFrame(rows)


# --------- statistics ---------

def bootstrap_ci(values: list[bool | int | float], n: int = 10_000, alpha: float = 0.05, seed: int = 0) -> tuple[float, float, float]:
    """Return (mean, lo, hi) via non-parametric bootstrap."""
    arr = np.asarray(values, dtype=float)
    if arr.size == 0:
        return 0.0, 0.0, 0.0
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, arr.size, size=(n, arr.size))
    means = arr[idx].mean(axis=1)
    lo = float(np.quantile(means, alpha / 2))
    hi = float(np.quantile(means, 1 - alpha / 2))
    return float(arr.mean()), lo, hi


def paired_bootstrap_test(a: list[bool | int | float], b: list[bool | int | float],
                          n: int = 10_000, seed: int = 0) -> float:
    """Two-sided paired bootstrap p-value for H0: mean(a) == mean(b).

    Lists must be the same length and aligned (same question). Returns p-value.
    """
    arr_a = np.asarray(a, dtype=float)
    arr_b = np.asarray(b, dtype=float)
    assert arr_a.shape == arr_b.shape
    diffs = arr_a - arr_b
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, diffs.size, size=(n, diffs.size))
    mean_diffs = diffs[idx].mean(axis=1)
    obs = diffs.mean()
    # Two-sided tail probability centred on 0.
    p = np.mean(np.abs(mean_diffs - obs) >= np.abs(obs))
    return float(p)


def per_question_accuracy(df: pd.DataFrame) -> pd.DataFrame:
    """Average across seeds -> one row per (config, dataset, question) with fraction correct."""
    return df.groupby(["config_id", "dataset", "question_id"])["is_correct"].mean().reset_index()


def summary_table(df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for (cfg, ds), g in df.groupby(["config_id", "dataset"]):
        mean_acc, lo, hi = bootstrap_ci(g["is_correct"].astype(float).tolist())
        rows.append({
            "config_id": cfg,
            "dataset": ds,
            "n": len(g),
            "accuracy": mean_acc,
            "acc_lo": lo,
            "acc_hi": hi,
            "mean_cost_usd": g["cost_usd"].mean(),
            "mean_latency_sec": g["latency_sec"].mean(),
            "mean_tokens_in": g["input_tokens"].mean(),
            "mean_tokens_out": g["output_tokens"].mean(),
        })
    return pd.DataFrame(rows)


# --------- pairwise significance ---------

def pairwise_significance(df: pd.DataFrame, configs: list[str] | None = None) -> pd.DataFrame:
    configs = configs or CONFIG_IDS
    pq = per_question_accuracy(df)
    rows = []
    for a in configs:
        for b in configs:
            if a >= b:
                continue
            pa = pq[pq.config_id == a].set_index(["dataset", "question_id"])["is_correct"]
            pb = pq[pq.config_id == b].set_index(["dataset", "question_id"])["is_correct"]
            common = pa.index.intersection(pb.index)
            if common.empty:
                continue
            p = paired_bootstrap_test(pa.loc[common].tolist(), pb.loc[common].tolist())
            delta = float(pa.loc[common].mean() - pb.loc[common].mean())
            rows.append({"a": a, "b": b, "n": len(common), "delta": delta, "p": p})
    return pd.DataFrame(rows)


# --------- paper assets ---------

CONFIG_PRETTY = {
    "s1": "S-1 (single)",
    "s_refine": "S-Refine",
    "o_worker": "O-Worker",
    "mad": "MAD",
    "as_free_nomod": "AS-Free (no mod)",
    "as_free_mod": "AS-Free (mod)",
    "as_raisehand": "AS-RaiseHand",
}


def emit_latex_tables(df: pd.DataFrame, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    sm = summary_table(df)
    sm["config_pretty"] = sm["config_id"].map(CONFIG_PRETTY)
    sm = sm.sort_values(["dataset", "config_id"])
    def esc(s: str) -> str:
        return str(s).replace("_", "\\_")
    with open(out_dir / "accuracy.tex", "w") as f:
        f.write("% Auto-generated from agentstorming-bench. Do not edit by hand.\n")
        f.write("\\begin{table}[H]\\centering\\small\n")
        f.write("\\caption{Accuracy per configuration with 95\\% bootstrap CI over seeds, plus mean cost / latency per answer. "
                "$N$ is the number of (question, seed) tuples completed at analysis time.}\n")
        f.write("\\label{tab:accuracy}\n")
        f.write("\\begin{tabular}{llrrcrr}\n\\toprule\n")
        f.write("Dataset & Config & $N$ & Accuracy & 95\\% CI & Cost/ans & Latency \\\\\n\\midrule\n")
        prev_ds = None
        for _, r in sm.iterrows():
            ds_col = esc(r["dataset"]) if r["dataset"] != prev_ds else ""
            prev_ds = r["dataset"]
            f.write(f"{ds_col} & {esc(r['config_pretty'])} & {int(r['n'])} & "
                    f"{r['accuracy']:.3f} & [{r['acc_lo']:.3f}, {r['acc_hi']:.3f}] & "
                    f"\\${r['mean_cost_usd']:.4f} & {r['mean_latency_sec']:.1f}s \\\\\n")
        f.write("\\bottomrule\n\\end{tabular}\n\\end{table}\n")


def emit_diversity_table(df: pd.DataFrame, out_dir: Path) -> None:
    """Emit a diversity/CRR/CAR table."""
    out_dir.mkdir(parents=True, exist_ok=True)

    def esc(s: str) -> str:
        return str(s).replace("_", "\\_")

    has_metrics = "cdiv_distinct2" in df.columns
    with open(out_dir / "diversity.tex", "w") as f:
        f.write("% Auto-generated from agentstorming-bench.\n")
        f.write("\\begin{table}[H]\\centering\\small\n")
        f.write("\\caption{Transcript diversity and cross-reference metrics. "
                "dist$_n$ is the distinct-$n$ ratio; C-Div$_{\\text{sem}}$ is the mean pairwise "
                "mpnet cosine distance between messages from different speakers; "
                "topics is the k-means topic count; CRR is the cross-reference rate; "
                "CAR is the citation-accuracy rate over transcripts with $\\ge 1$ arXiv/DOI. "
                "Dashes indicate the metric is structurally undefined.}\n")
        f.write("\\label{tab:diversity}\n")
        f.write("\\begin{tabular}{lcccccc}\n\\toprule\n")
        f.write("Configuration & dist$_2$ & dist$_3$ & C-Div$_{\\text{sem}}$ & topics & CRR & CAR \\\\\n\\midrule\n")
        if has_metrics:
            by_cfg = df.groupby("config_id")
            for cfg in CONFIG_IDS:
                if cfg not in by_cfg.groups:
                    continue
                g = by_cfg.get_group(cfg)
                pretty = CONFIG_PRETTY.get(cfg, cfg)
                d2 = g["cdiv_distinct2"].mean()
                d3 = g["cdiv_distinct3"].mean()
                sem = g["cdiv_semantic"].mean()
                top = g["cdiv_topics"].mean()
                crr = g["crr"].mean() if "crr" in g else float("nan")
                car_col = g["car"] if "car" in g else None
                if car_col is not None and car_col.notna().any():
                    car = car_col.dropna().mean()
                    car_s = f"{car:.2f}"
                else:
                    car_s = "--"
                crr_s = "--" if cfg in ("s1", "s_refine") else f"{crr:.2f}"
                f.write(f"\\texttt{{{esc(cfg)}}} & {d2:.3f} & {d3:.3f} & "
                        f"{sem:.3f} & {top:.1f} & {crr_s} & {car_s} \\\\\n")
        else:
            for cfg in CONFIG_IDS:
                f.write(f"\\texttt{{{esc(cfg)}}} & -- & -- & -- & -- & -- & -- \\\\\n")
        f.write("\\bottomrule\n\\end{tabular}\n\\end{table}\n")


def emit_significance_table(df: pd.DataFrame, out_dir: Path) -> None:
    """Emit a pairwise-significance LaTeX table (compact)."""
    out_dir.mkdir(parents=True, exist_ok=True)

    def esc(s: str) -> str:
        return str(s).replace("_", "\\_")

    sig = pairwise_significance(df)
    if sig.empty:
        return
    with open(out_dir / "significance.tex", "w") as f:
        f.write("% Auto-generated from agentstorming-bench.\n")
        f.write("\\begin{table}[H]\\centering\\small\n")
        f.write("\\caption{Pairwise paired-bootstrap $p$-values for accuracy differences (two-sided, $B=10{,}000$). "
                "$\\Delta = \\text{acc}(a) - \\text{acc}(b)$ on the common question set. "
                "$\\star$: $p<0.05$; $\\star\\star$: $p<0.01$.}\n")
        f.write("\\label{tab:sig}\n")
        f.write("\\begin{tabular}{llrrr}\n\\toprule\n")
        f.write("$a$ & $b$ & $N$ & $\\Delta$ & $p$ \\\\\n\\midrule\n")
        for _, r in sig.iterrows():
            star = ""
            if r["p"] < 0.01:
                star = "$^{\\star\\star}$"
            elif r["p"] < 0.05:
                star = "$^{\\star}$"
            f.write(f"\\texttt{{{esc(r['a'])}}} & \\texttt{{{esc(r['b'])}}} & {int(r['n'])} & "
                    f"${r['delta']:+.3f}$ & ${r['p']:.3f}${star} \\\\\n")
        f.write("\\bottomrule\n\\end{tabular}\n\\end{table}\n")


def plot_accuracy_cost(df: pd.DataFrame, out_dir: Path) -> None:
    import matplotlib.pyplot as plt
    out_dir.mkdir(parents=True, exist_ok=True)
    sm = summary_table(df)
    for ds, g in sm.groupby("dataset"):
        fig, ax = plt.subplots(figsize=(6, 4.2))
        for _, r in g.iterrows():
            ax.errorbar(r["mean_cost_usd"], r["accuracy"],
                        yerr=[[r["accuracy"] - r["acc_lo"]], [r["acc_hi"] - r["accuracy"]]],
                        fmt="o", capsize=3, label=CONFIG_PRETTY.get(r["config_id"], r["config_id"]))
        ax.set_xscale("log")
        ax.set_xlabel("Mean cost per question (USD)")
        ax.set_ylabel("Accuracy")
        ax.set_title(f"{ds}: accuracy vs cost")
        ax.grid(True, alpha=0.3)
        ax.legend(loc="best", fontsize=8)
        fig.tight_layout()
        fig.savefig(out_dir / f"accuracy-cost-{ds}.pdf")
        plt.close(fig)


def emit_summary_csv(df: pd.DataFrame, out_path: Path) -> None:
    summary_table(df).to_csv(out_path, index=False)


async def score_transcripts_async(results: list[RunResult]) -> list[dict]:
    """Compute the non-trivial metrics (CtC, CAR) on each transcript."""
    out = []
    for r in results:
        ctc, ctc_detail = await convergence_to_concrete(r.final_statement or "")
        car_val, car_detail = citation_accuracy_rate(r.transcript)
        out.append({
            "config_id": r.config_id,
            "dataset": r.dataset,
            "question_id": r.question_id,
            "seed": r.seed,
            "ctc": ctc,
            "ctc_testable": ctc_detail["testable"],
            "ctc_actionable": ctc_detail["actionable"],
            "ctc_traceable": ctc_detail["traceable"],
            "car": car_val if car_val is not None else np.nan,
            "car_total": car_detail["total"],
            "car_verified": car_detail["verified"],
        })
    return out
