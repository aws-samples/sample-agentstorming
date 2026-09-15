# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Citation Accuracy Rate (CAR).

Scans the transcript for arXiv IDs (2-4 digits dot 5 digits, or legacy 7 digit)
and DOI patterns. For each, attempts to verify:
  - arXiv: HTTP GET `http://export.arxiv.org/api/query?id_list=<id>` returns 1 entry.
  - DOI: HTTP GET `https://api.crossref.org/works/<doi>` returns 200.

CAR = verified_citations / extracted_citations. A transcript with no
extracted citations returns None (excluded from averages).

The verification endpoints are public and rate-limited; we cache results
on disk under `results/citation-cache.json` so re-runs are cheap.
"""

from __future__ import annotations

import logging
import os

import json
import re
from pathlib import Path
from typing import Iterable, Sequence

import httpx

from ..types import Message
from ..config import ROOT


CACHE_PATH = ROOT / "results" / "citation-cache.json"
ARXIV_RE = re.compile(r"\b(\d{4}\.\d{4,5})(v\d+)?\b|\barXiv:(\d{4}\.\d{4,5})(v\d+)?\b", re.I)
DOI_RE = re.compile(r"\b10\.\d{4,9}/[-._;()/:A-Za-z0-9]+\b")


log = logging.getLogger("agentstorming.bench.car")


def _load_cache() -> dict[str, bool]:
    if CACHE_PATH.exists():
        try:
            return json.loads(CACHE_PATH.read_text())
        except Exception:
            return {}
    return {}


def _save_cache(cache: dict[str, bool]) -> None:
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(json.dumps(cache, indent=2, sort_keys=True))


def extract_citations(transcript: Sequence[Message]) -> list[tuple[str, str]]:
    """Returns list of (kind, id) where kind in {'arxiv', 'doi'}."""
    cites: list[tuple[str, str]] = []
    for m in transcript:
        if m.role != "assistant":
            continue
        for match in ARXIV_RE.finditer(m.content):
            arxid = match.group(1) or match.group(3)
            if arxid:
                cites.append(("arxiv", arxid))
        for match in DOI_RE.finditer(m.content):
            cites.append(("doi", match.group(0)))
    # Deduplicate while preserving order.
    seen = set()
    out = []
    for c in cites:
        if c not in seen:
            seen.add(c)
            out.append(c)
    return out


def _verify_arxiv(arxid: str, client: httpx.Client) -> bool:
    try:
        r = client.get(
            "http://export.arxiv.org/api/query",
            params={"id_list": arxid},
            timeout=10.0,
        )
        return r.status_code == 200 and "<entry>" in r.text
    except Exception:
        return False


def _verify_doi(doi: str, client: httpx.Client) -> bool:
    try:
        r = client.get(f"https://api.crossref.org/works/{doi}", timeout=10.0)
        return r.status_code == 200
    except Exception:
        return False


def citation_accuracy_rate(transcript: Sequence[Message]) -> tuple[float | None, dict[str, int]]:
    cites = extract_citations(transcript)
    if not cites:
        return None, {"total": 0, "verified": 0}
    cache = _load_cache()
    verified = 0
    # arXiv and CrossRef ask for a contact address so they can reach an
    # operator whose script is misbehaving, and CrossRef gives politely
    # identified callers a faster rate-limit pool. Read it from the
    # environment: a real address baked into published code is disclosed to
    # every third-party API operator and every network observer, and it
    # would identify one person as the contact for everybody else's runs.
    contact = os.environ.get("AGENTSTORMING_CONTACT_EMAIL", "").strip()
    ua = "agentstorming-benchmark/0.1"
    if contact:
        ua = f"{ua} (mailto:{contact})"
    else:
        log.info(
            "AGENTSTORMING_CONTACT_EMAIL is unset — calling arXiv/CrossRef "
            "anonymously. Set it to your own address for the politely-"
            "identified rate-limit pool."
        )
    with httpx.Client(headers={"User-Agent": ua}) as client:
        for kind, ident in cites:
            key = f"{kind}:{ident}"
            if key in cache:
                if cache[key]:
                    verified += 1
                continue
            ok = _verify_arxiv(ident, client) if kind == "arxiv" else _verify_doi(ident, client)
            cache[key] = ok
            if ok:
                verified += 1
    _save_cache(cache)
    return verified / len(cites), {"total": len(cites), "verified": verified}
