# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Dataset interfaces. Each dataset returns a list of `Question` objects.

All loaders use the HuggingFace `datasets` library where possible, caching
raw data under `benchmark/data/`. Loaders are deterministic for a given seed.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from ..types import Question
from ..config import DATA_DIR


_REGISTRY: dict[str, Callable[..., list[Question]]] = {}


def register(name: str):
    def deco(fn):
        _REGISTRY[name] = fn
        return fn
    return deco


def load(name: str, **kwargs) -> list[Question]:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if name not in _REGISTRY:
        raise KeyError(f"unknown dataset {name!r}; known: {list(_REGISTRY)}")
    return _REGISTRY[name](**kwargs)


def available() -> list[str]:
    return sorted(_REGISTRY)


# Triggers registration.
from . import mmlu_pro, gpqa, math500, musr, dabstep, truthfulqa, arc, xdomain  # noqa: E402,F401
