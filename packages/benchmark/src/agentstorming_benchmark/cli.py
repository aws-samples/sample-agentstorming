# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Agent Storming benchmark CLI."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table
from dotenv import load_dotenv

from . import __version__
from .config import RESULTS_DIR, TIERS
from . import runners as _runners
from . import datasets as _datasets
from .orchestrate import plan_tier, pending, run_all, summary as plan_summary


load_dotenv(Path(__file__).resolve().parents[2] / ".env", override=False)


app = typer.Typer(add_completion=False, help="Agent Storming benchmark harness.")
data_app = typer.Typer()
app.add_typer(data_app, name="data", help="Dataset helpers.")
console = Console()


def _setup_logging():
    level = os.getenv("AGENTSTORMING_BENCH_LOG_LEVEL", "INFO").upper()
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


@app.command()
def version():
    """Print version."""
    console.print(f"agentstorming-bench {__version__}")


@app.command()
def plan(tier: str = typer.Option("smoke")):
    """Show the task plan for a tier."""
    tasks = plan_tier(tier)
    s = plan_summary(tasks)
    table = Table(title=f"Plan: tier={tier}")
    for k, v in s.items():
        table.add_row(k, str(v))
    console.print(table)


@app.command()
def run(
    tier: str = typer.Option("smoke"),
    concurrency: int = typer.Option(4, help="Concurrent (config, question) pairs."),
    n_override: Optional[int] = typer.Option(None, help="Force N questions per dataset."),
):
    """Run a tier with checkpointing; safe to re-run to resume."""
    _setup_logging()
    tasks = plan_tier(tier, n_override=n_override)
    todo = pending(tasks)
    console.print(f"[bold]tier={tier}[/bold]  total={len(tasks)}  pending={len(todo)}")
    if not todo:
        console.print("[green]nothing to do[/green]")
        return
    asyncio.run(run_all(todo, concurrency=concurrency))


@data_app.command("download")
def data_download():
    """Force-cache all datasets; fails loudly if anything is unreachable."""
    _setup_logging()
    for name in _datasets.available():
        rows = _datasets.load(name, n=20, seed=0)
        console.print(f"[green]{name}[/green]: {len(rows)} rows cached")


@data_app.command("sample")
def data_sample(
    dataset: str = typer.Argument(...),
    n: int = typer.Option(3),
    seed: int = typer.Option(0),
):
    rows = _datasets.load(dataset, n=n, seed=seed)
    for q in rows:
        console.print(f"[cyan]{q.id}[/cyan] subject={q.subject}  key={q.answer_key}")
        console.print(q.question[:400])
        console.print("---")


@app.command()
def analyse(
    results_dir: Path = typer.Option(RESULTS_DIR),
    out: Path = typer.Option(RESULTS_DIR / "aggregated"),
    tables: Path = typer.Option(RESULTS_DIR / "paper-ready" / "tables"),
    plots: Path = typer.Option(RESULTS_DIR / "paper-ready" / "plots"),
    include_metrics: bool = typer.Option(True, help="Compute CRR/CAR/CDiv (slow)."),
):
    """Aggregate results, compute stats, render tables and plots."""
    _setup_logging()
    from .analysis import collect_results, to_dataframe, summary_table, emit_latex_tables, plot_accuracy_cost, pairwise_significance, emit_summary_csv, emit_diversity_table, emit_significance_table

    results = collect_results(results_dir)
    if not results:
        console.print("[red]no results found[/red]")
        raise typer.Exit(code=1)
    out.mkdir(parents=True, exist_ok=True)
    df = to_dataframe(results, include_metrics=include_metrics)
    df.to_csv(out / "per_run.csv", index=False)
    emit_summary_csv(df, out / "summary.csv")
    emit_latex_tables(df, tables)
    emit_diversity_table(df, tables)
    emit_significance_table(df, tables)
    plot_accuracy_cost(df, plots)
    sig = pairwise_significance(df)
    sig.to_csv(out / "pairwise_significance.csv", index=False)
    console.print(f"[green]wrote[/green] {out} and {tables} and {plots}")


@app.command()
def emit_paper_assets(
    out: Path = typer.Option(Path("./paper-artifacts")),
):
    """Copy the latest tables and plots to `out` for inclusion in a LaTeX source tree."""
    import shutil
    tables_src = RESULTS_DIR / "paper-ready" / "tables"
    plots_src = RESULTS_DIR / "paper-ready" / "plots"
    out.mkdir(parents=True, exist_ok=True)
    (out / "tables").mkdir(exist_ok=True)
    (out / "figures").mkdir(exist_ok=True)
    for fp in tables_src.glob("*.tex"):
        shutil.copy2(fp, out / "tables" / fp.name)
    for fp in plots_src.glob("*.pdf"):
        shutil.copy2(fp, out / "figures" / fp.name)
    console.print(f"[green]emitted paper assets into {out}[/green]")


@app.command()
def info():
    """Show configuration information."""
    t = Table(title="Agent Storming benchmark configuration")
    t.add_column("key"); t.add_column("value")
    from .config import PRIMARY_MODEL, JUDGE_MODEL, AWS_REGION, CONCURRENCY
    for k, v in [("primary_model", PRIMARY_MODEL), ("judge_model", JUDGE_MODEL),
                 ("region", AWS_REGION), ("concurrency", str(CONCURRENCY)),
                 ("tiers", ", ".join(TIERS)), ("configs", ", ".join(_runners.available())),
                 ("datasets", ", ".join(_datasets.available()))]:
        t.add_row(k, str(v))
    console.print(t)


if __name__ == "__main__":
    app()
