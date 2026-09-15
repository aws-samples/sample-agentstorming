# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Contribution-Diversity (C-Div).

Three sub-scores; we report the mean in paper tables and all three for
supplementary detail.

- distinct_n: distinct-n ratio across concatenated messages (n=2, n=3).
- semantic: mean pairwise cosine distance between message embeddings
  *from different speakers* (mpnet).
- topics: number of distinct topic clusters via lightweight HDBSCAN over
  normalized embeddings. We do NOT require bertopic to be installed; if
  unavailable, we fall back to silhouette-based k-means cluster counting.

The design target is that a single agent talking to itself scores low
across all three, orchestrator-worker scores moderate on semantic but low
on lexical (because the orchestrator rewrites), and a genuine peer
discussion scores high on all three.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Sequence
import math
import re

import numpy as np

from ..types import Message


_WS = re.compile(r"\s+")


def _tokens(text: str) -> list[str]:
    text = _WS.sub(" ", text.lower())
    return re.findall(r"[a-z][a-z0-9\-]+", text)


def distinct_n(messages: Sequence[Message], n: int = 2) -> float:
    grams: list[tuple[str, ...]] = []
    for m in messages:
        toks = _tokens(m.content)
        grams.extend(tuple(toks[i:i+n]) for i in range(len(toks) - n + 1))
    if not grams:
        return 0.0
    return len(set(grams)) / len(grams)


def _embed(texts: list[str]) -> np.ndarray | None:
    """Return L2-normalized embeddings for a list of texts.

    Uses sentence-transformers `all-mpnet-base-v2`. Cached per-process. If
    sentence-transformers isn't installed or can't load, returns None and
    callers should skip the semantic score gracefully.
    """
    try:
        from sentence_transformers import SentenceTransformer  # type: ignore
    except Exception:  # noqa: BLE001
        return None
    global _MODEL  # noqa: PLW0603
    try:
        _MODEL
    except NameError:
        _MODEL = SentenceTransformer("sentence-transformers/all-mpnet-base-v2")
    vecs = _MODEL.encode(texts, show_progress_bar=False, normalize_embeddings=True)
    return np.asarray(vecs)


def semantic_pairwise(messages: Sequence[Message]) -> float:
    msgs = [m for m in messages if m.role == "assistant" and len(m.content) > 40]
    if len(msgs) < 2:
        return 0.0
    vecs = _embed([m.content for m in msgs])
    if vecs is None:
        return 0.0
    speakers = [m.speaker for m in msgs]
    dists = []
    for i in range(len(msgs)):
        for j in range(i + 1, len(msgs)):
            if speakers[i] == speakers[j]:
                continue
            cos = float(np.dot(vecs[i], vecs[j]))
            dists.append(1.0 - cos)
    return float(np.mean(dists)) if dists else 0.0


def topic_count(messages: Sequence[Message]) -> int:
    msgs = [m for m in messages if m.role == "assistant" and len(m.content) > 40]
    if len(msgs) < 2:
        return len(msgs)
    vecs = _embed([m.content for m in msgs])
    if vecs is None:
        return 1
    try:
        from sklearn.cluster import KMeans
        # Heuristic: try k from 1..min(5, n-1), pick the smallest k where
        # mean intra-cluster distance is < threshold (0.35 on normalized
        # mpnet usually marks distinct topics).
        best = 1
        for k in range(1, min(6, len(msgs))):
            km = KMeans(n_clusters=k, n_init=5, random_state=0).fit(vecs)
            labels = km.labels_
            intra = []
            for c in range(k):
                members = vecs[labels == c]
                if len(members) < 2:
                    continue
                centroid = members.mean(axis=0)
                intra.append(float(np.mean([1 - float(np.dot(v, centroid) / (np.linalg.norm(centroid) + 1e-9))
                                            for v in members])))
            if not intra or max(intra) < 0.35:
                best = k
        return best
    except Exception:  # noqa: BLE001
        return 1


@dataclass
class CDiv:
    distinct_2: float
    distinct_3: float
    semantic: float
    topics: int

    @property
    def mean(self) -> float:
        # We rescale topics via log to put them on a comparable [0,1]ish scale.
        return float(np.mean([self.distinct_2, self.distinct_3, self.semantic, min(1.0, self.topics / 5)]))


def contribution_diversity(transcript: Sequence[Message]) -> CDiv:
    msgs = [m for m in transcript if m.role == "assistant"]
    return CDiv(
        distinct_2=distinct_n(msgs, 2),
        distinct_3=distinct_n(msgs, 3),
        semantic=semantic_pairwise(msgs),
        topics=topic_count(msgs),
    )
