# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Explicit Hugging Face dataset revisions.

Calling ``load_dataset("truthful_qa", …)`` with no ``revision`` resolves to
whatever the dataset's default branch points at *today*. Two consequences,
one security and one scientific:

- The code silently executes whatever the repository currently contains. A
  compromised or simply re-uploaded dataset changes what you run without any
  diff on your side.
- A benchmark number computed against an unpinned dataset is not
  reproducible. This project has already retracted three headline numbers
  (see the benchmark README); "the dataset moved" would be an
  embarrassing fourth.

So every loader passes an explicit revision from the table below.

**The default of ``"main"`` is a floor, not a finish line.** A branch name
pins the *name*, not the content. Any run whose numbers you intend to
publish MUST set real commit SHAs — either by editing this table or via the
``AGENTSTORMING_HF_REVISIONS`` environment variable, which takes a JSON
object of ``{"repo_id": "sha"}``:

    AGENTSTORMING_HF_REVISIONS='{"truthful_qa": "0a4f5f9…"}'

``resolve()`` logs the revision it used for every load, so the run log
records what was actually read even when the table says ``main``.
"""

from __future__ import annotations

import json
import logging
import os

log = logging.getLogger("agentstorming.bench.datasets")

_ENV_VAR = "AGENTSTORMING_HF_REVISIONS"

#: repo_id → revision (branch, tag, or — preferably — a commit SHA).
#:
#: Every 40-hex value below carries `pragma: allowlist secret`. They are public
#: git commit SHAs on the Hugging Face Hub — `GET /api/datasets/<repo>/revision/
#: <sha>` returns 200 unauthenticated — and they are load-bearing: they ARE the
#: pins, so unlike a fixture value they cannot be changed to something a
#: high-entropy detector ignores. The marker is inline rather than in
#: .secrets.baseline because the baseline is not published, so anything that
#: scans the exported tree would otherwise re-report all of them with no way to
#: see they were reviewed.
#: Resolved from the Hub API on 2026-09-04. `main` remained on several entries
#: until an equivalence run tried to publish a number and the warning below
#: fired, which is what the warning is for.
DATASET_REVISIONS: dict[str, str] = {
    "ai2_arc": "210d026faf9955653af8916fad021475a3f00453",  # pragma: allowlist secret
    "allenai/ai2_arc": "210d026faf9955653af8916fad021475a3f00453",  # pragma: allowlist secret
    "adyen/DABstep": "3e7c1e8d87d9381f6e89f387759dcf3e1bd140dc",  # pragma: allowlist secret
    "Idavidrein/gpqa": "633f5ee89ab8ad4522a9f850766b73f62147ffdd",  # pragma: allowlist secret
    "HuggingFaceH4/MATH-500": "6e4ed1a2a79af7d8630a6b768ec859cb5af4d3be",  # pragma: allowlist secret
    "TAUR-Lab/MuSR": "7c365b439a222150f317764d4f16ae6c96d7d94a",  # pragma: allowlist secret
    "TIGER-Lab/MMLU-Pro": "b189ec765aa7ed75c8acfea42df31fdae71f97be",  # pragma: allowlist secret
    "truthful_qa": "741b8276f2d1982aa3d5b832d3ee81ed3b896490",  # pragma: allowlist secret
    "truthfulqa/truthful_qa": "741b8276f2d1982aa3d5b832d3ee81ed3b896490",  # pragma: allowlist secret
    # Sample-project corpora. Left on `main` deliberately: they belong to the
    # excluded neural-experiments sample and no published number depends on
    # them, so the warning firing for these is correct rather than noise.
    "roneneldan/TinyStories": "main",
    "wikitext": "main",
}

DEFAULT_REVISION = "main"


def _overrides() -> dict[str, str]:
    raw = os.environ.get(_ENV_VAR)
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(f"{_ENV_VAR} is not valid JSON: {e}") from e
    if not isinstance(parsed, dict):
        raise ValueError(f"{_ENV_VAR} must be a JSON object of repo_id → revision")
    return {str(k): str(v) for k, v in parsed.items()}


def resolve(repo_id: str) -> str:
    """The revision to load ``repo_id`` at, honouring env overrides."""
    revision = _overrides().get(repo_id) or DATASET_REVISIONS.get(repo_id, DEFAULT_REVISION)
    if revision == DEFAULT_REVISION:
        log.warning(
            "dataset %s is pinned only to %r — a branch name, not a commit. "
            "Set %s before publishing any number from this run.",
            repo_id, revision, _ENV_VAR,
        )
    else:
        log.info("dataset %s pinned at %s", repo_id, revision)
    return revision
