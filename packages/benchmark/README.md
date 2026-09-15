# Agent Storming benchmark

End-to-end evaluation of Agent Storming against single-agent, orchestrator-worker,
and multi-agent-debate baselines.

> **Read before trusting any number here.** This harness was driven by a
> long-running autonomous experiment whose headline results changed
> substantially over time — several early "wins" turned out to be
> grading artifacts and were retracted. The honest current verdict (AS
> did **not** meet its success criterion; it is a narrow-niche method,
> not a general winner) and the full history are in
> this README, below.
> The configuration table below is the *original* 7-config design; the
> experiment ran ~60 AS variants and ~40 baselines across 22 benchmarks.
> The `dev/paper/` drafts predate the retractions — do not cite them
> as-is.

## What this measures

Seven configurations, all using `us.anthropic.claude-sonnet-4-6` via AWS Bedrock:

| Key | Configuration |
|---|---|
| `s1` | Single agent, 1 turn (chain-of-thought). Baseline. |
| `s_refine` | Single agent, 5 turns of self-refine (Madaan et al. 2023). |
| `o_worker` | Orchestrator + 5 specialist workers (parallel); orchestrator synthesises. |
| `mad` | Multi-agent debate (Du et al. 2023); 5 agents, 3 rounds, majority vote. |
| `as_free_nomod` | Agent Storming, 5 specialists, free-speak, no moderator; majority vote. |
| `as_free_mod` | Agent Storming, 5 specialists + moderator; moderator synthesises. |
| `as_raisehand` | Agent Storming, 5 specialists + moderator, raise-hand-required. |

## Datasets

- **MMLU-Pro** (Wang et al. 2024) — primary, 200-question stratified subsample across 14 subjects.
- **GPQA Diamond** (Rein et al. 2023) — optional stretch, 198 graduate-level questions (requires `HF_TOKEN` for the gated dataset).
- **MATH-500** (Lightman et al. 2023) — optional stretch, 500 competition maths problems.

## Metrics

- **Accuracy** — fraction correct. 95 % CI via paired bootstrap (B = 10 000).
- **Cost** — USD Bedrock spend per answer using published list prices for Claude Sonnet 4.6.
- **Latency** — wall-clock seconds per answer.
- **C-Div** — lexical distinct-n + semantic pairwise embedding distance + k-means topic count.
- **CRR** — cross-reference rate (regex-based).
- **CtC** — convergence-to-concrete (LLM-judge 0–3 rubric on the final statement).
- **CAR** — citation-accuracy rate (extract arXiv IDs / DOIs, verify against arXiv / CrossRef).

## Reproduce the paper's numbers

### Prerequisites

- AWS account with Bedrock Claude Sonnet 4.6 inference-profile access in `us-east-1`.
- `~/.aws/credentials` or env-var credentials.
- Python ≥ 3.10.
- `tectonic` (for PDF: `brew install tectonic`) and `pandoc` (for DOCX: `brew install pandoc`).
- Optional: `HF_TOKEN` env var for GPQA Diamond.

### One command

```bash
cd packages/benchmark
make all
```

`make all` runs the whole pipeline in order: create venv → install deps → download datasets → smoke test (1 question) → pilot (630 runs) → full (4{,}200 runs) → compute metrics → rebuild LaTeX tables and plots → rebuild the NeurIPS PDF and DOCX.

### Or stage by stage

```bash
make setup          # venv + deps (one-time)
make data           # cache HF datasets locally
make smoke          # 1q x 7 configs ~$1
make pilot          # 30q x 7 configs x 3 seeds ~$35
make full           # 200q x 7 configs x 3 seeds ~$200
make analyse        # compute metrics, emit CSV/LaTeX/PDF tables + plots
make paper          # rebuild dev/paper/*.pdf and *.docx from current tables
make lit-review     # re-harvest + curate the 5,776-paper bibliography
```

### Resume a crashed run

The orchestrator writes one JSON file per (config, dataset, seed, question) and skips existing files, so simply rerun the tier target; only the missing tasks execute. SSL errors and Bedrock throttling are transparently retried (see `src/agentstorming_benchmark/models.py`).

## Outputs

- `results/runs/<config>/<dataset>/seed<N>/<qid>.json` — per-run artefacts (~4,200).
- `results/aggregated/per_run.csv` — one row per run (+ metrics if computed).
- `results/aggregated/summary.csv` — per-config summary.
- `results/aggregated/pairwise_significance.csv` — paired bootstrap p-values.
- `results/paper-ready/tables/*.tex` — LaTeX tables (accuracy, diversity, significance).
- `results/paper-ready/plots/*.pdf` — accuracy-vs-cost Pareto plot.
- `results/logs/` — tqdm progress logs for background runs.

`make paper-tables` copies the tables and plots into `dev/paper/tables/` and `dev/paper/figures/`, where the LaTeX source `\input`s them.

## Concurrency and cost control

The harness uses asyncio with a configurable concurrency cap (default 8 concurrent Bedrock Converse calls; `AGENTSTORMING_BENCH_CONCURRENCY` env var overrides). Tenacity-based retries with exponential backoff handle throttle, SSL, and ServiceUnavailable errors. Every run is checkpointed; interruption costs zero.

## Known issues

- MMLU-Pro HuggingFace download warns about unauthenticated access when `HF_TOKEN` is unset. The download still works; setting `HF_TOKEN` speeds it up.
- `make full` takes ~5-10 hours on 8-way concurrency. Reduce the seed count in `src/agentstorming_benchmark/config.py` (`TIERS["full"].seeds`) for a faster but less statistically powerful run.
- Metrics computation (`analyse --include-metrics`) loads a sentence-transformers model; first run downloads ~420 MB.

## Citing

If you use this harness, cite the Agent Storming paper.
