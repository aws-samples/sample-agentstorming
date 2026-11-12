# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Pieces shared by every signed-request surface: owner and candidate.

Both verify a client timestamp and a signature over a canonical form of the
request, and both answered malformed input with a 500 until these checks were
made common: a timestamp with no UTC offset raised TypeError when compared
with an aware clock, and a body with no JCS form (NaN, 1e400, a lone
surrogate) raised inside the canonicaliser.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import HTTPException

from .jcs import canonicalise


def verify_ts(ts_str: Any, max_skew_seconds: int) -> None:
    """400 unless ``ts_str`` is ISO-8601 with a UTC offset, within the skew."""
    if not isinstance(ts_str, str):
        raise HTTPException(status_code=400, detail={"code": "org.agentstorming.err.ts_invalid"})
    try:
        ts = datetime.fromisoformat(ts_str)
    except ValueError:
        raise HTTPException(status_code=400, detail={"code": "org.agentstorming.err.ts_invalid"})
    if ts.utcoffset() is None:
        raise HTTPException(status_code=400, detail={"code": "org.agentstorming.err.ts_invalid"})
    if abs((datetime.now(UTC) - ts).total_seconds()) > max_skew_seconds:
        raise HTTPException(status_code=400, detail={"code": "org.agentstorming.err.ts_skew"})


def canonical_bytes(payload: Any) -> bytes:
    """The JCS bytes of ``payload``, or 400 if it has none (so nothing signed it)."""
    try:
        return canonicalise(payload)
    except (ValueError, TypeError, RecursionError):
        raise HTTPException(status_code=400, detail={"code": "org.agentstorming.err.bad_body"})
