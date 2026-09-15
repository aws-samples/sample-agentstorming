# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Benchmark-wide configuration.

All tunables that affect cost, scope, and reproducibility live here. Env vars
override defaults. See .env.example.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]          # agentstorming/benchmark/
REPO_ROOT = ROOT.parents[1]                         # neural-experiments/
RESULTS_DIR = ROOT / "results"
DATA_DIR = ROOT / "data"
CONFIGS_DIR = ROOT / "configs"


# Bedrock inference profiles (us-east-1). See AWS Bedrock list-inference-profiles.
# Sonnet 4.6 is our primary benchmark model: newer than Sonnet 4.5 used in the trials,
# cheaper than Opus 4.7, widely available as a US inference profile.
PRIMARY_MODEL = os.getenv(
    "AGENTSTORMING_BENCH_MODEL",
    "us.anthropic.claude-sonnet-4-6",
)
JUDGE_MODEL = os.getenv(
    "AGENTSTORMING_BENCH_JUDGE_MODEL",
    "us.anthropic.claude-sonnet-4-6",
)
AWS_REGION = os.getenv("AWS_REGION", "us-east-1")
CONCURRENCY = int(os.getenv("AGENTSTORMING_BENCH_CONCURRENCY", "8"))


# AWS Bedrock Claude 4.x public on-demand prices (USD / million tokens).
# Matches the published Anthropic-on-Bedrock price list (see README). These are
# *only* used for cost accounting in the paper; the actual spend comes from AWS
# billing. Sonnet 4.6 currently prices identically to 4.5.
PRICES_USD_PER_MTOK = {
    "us.anthropic.claude-sonnet-4-6":              {"input": 3.00, "output": 15.00},
    "us.anthropic.claude-sonnet-4-5-20250929-v1:0":{"input": 3.00, "output": 15.00},
    "us.anthropic.claude-haiku-4-5-20251001-v1:0": {"input": 1.00, "output":  5.00},
    "us.anthropic.claude-opus-4-7":                {"input":15.00, "output": 75.00},
    "us.anthropic.claude-opus-4-5-20251101-v1:0":  {"input":15.00, "output": 75.00},
    # Panel configs: use representative model price (Sonnet-4.6) for cost approximation
    "panel-as-v5-adaptive":                        {"input": 3.00, "output": 15.00},
}


# All seven configurations benchmarked in the paper.
CONFIG_IDS = [
    "s1",
    "s_refine",
    "o_worker",
    "mad",
    "as_free_nomod",
    "as_free_mod",
    "as_raisehand",
]


@dataclass
class Tier:
    """A named run plan: which configs, datasets, question counts, and seeds."""
    name: str
    configs: list[str]
    datasets: list[str]
    n_questions: int        # per dataset, stratified sample
    seeds: list[int]


TIERS: dict[str, Tier] = {
    "smoke":   Tier("smoke",   CONFIG_IDS,                       ["mmlu_pro"],            1, [0]),
    "pilot":   Tier("pilot",   CONFIG_IDS,                       ["mmlu_pro"],           30, [0, 1, 2]),
    "full":    Tier("full",    CONFIG_IDS,                       ["mmlu_pro"],          200, [0, 1, 2]),
    "stretch": Tier("stretch",
                    ["s1", "s_refine", "o_worker", "mad", "as_free_mod", "as_raisehand"],
                    ["gpqa_diamond", "math500"],
                    198, [0, 1, 2]),
    # v3 plan: smoke pass on the heterogeneous-panel design before
    # committing to a full GPQA-D / MuSR / DABStep run.
    "v3_smoke": Tier(
        "v3_smoke",
        ["s1", "mad", "as_raisehand"],
        ["mmlu_pro"],
        3, [42],  # fresh seed not used by prior smoke runs
    ),
    "v3_pilot": Tier(
        "v3_pilot",
        ["s1", "s_refine", "mad", "as_free_mod", "as_raisehand"],
        ["mmlu_pro"],
        20, [0, 1, 2],
    ),
}


@dataclass
class PersonaSet:
    """Specialist personas used by the multi-agent configurations."""
    moderator: str = "project-lead"
    specialists: list[str] = field(default_factory=lambda: [
        "mathematician",
        "deep-learning-scientist",
        "physics-scientist",
        "fourier-transform-scientist",
        "neuron-biologist",
    ])


PERSONAS = PersonaSet()


# Heterogeneous persona-to-model map for the v3 experiments. When set
# (via env or programmatically), each persona uses its own Bedrock
# inference profile; when None, every persona falls back to PRIMARY_MODEL.
# The keys MUST match PersonaSet.specialists + "project-lead".
PERSONA_MODEL_MAP: dict[str, str] | None = None


def use_heterogeneous_panel(panel: dict[str, str]) -> None:
    """Install a per-persona model map. Call before any runner.run().

    panel = {
      "project-lead": "us.anthropic.claude-sonnet-4-5-20250929-v1:0",
      "mathematician": "us.amazon.nova-pro-v1:0",
      "deep-learning-scientist": "us.meta.llama3-3-70b-instruct-v1:0",
      ...
    }
    """
    global PERSONA_MODEL_MAP
    PERSONA_MODEL_MAP = dict(panel)


def model_for_persona(name: str) -> str:
    if PERSONA_MODEL_MAP and name in PERSONA_MODEL_MAP:
        return PERSONA_MODEL_MAP[name]
    return PRIMARY_MODEL


@dataclass
class RunConstants:
    refine_turns: int = 5
    mad_rounds: int = 3
    mad_agents: int = 5
    worker_count: int = 5
    as_max_messages: int = 40
    as_max_duration_sec: int = 180
    default_temperature: float = 0.7
    max_output_tokens: int = 2048
    judge_temperature: float = 0.0
    judge_max_output_tokens: int = 512


CONSTANTS = RunConstants()
